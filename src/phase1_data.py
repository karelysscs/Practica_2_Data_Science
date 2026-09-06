"""Fase 1 — Adquisición y validación de datos (3.0 pts).

Construye datasets geoespaciales validados de:
  * demanda  -> centros poblados (población + coordenadas)
  * oferta   -> establecimientos de salud resolutivos (RENIPRESS, cat. II-1+)

Regla de oro: **no se descartan filas en silencio**. Cada registro problemático
se marca con una bandera y se documenta en un reporte de calidad
(``reports/quality/*.csv`` y ``*.md``).

Uso:
    python -m src.phase1_data
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

import pandas as pd

from src.config import CFG, department_ubigeos, get_path
from src.utils import get_logger, haversine_m, timestamp

log = get_logger("phase1")

V = CFG["validation"]
LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = V["peru_bbox"]


# --------------------------------------------------------------------------- #
# Normalización
# --------------------------------------------------------------------------- #
def strip_accents(text: str) -> str:
    if not isinstance(text, str):
        return text
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip()


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = (
        df.columns.str.strip()
        .str.lower()
        .map(strip_accents)
        .str.replace(r"[^\w]+", "_", regex=True)
        .str.strip("_")
    )
    return df


def coerce_coords(df: pd.DataFrame, lat: str = "latitud", lon: str = "longitud") -> pd.DataFrame:
    df = df.copy()
    for col in (lat, lon):
        df[col] = (
            df[col].astype(str)
            .str.replace(",", ".", regex=False)
            .str.replace(r"[^\d.\-]", "", regex=True)
        )
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# --------------------------------------------------------------------------- #
# Reglas de validación -> devuelven una Serie booleana (True = problema)
# --------------------------------------------------------------------------- #
def flag_missing_coords(df: pd.DataFrame) -> pd.Series:
    return df["latitud"].isna() | df["longitud"].isna()


def flag_zero_island(df: pd.DataFrame) -> pd.Series:
    """(0, 0) o coordenadas casi nulas: error clásico de captura."""
    return (df["latitud"].abs() < 0.01) & (df["longitud"].abs() < 0.01)


def flag_out_of_peru(df: pd.DataFrame) -> pd.Series:
    inside = (
        df["longitud"].between(LON_MIN, LON_MAX)
        & df["latitud"].between(LAT_MIN, LAT_MAX)
    )
    return ~inside & df["latitud"].notna() & df["longitud"].notna()


def flag_swapped_latlon(df: pd.DataFrame) -> pd.Series:
    """lat/long intercambiadas: lat fuera de rango pero válida si se invierte."""
    bad = ~df["latitud"].between(LAT_MIN, LAT_MAX)
    fixable = df["longitud"].between(LAT_MIN, LAT_MAX) & df["latitud"].between(LON_MIN, LON_MAX)
    return bad & fixable


def flag_wrong_department(df: pd.DataFrame, ubigeo_col: str) -> pd.Series:
    """UBIGEO fuera de los departamentos del estudio."""
    valid = set(department_ubigeos().values())
    dd = df[ubigeo_col].astype(str).str.zfill(6).str[:2]
    return ~dd.isin(valid)


def flag_duplicates_by_code(df: pd.DataFrame, code_col: str) -> pd.Series:
    return df.duplicated(subset=[code_col], keep=False) & df[code_col].notna()


def flag_spatial_duplicates(df: pd.DataFrame, thresh_m: float | None = None) -> pd.Series:
    """Marca pares de puntos a menos de ``thresh_m`` (comparación O(n^2) simple;
    los datasets por 3 departamentos son suficientemente chicos)."""
    thresh_m = thresh_m or V["duplicate_distance_m"]
    flags = pd.Series(False, index=df.index)
    sub = df.dropna(subset=["latitud", "longitud"])
    rows = list(sub.itertuples())
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            d = haversine_m(rows[i].longitud, rows[i].latitud,
                            rows[j].longitud, rows[j].latitud)
            if d <= thresh_m:
                flags.at[rows[i].Index] = True
                flags.at[rows[j].Index] = True
    return flags


# --------------------------------------------------------------------------- #
# Orquestación
# --------------------------------------------------------------------------- #
def validate(df: pd.DataFrame, *, code_col: str, ubigeo_col: str, kind: str) -> pd.DataFrame:
    df = df.copy()
    checks = {
        "coord_faltante": flag_missing_coords(df),
        "isla_cero": flag_zero_island(df),
        "fuera_de_peru": flag_out_of_peru(df),
        "latlon_invertida": flag_swapped_latlon(df),
        "depto_fuera_ambito": flag_wrong_department(df, ubigeo_col),
        "duplicado_codigo": flag_duplicates_by_code(df, code_col),
        "duplicado_espacial": flag_spatial_duplicates(df),
    }
    for name, mask in checks.items():
        df[f"qc_{name}"] = mask.fillna(False)
    qc_cols = [c for c in df.columns if c.startswith("qc_")]
    df["qc_ok"] = ~df[qc_cols].any(axis=1)
    _write_quality_report(df, qc_cols, kind)
    return df


def _write_quality_report(df: pd.DataFrame, qc_cols: list[str], kind: str) -> None:
    out = get_path("quality_reports")
    resumen = (
        df[qc_cols].sum().sort_values(ascending=False).rename("registros").to_frame()
    )
    resumen["porcentaje"] = (resumen["registros"] / len(df) * 100).round(2)

    resumen.to_csv(out / f"calidad_{kind}.csv")
    df.loc[~df["qc_ok"]].to_csv(out / f"calidad_{kind}_detalle.csv", index=False)

    md = [
        f"# Reporte de calidad — {kind}",
        "",
        f"_Generado: {timestamp()}_",
        "",
        f"- Registros totales: **{len(df)}**",
        f"- Registros sin problemas (`qc_ok`): **{int(df['qc_ok'].sum())}** "
        f"({df['qc_ok'].mean() * 100:.1f} %)",
        "",
        "| Regla | Registros | % |",
        "|-------|-----------|---|",
    ]
    for regla, row in resumen.iterrows():
        md.append(f"| `{regla}` | {int(row['registros'])} | {row['porcentaje']} |")
    md += ["", "> Los registros marcados **no se eliminan**: se conservan con sus",
           "> banderas `qc_*` para trazabilidad y se excluyen del análisis vía `qc_ok`."]
    (out / f"calidad_{kind}.md").write_text("\n".join(md), encoding="utf-8")
    log.info("Reporte de calidad '%s' escrito en %s", kind, out)


# --------------------------------------------------------------------------- #
# Carga de fuentes
# --------------------------------------------------------------------------- #
def load_facilities() -> pd.DataFrame:
    path = get_path("raw") / "renipress.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"Falta {path}. Ver docs/DATA_SOURCES.md (descarga manual de RENIPRESS)."
        )
    df = pd.read_csv(path, dtype=str, encoding="latin-1", on_bad_lines="skip")
    df = normalize_columns(df)
    df = coerce_coords(df)
    # TODO: mapear nombres reales de columnas de RENIPRESS -> canónicos
    #   codigo_renaes, nombre, categoria, ubigeo
    cats = set(CFG["facilities"]["resolutive_categories"])
    if "categoria" in df.columns:
        df["es_resolutivo"] = df["categoria"].str.upper().str.strip().isin(cats)
    return df


def load_demand() -> pd.DataFrame:
    path = get_path("raw") / "centros_poblados.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"Falta {path}. Ver docs/DATA_SOURCES.md (descarga manual de CCPP)."
        )
    df = pd.read_csv(path, dtype=str, encoding="latin-1", on_bad_lines="skip")
    df = normalize_columns(df)
    df = coerce_coords(df)
    if "poblacion" in df.columns:
        df["poblacion"] = pd.to_numeric(df["poblacion"], errors="coerce")
        thr = CFG["demand"]["urban_population_threshold"]
        df["ambito"] = df["poblacion"].apply(
            lambda p: "urbano" if pd.notna(p) and p >= thr else "rural"
        )
    return df


def run() -> None:
    log.info("=== Fase 1: adquisición y validación ===")
    fac = load_facilities()
    fac = validate(fac, code_col="codigo_renaes", ubigeo_col="ubigeo", kind="oferta")
    dem = load_demand()
    dem = validate(dem, code_col="nombre_ccpp", ubigeo_col="ubigeo", kind="demanda")

    proc = get_path("processed")
    fac.to_parquet(proc / "facilities_validated.parquet", index=False)
    dem.to_parquet(proc / "demand_validated.parquet", index=False)
    log.info("Fase 1 completa: %d establecimientos, %d centros poblados", len(fac), len(dem))


if __name__ == "__main__":
    run()
