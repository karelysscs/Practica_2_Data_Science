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

log = get_logger("phase3")

M = CFG["metrics"]
BANDS = M["coverage_bands_min"]
GOLDEN = M["golden_hour_min"]
PRIMARY = "driving" if "driving" in CFG["routing"]["profiles"] else CFG["routing"]["profiles"][0]

_DISTRICTS_RAW = "peru_distritos.geojson"
_DISTRICTS_URL = ("https://raw.githubusercontent.com/juaneladio/peru-geojson/"
                  "master/peru_distrital_simple.geojson")


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

    tcol = f"min_{PRIMARY}"
    if "poblacion" not in df or df["poblacion"].isna().all():
        log.warning("Sin población: métricas con PESO UNIFORME (pendiente descarga).")
        df["poblacion"] = 1.0
        pop_ok = False
    else:
        df["poblacion"] = df["poblacion"].fillna(df["poblacion"].median())
        pop_ok = True
    w = df["poblacion"]

    # --- altitud: terciles (dimensión secundaria) ---
    if "altitud" in df.columns and df["altitud"].notna().any():
        df["altitud_tercil"] = pd.qcut(df["altitud"], 3,
                                       labels=["bajo", "medio", "alto"], duplicates="drop")
    else:
        df["altitud_tercil"] = pd.NA

    # --- distrito ---
    df["ubigeo_distrito"] = df["ubigeo"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(6)
    dep_col = "departamento" if "departamento" in df.columns else "dep"

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
                "pct_fuera_hora_dorada": round(
                    float(g["poblacion"][g[tcol] > GOLDEN].sum() / g["poblacion"].sum()) * 100, 2),
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
            "poblacion": float(gw.sum()) if pop_ok else np.nan,
            "acceso_min_ponderado": round(wmean(g[tcol], gw), 2),
            "pct_fuera_hora_dorada": round(
                float(gw[g[tcol] > GOLDEN].sum() / gw.sum()) * 100, 2),
            "gini_acceso": round(gini(g[tcol], gw), 4),
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
