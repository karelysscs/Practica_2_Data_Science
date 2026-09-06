"""Fase 1 — Adquisición y validación de datos (3.0 pts).

Construye datasets geoespaciales validados de:
  * OFERTA   -> establecimientos de salud resolutivos (RENIPRESS, cat. II-1+)
  * DEMANDA  -> centros poblados (población + coordenadas)

Regla de oro: **no se descartan filas en silencio**. Cada registro problemático
se marca con banderas ``qc_*`` y se documenta en un reporte de calidad
(``reports/quality/calidad_<fuente>.{md,csv}``). El análisis posterior filtra
por ``qc_ok`` pero los registros marcados quedan versionados para trazabilidad.

Uso:
    python -m src.phase1_data
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

import pandas as pd

from src.config import CFG, department_ubigeos, get_path
from src.utils import get_logger, haversine_m, timestamp

try:  # geopandas es opcional hasta que haga falta leer shapefiles
    import geopandas as gpd
except ImportError:  # pragma: no cover
    gpd = None

log = get_logger("phase1")

V = CFG["validation"]
LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = V["peru_bbox"]

# Mapeo de columnas RENIPRESS (padrón SUSALUD) -> nombres canónicos
RENIPRESS_MAP = {
    "COD_IPRESS": "codigo",
    "NOMBRE": "nombre",
    "CLASIFICACION": "clasificacion",
    "TIPO_ESTABLECIMIENTO": "tipo",
    "INSTITUCION": "institucion",
    "CATEGORIA": "categoria",
    "ESTADO": "estado",
    "DEPARTAMENTO": "departamento",
    "PROVINCIA": "provincia",
    "DISTRITO": "distrito",
    "UBIGEO": "ubigeo",
    "NORTE": "latitud",
    "ESTE": "longitud",
}

# Posibles nombres de columnas en padrones de centros poblados
# (SIGMED CP_P.shp / INEI cartografía censal / geogpsperu)
DEMAND_ALIASES = {
    "codigo":       ["codigo", "codcp", "cod_ccpp", "codccpp", "id_ccpp", "ccpp", "codigo_ccpp", "cod_cp"],
    "cod_inei":     ["cpinei", "codccpp_inei", "cod_ccpp_inei", "ccpp_inei", "id_ccpp_inei"],
    "nombre":       ["descripcio", "nomcp", "nomccpp", "nom_ccpp", "nombre_ccpp", "nombre", "nom_cp", "centro_poblado"],
    "poblacion":    ["poblacion", "pob_total", "pobla", "poblac", "pob2017", "pob", "cant_pob",
                     "poblacen", "pobtotal", "pob_tot"],
    "viviendas":    ["viviendas", "viv_total", "vivienda", "viv", "vivtotal", "total_vivi"],
    "ubigeo":       ["ubigeo", "iddist", "cod_dist", "ubigeo_dist", "codigo_ubigeo"],
    "latitud":      ["latitud", "lat", "y", "coord_y", "ygd", "norte"],
    "longitud":     ["longitud", "long", "lon", "x", "coord_x", "xgd", "este"],
    "altitud":      ["altitud", "z", "elevacion", "msnm", "altura"],
    "categoria":    ["categoria", "categoria_", "cat_ccpp"],
    "region_nat":   ["region_nat", "region_natural", "reg_nat"],
    "departamento": ["departamen", "dep", "departamento", "dpto", "nombdep", "nom_dep"],
}


# --------------------------------------------------------------------------- #
# Normalización
# --------------------------------------------------------------------------- #
def strip_accents(text):
    if not isinstance(text, str):
        return text
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip()


def snake_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = (
        pd.Index(df.columns).astype(str).str.strip().str.lower()
        .map(strip_accents)
        .str.replace(r"[^\w]+", "_", regex=True)
        .str.strip("_")
    )
    return df


def coerce_coords(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("latitud", "longitud"):
        s = (
            df[col].astype(str)
            .str.strip()
            .str.replace(",", ".", regex=False)
            .str.replace(r"[^\d.\-eE]", "", regex=True)
        )
        df[col] = pd.to_numeric(s, errors="coerce")
    return df


def detect_mojibake(series: pd.Series) -> pd.Series:
    """True si el texto tiene el carácter de reemplazo U+FFFD o secuencias
    típicas de doble codificación (Ã, Â seguidas de símbolo)."""
    s = series.fillna("").astype(str)
    return s.str.contains("�", regex=False) | s.str.contains(r"Ã.|Â.| Â", regex=True)


# --------------------------------------------------------------------------- #
# Reglas de validación -> Serie booleana (True = problema)
# --------------------------------------------------------------------------- #
def flag_missing_coords(df):
    return df["latitud"].isna() | df["longitud"].isna()


def flag_zero_island(df):
    return (df["latitud"].abs() < 0.01) & (df["longitud"].abs() < 0.01)


def flag_out_of_peru(df):
    inside = df["longitud"].between(LON_MIN, LON_MAX) & df["latitud"].between(LAT_MIN, LAT_MAX)
    return ~inside & df["latitud"].notna() & df["longitud"].notna()


def flag_swapped_latlon(df):
    bad = ~df["latitud"].between(LAT_MIN, LAT_MAX) & df["latitud"].notna()
    fixable = df["longitud"].between(LAT_MIN, LAT_MAX) & df["latitud"].between(LON_MIN, LON_MAX)
    return bad & fixable


def flag_wrong_department(df):
    valid = set(department_ubigeos().values())
    dd = df["ubigeo"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(6).str[:2]
    return ~dd.isin(valid)


def flag_dup_code(df):
    return df["codigo"].duplicated(keep=False) & df["codigo"].notna()


def flag_spatial_duplicates(df, thresh_m=None):
    """Pares de puntos a < thresh_m (comparación O(n^2); datasets por 3 dptos.
    son chicos). Solo compara dentro del mismo distrito para acotar."""
    thresh_m = thresh_m or V["duplicate_distance_m"]
    flags = pd.Series(False, index=df.index)
    sub = df.dropna(subset=["latitud", "longitud"])
    for _, grp in sub.groupby(sub["ubigeo"].astype(str).str[:6]):
        rows = list(grp.itertuples())
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if haversine_m(rows[i].longitud, rows[i].latitud,
                               rows[j].longitud, rows[j].latitud) <= thresh_m:
                    flags.at[rows[i].Index] = True
                    flags.at[rows[j].Index] = True
    return flags


def flag_encoding(df):
    text_cols = [c for c in ("nombre", "departamento", "provincia", "distrito") if c in df.columns]
    out = pd.Series(False, index=df.index)
    for c in text_cols:
        out |= detect_mojibake(df[c])
    return out


# --------------------------------------------------------------------------- #
# Orquestación de validación
# --------------------------------------------------------------------------- #
def validate(df: pd.DataFrame, *, kind: str, extra_checks: dict | None = None) -> pd.DataFrame:
    df = df.copy()
    checks = {
        "coord_faltante": flag_missing_coords(df),
        "isla_cero": flag_zero_island(df),
        "fuera_de_peru": flag_out_of_peru(df),
        "latlon_invertida": flag_swapped_latlon(df),
        "depto_fuera_ambito": flag_wrong_department(df),
        "duplicado_codigo": flag_dup_code(df),
        "duplicado_espacial": flag_spatial_duplicates(df),
        "problema_encoding": flag_encoding(df),
    }
    if extra_checks:
        checks.update(extra_checks)
    for name, mask in checks.items():
        df[f"qc_{name}"] = mask.reindex(df.index).fillna(False).astype(bool)
    qc_cols = [c for c in df.columns if c.startswith("qc_")]
    df["qc_ok"] = ~df[qc_cols].any(axis=1)
    _write_quality_report(df, qc_cols, kind)
    return df


def _write_quality_report(df, qc_cols, kind):
    out = get_path("quality_reports")
    resumen = df[qc_cols].sum().sort_values(ascending=False).rename("registros").to_frame()
    resumen["porcentaje"] = (resumen["registros"] / max(len(df), 1) * 100).round(2)
    resumen.to_csv(out / f"calidad_{kind}.csv")
    df.loc[~df["qc_ok"]].to_csv(out / f"calidad_{kind}_detalle.csv", index=False)

    md = [
        f"# Reporte de calidad — {kind}", "",
        f"_Generado: {timestamp()}_", "",
        f"- Registros totales: **{len(df)}**",
        f"- Sin problemas (`qc_ok`): **{int(df['qc_ok'].sum())}** "
        f"({df['qc_ok'].mean() * 100:.1f} %)", "",
        "| Regla | Registros | % |", "|---|---|---|",
    ]
    for regla, row in resumen.iterrows():
        md.append(f"| `{regla}` | {int(row['registros'])} | {row['porcentaje']} |")
    md += ["", "> Los registros marcados **no se eliminan**: conservan sus banderas",
           "> `qc_*` y se excluyen del análisis mediante `qc_ok`."]
    (out / f"calidad_{kind}.md").write_text("\n".join(md), encoding="utf-8")
    log.info("Reporte de calidad '%s' -> %s", kind, out)


# --------------------------------------------------------------------------- #
# Carga: OFERTA (RENIPRESS)
# --------------------------------------------------------------------------- #
def load_facilities() -> pd.DataFrame:
    path = get_path("raw") / "renipress.csv"
    if not path.exists():
        raise FileNotFoundError(f"Falta {path}. Ver docs/DATA_SOURCES.md")

    raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig", sep=";")
    raw.columns = [c.strip() for c in raw.columns]
    df = raw.rename(columns=RENIPRESS_MAP)[list(RENIPRESS_MAP.values())].copy()

    df["codigo"] = df["codigo"].str.strip().str.zfill(8)
    df["categoria"] = df["categoria"].str.strip().str.upper()
    df["estado"] = df["estado"].str.strip().str.upper()
    df["ubigeo"] = df["ubigeo"].str.strip().str.zfill(6)
    df = coerce_coords(df)

    cats = {c.upper() for c in CFG["facilities"]["resolutive_categories"]}
    df["es_activo"] = df["estado"].eq("ACTIVO")
    df["es_resolutivo"] = df["categoria"].isin(cats)
    df["categoria_invalida"] = ~df["categoria"].str.match(r"^(I{1,3})-(\d|E)$").fillna(False)

    # ámbito de estudio (los 3 departamentos): se valida SOLO el subconjunto
    deps = {strip_accents(d).upper() for d in CFG["departments"].values()}
    df["en_ambito"] = df["departamento"].map(strip_accents).str.upper().isin(deps)
    n_nacional = len(df)
    df = df.loc[df["en_ambito"]].copy()

    log.info("RENIPRESS: %d filas nacionales; %d en ámbito (%s); %d activas resolutivas",
             n_nacional, len(df), ", ".join(sorted(deps)),
             int((df["es_activo"] & df["es_resolutivo"]).sum()))

    extra = {
        "no_activo": ~df["es_activo"],
        "categoria_invalida": df["categoria_invalida"],
    }
    df = validate(df, kind="oferta", extra_checks=extra)

    # 'qc_ok' para oferta = válido geográficamente Y activo Y categoría resolutiva
    df["apto_oferta"] = (
        df["qc_ok"] | (~df[["qc_coord_faltante", "qc_isla_cero", "qc_fuera_de_peru",
                            "qc_duplicado_codigo"]].any(axis=1))
    ) & df["es_activo"] & df["es_resolutivo"] & df["en_ambito"]
    return df


# --------------------------------------------------------------------------- #
# Carga: DEMANDA (centros poblados)
# --------------------------------------------------------------------------- #
_VECTOR_EXT = (".shp", ".gpkg", ".geojson", ".json")


# Carpetas candidatas para el padrón de demanda, en orden de preferencia.
# geogpsperu (CPV 2017) trae población + altitud + región natural en la misma capa.
_DEMAND_DIRS = ["*cpp_pobla*", "*ccpp_pobla*", "centros_poblados"]


def _demand_files() -> list[Path]:
    raw = get_path("raw")
    for pat in _DEMAND_DIRS:
        dirs = [d for d in raw.glob(pat) if d.is_dir()]
        files = [p for d in dirs for ext in _VECTOR_EXT for p in d.rglob(f"*{ext}")]
        if files:
            return sorted(files)
    loose = [p for ext in _VECTOR_EXT + (".csv",) for p in raw.glob(f"*{ext}")]
    if loose:
        return sorted(loose)
    raise FileNotFoundError(
        "No se encontró el padrón de centros poblados en data/raw/. "
        "Ver docs/DATA_SOURCES.md"
    )


def _read_any(path: Path) -> pd.DataFrame:
    """Lee shapefile/GeoPackage/GeoJSON (geopandas) o CSV (pandas)."""
    if path.suffix.lower() in _VECTOR_EXT:
        if gpd is None:
            raise ImportError("Se necesita geopandas para leer " + path.suffix)
        g = gpd.read_file(path)
        if g.crs and g.crs.to_epsg() != 4326:
            g = g.to_crs(4326)
        pts = g.geometry.representative_point()
        out = pd.DataFrame(g.drop(columns=g.geometry.name))
        out["_geom_lon"], out["_geom_lat"] = pts.x.values, pts.y.values
        return out
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(path, dtype=str, encoding=enc, sep=None, engine="python")
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    raise ValueError(f"No se pudo leer {path}")


def _resolve_aliases(df: pd.DataFrame) -> pd.DataFrame:
    ren: dict[str, str] = {}
    for canonical, aliases in DEMAND_ALIASES.items():
        if canonical in df.columns:
            continue
        for a in aliases:
            if a in df.columns and a not in ren:
                ren[a] = canonical
                break
    return df.rename(columns=ren)


def _load_population_lookup() -> pd.DataFrame | None:
    """Lee un padrón auxiliar con población en data/raw/ccpp_poblacion/ y
    devuelve [codigo, cod_inei, poblacion, viviendas] para hacer join."""
    folder = get_path("raw") / "ccpp_poblacion"
    if not folder.exists():
        return None
    files = [p for ext in _VECTOR_EXT + (".csv", ".xlsx")
             for p in folder.rglob(f"*{ext}")]
    if not files:
        return None
    frames = []
    for f in files:
        try:
            raw = _read_any(f) if f.suffix.lower() != ".xlsx" else pd.read_excel(f, dtype=str)
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo leer población %s: %s", f.name, exc)
            continue
        d = _resolve_aliases(snake_columns(raw))
        keep = [c for c in ("codigo", "cod_inei", "poblacion", "viviendas") if c in d.columns]
        if "poblacion" in keep and ({"codigo", "cod_inei"} & set(keep)):
            frames.append(d[keep])
    if not frames:
        return None
    lut = pd.concat(frames, ignore_index=True).drop_duplicates()
    lut["poblacion"] = pd.to_numeric(
        lut["poblacion"].astype(str).str.replace(r"[^\d.]", "", regex=True), errors="coerce")
    log.info("Lookup de población: %d filas desde %s", len(lut), folder)
    return lut


def load_demand() -> pd.DataFrame:
    files = _demand_files()
    log.info("Padrón de CCPP: %d archivo(s) — %s",
             len(files), ", ".join(p.parent.name + "/" + p.name for p in files))
    parts = []
    for p in files:
        d = _resolve_aliases(snake_columns(_read_any(p)))
        # descartar archivos defectuosos (p. ej. geogpsperu Tumbes: CODIGO="0",
        # POBLACION=0 en todas las filas)
        bad_code = "codigo" in d and (d["codigo"].astype(str).str.fullmatch(r"0+").mean() > 0.9)
        bad_pop = "poblacion" in d and pd.to_numeric(d["poblacion"], errors="coerce").fillna(0).eq(0).mean() > 0.95
        if bad_code or bad_pop:
            log.warning("Padrón DEFECTUOSO ignorado (%s): %s",
                        "códigos nulos" if bad_code else "población toda cero", p)
            continue
        parts.append(d)
    if not parts:
        raise FileNotFoundError("Todos los padrones de CCPP encontrados están defectuosos. "
                                "Re-descargar (ver docs/DATA_SOURCES.md).")
    common = set.intersection(*(set(p.columns) for p in parts))
    df = pd.concat([p[sorted(common)] for p in parts], ignore_index=True)

    # coordenadas: usar columnas explícitas o, si no hay, las de la geometría
    if "latitud" not in df.columns and "_geom_lat" in df.columns:
        df["latitud"], df["longitud"] = df["_geom_lat"], df["_geom_lon"]
    df = coerce_coords(df)

    for col in ("codigo", "cod_inei"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    if "codigo" not in df.columns:
        df["codigo"] = pd.NA
    if "altitud" in df.columns:
        df["altitud"] = pd.to_numeric(df["altitud"], errors="coerce")

    # --- población: de la propia capa o vía join con padrón auxiliar ---
    pop_source = "capa principal"
    if "poblacion" not in df.columns:
        lut = _load_population_lookup()
        if lut is not None:
            key = "cod_inei" if ("cod_inei" in df.columns and "cod_inei" in lut.columns) else "codigo"
            df = df.merge(lut.dropna(subset=[key]).drop_duplicates(key),
                          on=key, how="left", suffixes=("", "_lut"))
            pop_source = f"join auxiliar por {key}"
        else:
            log.warning("Sin población: falta data/raw/ccpp_poblacion/. "
                        "Ver docs/DATA_SOURCES.md")
            df["poblacion"] = pd.NA
    df["poblacion"] = pd.to_numeric(
        df["poblacion"].astype(str).str.replace(r"[^\d.]", "", regex=True), errors="coerce")
    matched = df["poblacion"].notna().mean() * 100
    log.info("Población (%s): %.1f%% de CCPP con dato", pop_source, matched)

    thr = CFG["demand"]["urban_population_threshold"]
    df["ambito_uro"] = df["poblacion"].apply(
        lambda p: "urbano" if pd.notna(p) and p >= thr else "rural")

    deps = {strip_accents(d).upper() for d in CFG["departments"].values()}
    if "departamento" in df.columns:
        df["en_ambito"] = df["departamento"].map(strip_accents).str.upper().isin(deps)
    else:
        valid = set(department_ubigeos().values())
        df["en_ambito"] = df["ubigeo"].astype(str).str.zfill(6).str[:2].isin(valid)

    n_nacional = len(df)
    df = df.loc[df["en_ambito"]].copy()
    log.info("CCPP: %d nacionales; %d en ámbito", n_nacional, len(df))

    extra = {
        "poblacion_faltante": df["poblacion"].isna(),
        "poblacion_no_positiva": df["poblacion"].fillna(1) < CFG["demand"]["min_population"],
    }
    df = validate(df, kind="demanda", extra_checks=extra)
    # para demanda, la falta de población no descalifica el punto geográficamente;
    # apto_demanda exige geometría válida + ámbito (la población se imputa en Fase 3)
    geo_bad = df[["qc_coord_faltante", "qc_isla_cero", "qc_fuera_de_peru",
                  "qc_latlon_invertida", "qc_depto_fuera_ambito"]].any(axis=1)
    df["apto_demanda"] = ~geo_bad & df["en_ambito"]
    return df


# --------------------------------------------------------------------------- #
def run() -> None:
    log.info("=== Fase 1: adquisición y validación ===")
    proc = get_path("processed")

    fac = load_facilities()
    fac.to_parquet(proc / "facilities_validated.parquet", index=False)
    log.info("Oferta apta (resolutiva, activa, en ámbito): %d", int(fac["apto_oferta"].sum()))

    try:
        dem = load_demand()
        dem.to_parquet(proc / "demand_validated.parquet", index=False)
        log.info("Demanda apta (CCPP válidos en ámbito): %d", int(dem["apto_demanda"].sum()))
    except FileNotFoundError as exc:
        log.warning("Demanda pendiente: %s", exc)

    log.info("Fase 1 completa. Reportes en %s", get_path("quality_reports"))


if __name__ == "__main__":
    run()
