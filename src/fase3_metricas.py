"""Fase 3 — Construcción de métricas y análisis (2.0 pts).

A partir de la matriz O-D (Fase 2) y la población de cada centro poblado:

  * tiempo mínimo de acceso por CCPP (al establecimiento resolutivo más cercano),
    perfiles driving y walking
  * bandas de cobertura ponderadas por población (<=30 / <=60 / <=120 / >120 min)
  * desigualdad: coeficiente de Gini del tiempo de acceso (ponderado)
  * contraste urbano vs. rural y por departamento
  * cross-analysis: acceso × altitud (terciles de msnm)  [dimensión secundaria]
  * agregados a nivel distrito para el choropleth de la Fase 4

Si aún no hay población (descarga pendiente), usa peso uniforme y lo advierte.

Salida: data/outputs/metrics_ccpp.parquet, metrics_distrito.parquet, summary.json
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from src.config import CFG, get_path
from src.utils import get_logger

log = get_logger("fase3")

M = CFG["metrics"]
BANDS = M["coverage_bands_min"]
GOLDEN = M["golden_hour_min"]
PRIMARY = "driving" if "driving" in CFG["routing"]["profiles"] else CFG["routing"]["profiles"][0]

_DISTRICTS_RAW = "peru_distritos.geojson"
_DISTRICTS_URL = ("https://raw.githubusercontent.com/juaneladio/peru-geojson/"
                  "master/peru_distrital_simple.geojson")
_UBIGEO_CSV = "ubigeo_distrito.csv"
_UBIGEO_URL = ("https://raw.githubusercontent.com/jmcastagnetto/"
               "ubigeo-peru-aumentado/main/ubigeo_distrito.csv")

# Peso relativo por categoría de centro poblado, para repartir población
# distrital cuando el padrón no la trae a nivel CCPP (caso Tumbes).
_CAT_WEIGHT = {"CIUDAD": 120, "PUEBLO": 25, "VILLA": 40, "CASERIO": 6,
               "ANEXO": 4, "UNIDAD AGROPECUARIA": 1, "OTROS": 2}
_CAT_DEFAULT = 4


def load_district_context() -> pd.DataFrame:
    """Contexto distrital: población estimada (densidad 2020 × superficie),
    pobreza e IDH. Fuente: repo ubigeo-peru-aumentado (INEI/PNUD)."""
    path = get_path("raw") / _UBIGEO_CSV
    if not path.exists():
        import requests
        log.info("Descargando contexto distrital: %s", _UBIGEO_URL)
        path.write_bytes(requests.get(_UBIGEO_URL, timeout=60).content)
    d = pd.read_csv(path, dtype={"inei": str})
    d = d.rename(columns={"inei": "ubigeo_distrito"})
    d["ubigeo_distrito"] = d["ubigeo_distrito"].str.zfill(6)
    d["poblacion_est"] = (pd.to_numeric(d["pob_densidad_2020"], errors="coerce")
                          * pd.to_numeric(d["superficie"], errors="coerce"))
    return d[["ubigeo_distrito", "poblacion_est", "pct_pobreza_total",
             "pct_pobreza_extrema", "idh_2019"]]


def load_districts():
    """Polígonos distritales recortados a los 3 departamentos (EPSG:4326).
    Descarga el GeoJSON nacional a data/raw/ la primera vez (fuente: repo
    juaneladio/peru-geojson, límites INEI).
    Devuelve GeoDataFrame con ['ubigeo_distrito','distrito','provincia','departamento','geometry']."""
    import geopandas as gpd

    from src.config import department_ubigeos

    path = get_path("raw") / _DISTRICTS_RAW
    if not path.exists():
        import requests
        log.info("Descargando límites distritales: %s", _DISTRICTS_URL)
        path.write_bytes(requests.get(_DISTRICTS_URL, timeout=60).content)

    g = gpd.read_file(path)
    g = g.rename(columns={"IDDIST": "ubigeo_distrito", "NOMBDIST": "distrito",
                          "NOMBPROV": "provincia", "NOMBDEP": "departamento"})
    g["ubigeo_distrito"] = g["ubigeo_distrito"].astype(str).str.zfill(6)
    keep = set(department_ubigeos().values())
    g = g.loc[g["ubigeo_distrito"].str[:2].isin(keep)]
    return g[["ubigeo_distrito", "distrito", "provincia", "departamento", "geometry"]].reset_index(drop=True)


# --------------------------------------------------------------------------- #
def gini(values, weights=None) -> float:
    """Gini ponderado (0 = igualdad). NaN si no hay datos válidos."""
    v = np.asarray(values, dtype=float)
    w = np.ones_like(v) if weights is None else np.asarray(weights, dtype=float)
    m = np.isfinite(v) & np.isfinite(w) & (w > 0)
    v, w = v[m], w[m]
    if v.size == 0 or v.sum() == 0:
        return float("nan")
    order = np.argsort(v)
    v, w = v[order], w[order]
    cw = np.cumsum(w)
    cvw = np.cumsum(v * w)
    return float(1 - np.sum((cvw[1:] + cvw[:-1]) * np.diff(cw)) / (cvw[-1] * cw[-1]))


def wmean(series: pd.Series, weights: pd.Series) -> float:
    m = series.notna() & weights.notna() & (weights > 0)
    if not m.any():
        return float(series.mean())
    return float(np.average(series[m], weights=weights[m]))


def coverage_bands(t: pd.Series, w: pd.Series) -> dict:
    tot = w[w > 0].sum()
    out, prev = {}, 0
    for b in BANDS:
        out[f"<= {b} min"] = round(float(w[t <= b].sum() / tot) * 100, 2)
        prev = b
    out[f"> {prev} min"] = round(float(w[t > prev].sum() / tot) * 100, 2)
    return out


# --------------------------------------------------------------------------- #
def _min_access(matrix: pd.DataFrame) -> pd.DataFrame:
    """CCPP × perfil -> minutos al establecimiento más cercano + su id + fuente."""
    idx = matrix.groupby(["ccpp_id", "profile"])["seconds"].idxmin()
    best = matrix.loc[idx, ["ccpp_id", "profile", "minutes", "facility_id", "source"]]
    wide = best.pivot(index="ccpp_id", columns="profile", values="minutes")
    wide.columns = [f"min_{c}" for c in wide.columns]
    extra = (
        best[best["profile"] == PRIMARY]
        .set_index("ccpp_id")[["facility_id", "source"]]
        .rename(columns={"facility_id": "estab_cercano", "source": "fuente_ruta"})
    )
    return wide.join(extra).reset_index()


def build() -> None:
    proc, out = get_path("processed"), get_path("outputs")
    matrix = pd.read_parquet(proc / "od_matrix.parquet")
    dem = pd.read_parquet(proc / "demand_validated.parquet")
    fac = pd.read_parquet(proc / "facilities_validated.parquet")

    dem = dem.loc[dem["apto_demanda"]].copy()
    dem["ccpp_id"] = dem["codigo"].astype(str)

    acc = _min_access(matrix)
    df = dem.merge(acc, on="ccpp_id", how="left")
    df["ubigeo_distrito"] = df["ubigeo"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(6)

    # --- contexto distrital (población estimada, pobreza, IDH) ---
    ctx = load_district_context()
    df = df.merge(ctx, on="ubigeo_distrito", how="left")

    tcol = f"min_{PRIMARY}"
    df["poblacion"] = pd.to_numeric(df.get("poblacion"), errors="coerce")

    # --- imputación de población para CCPP sin dato (p. ej. Tumbes) ---
    need = df["poblacion"].isna()
    if need.any():
        cat = df.get("categoria", pd.Series(index=df.index, dtype=object)).fillna("").str.upper()
        wcat = cat.map(_CAT_WEIGHT).fillna(_CAT_DEFAULT)
        # población distrital aún no explicada por CCPP con dato
        known = df.loc[~need].groupby("ubigeo_distrito")["poblacion"].sum()
        for dist_id, g in df.loc[need].groupby("ubigeo_distrito"):
            total = df["poblacion_est"].loc[g.index].iloc[0]
            if not np.isfinite(total):
                continue
            resto = max(total - float(known.get(dist_id, 0.0)), 0.0)
            share = wcat.loc[g.index]
            df.loc[g.index, "poblacion"] = resto * (share / share.sum())
        n_imp = int(need.sum())
        log.warning("Población imputada en %d CCPP (%.0f hab.) por reparto distrital "
                    "ponderado por categoría.", n_imp, df.loc[need, "poblacion"].sum())
    df["poblacion_imputada"] = need

    pop_ok = df["poblacion"].notna().any()
    if not pop_ok:
        log.warning("Sin población: métricas con PESO UNIFORME.")
        df["poblacion"] = 1.0
    else:
        df["poblacion"] = df["poblacion"].fillna(0.0)
    w = df["poblacion"]

    # --- altitud: terciles (dimensión secundaria) ---
    if "altitud" in df.columns and df["altitud"].notna().any():
        df["altitud_tercil"] = pd.qcut(df["altitud"], 3,
                                       labels=["bajo", "medio", "alto"], duplicates="drop")
    else:
        df["altitud_tercil"] = pd.NA

    dep_col = "departamento" if "departamento" in df.columns else "dep"

    def _pct_out(g):
        gw = g["poblacion"]
        return round(float(gw[g[tcol] > GOLDEN].sum() / gw.sum()) * 100, 2) if gw.sum() else float("nan")

    # cuartiles de pobreza distrital (dimensión secundaria)
    if df["pct_pobreza_total"].notna().any():
        df["pobreza_cuartil"] = pd.qcut(df["pct_pobreza_total"], 4,
                                        labels=["Q1 (menos pobre)", "Q2", "Q3", "Q4 (más pobre)"],
                                        duplicates="drop")
    else:
        df["pobreza_cuartil"] = pd.NA

    summary = {
        "perfil_primario": PRIMARY,
        "poblacion_ponderada": bool(pop_ok),
        "n_ccpp": int(len(df)),
        "n_establecimientos_resolutivos": int(fac["apto_oferta"].sum()),
        "poblacion_total": float(w.sum()) if pop_ok else None,
        "acceso_min_medio_ponderado": round(wmean(df[tcol], w), 2),
        "acceso_min_mediana": round(float(df[tcol].median()), 2),
        "pct_poblacion_fuera_hora_dorada": round(
            float(w[df[tcol] > GOLDEN].sum() / w.sum()) * 100, 2),
        "gini_acceso": round(gini(df[tcol], w), 4),
        "cobertura_bandas_pct": coverage_bands(df[tcol], w),
        "por_departamento": {
            str(k): {
                "n_ccpp": int(len(g)),
                "acceso_min_medio": round(wmean(g[tcol], g["poblacion"]), 2),
                "pct_fuera_hora_dorada": _pct_out(g),
            }
            for k, g in df.groupby(dep_col)
        },
        "urbano_rural": {
            str(k): round(wmean(g[tcol], g["poblacion"]), 2)
            for k, g in df.groupby("ambito_uro")
        } if "ambito_uro" in df.columns else {},
        "acceso_por_tercil_altitud": {
            str(k): round(wmean(g[tcol], g["poblacion"]), 2)
            for k, g in df.groupby("altitud_tercil", observed=True)
        },
        "acceso_por_cuartil_pobreza": {
            str(k): round(wmean(g[tcol], g["poblacion"]), 2)
            for k, g in df.groupby("pobreza_cuartil", observed=True)
        },
        "correlacion_acceso_pobreza": round(
            float(df[[tcol, "pct_pobreza_total"]].dropna().corr().iloc[0, 1]), 3),
        "correlacion_acceso_idh": round(
            float(df[[tcol, "idh_2019"]].dropna().corr().iloc[0, 1]), 3),
        "poblacion_imputada_pct": round(float(df["poblacion_imputada"].mean()) * 100, 1),
    }
    if {"min_driving", "min_walking"} <= set(df.columns):
        summary["driving_vs_walking_min_medio"] = {
            "driving": round(wmean(df["min_driving"], w), 2),
            "walking": round(wmean(df["min_walking"], w), 2),
        }

    # --- agregado distrital ---
    def _agg(g: pd.DataFrame) -> pd.Series:
        gw = g["poblacion"]
        return pd.Series({
            dep_col: g[dep_col].iloc[0],
            "n_ccpp": len(g),
            "poblacion": round(float(gw.sum()), 0),
            "acceso_min_ponderado": round(wmean(g[tcol], gw), 2),
            "pct_fuera_hora_dorada": _pct_out(g),
            "gini_acceso": round(gini(g[tcol], gw), 4),
            "pct_pobreza_total": round(float(g["pct_pobreza_total"].iloc[0]), 2)
            if g["pct_pobreza_total"].notna().any() else np.nan,
            "idh_2019": round(float(g["idh_2019"].iloc[0]), 3)
            if g["idh_2019"].notna().any() else np.nan,
            "poblacion_imputada": bool(g["poblacion_imputada"].any()),
        })

    dist = df.groupby("ubigeo_distrito").apply(_agg, include_groups=False).reset_index()

    df.to_parquet(out / "metrics_ccpp.parquet", index=False)
    dist.to_parquet(out / "metrics_distrito.parquet", index=False)
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # GeoPackage distrital (polígonos + métricas) para el choropleth de la Fase 4
    try:
        gdist = load_districts().merge(dist, on="ubigeo_distrito", how="left",
                                       suffixes=("", "_m"))
        gdist.to_file(out / "metrics_distrito.gpkg", driver="GPKG")
        log.info("GeoPackage distrital -> %s (%d distritos)",
                 out / "metrics_distrito.gpkg", len(gdist))
    except Exception as exc:  # noqa: BLE001
        log.warning("No se generó el GeoPackage distrital: %s", exc)

    log.info("Fase 3 completa.\n%s", json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    build()
