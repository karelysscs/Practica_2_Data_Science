"""Fase 3 — Construcción de métricas y análisis (2.0 pts).

A partir de la matriz O-D y la población de cada CCPP:
  * tiempo mínimo de acceso por CCPP (al establecimiento resolutivo más cercano)
  * bandas de cobertura ponderadas por población (30 / 60 / 120+ min)
  * desigualdad: coeficiente de Gini del tiempo de acceso
  * contraste urbano-rural
  * agregados a nivel distrito (para el choropleth del dashboard)
  * cruce con una dimensión secundaria (altitud / pobreza / demografía)

Salida: ``data/outputs/metrics_ccpp.parquet``, ``metrics_distrito.parquet``,
        ``summary.json``
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


def gini(values: np.ndarray, weights: np.ndarray | None = None) -> float:
    """Coeficiente de Gini (0 = igualdad perfecta). Admite ponderación."""
    v = np.asarray(values, dtype=float)
    if weights is None:
        weights = np.ones_like(v)
    w = np.asarray(weights, dtype=float)
    mask = np.isfinite(v) & (w > 0)
    v, w = v[mask], w[mask]
    if v.size == 0:
        return float("nan")
    order = np.argsort(v)
    v, w = v[order], w[order]
    cum_w = np.cumsum(w)
    cum_vw = np.cumsum(v * w)
    return float(1 - np.sum((cum_vw[1:] + cum_vw[:-1]) * np.diff(cum_w)) /
                (cum_vw[-1] * cum_w[-1]))


def min_access_time(matrix: pd.DataFrame, profile: str = "driving") -> pd.DataFrame:
    """Tiempo mínimo (min) por CCPP para el perfil dado."""
    sub = matrix.loc[matrix["profile"] == profile]
    best = (
        sub.groupby("ccpp_id")["seconds"].min().div(60).rename("min_acceso_min")
        .reset_index()
    )
    nearest = sub.loc[sub.groupby("ccpp_id")["seconds"].idxmin(),
                      ["ccpp_id", "facility_id"]].rename(columns={"facility_id": "estab_mas_cercano"})
    return best.merge(nearest, on="ccpp_id")


def coverage_bands(df: pd.DataFrame, time_col: str, pop_col: str) -> dict:
    total = df[pop_col].sum()
    out = {}
    prev = 0
    for b in BANDS:
        share = df.loc[df[time_col] <= b, pop_col].sum() / total
        out[f"<= {b} min"] = round(float(share) * 100, 2)
        prev = b
    out[f"> {prev} min"] = round(float(df.loc[df[time_col] > prev, pop_col].sum() / total) * 100, 2)
    return out


def build(profile: str = "driving") -> None:
    proc, out = get_path("processed"), get_path("outputs")
    matrix = pd.read_parquet(proc / "od_matrix.parquet")
    demand = pd.read_parquet(proc / "demand_validated.parquet")
    demand = demand.loc[demand["qc_ok"]].rename(columns={"nombre_ccpp": "ccpp_id"})

    acc = min_access_time(matrix, profile)
    ccpp = demand.merge(acc, on="ccpp_id", how="left")

    pop = "poblacion"
    tcol = "min_acceso_min"
    summary = {
        "perfil": profile,
        "poblacion_total": float(ccpp[pop].sum()),
        "tiempo_min_ponderado": float(
            np.average(ccpp[tcol].dropna(),
                       weights=ccpp.loc[ccpp[tcol].notna(), pop])
        ),
        "mediana_min": float(ccpp[tcol].median()),
        "pct_fuera_hora_dorada": round(
            float(ccpp.loc[ccpp[tcol] > M["golden_hour_min"], pop].sum() / ccpp[pop].sum()) * 100, 2
        ),
        "gini_tiempo_acceso": round(gini(ccpp[tcol].to_numpy(), ccpp[pop].to_numpy()), 4),
        "cobertura_bandas_pct": coverage_bands(ccpp.dropna(subset=[tcol]), tcol, pop),
    }
    if "ambito" in ccpp.columns:
        summary["urbano_rural_min"] = (
            ccpp.groupby("ambito")
            .apply(lambda g: float(np.average(g[tcol].dropna(),
                   weights=g.loc[g[tcol].notna(), pop])))
            .to_dict()
        )

    ccpp["ubigeo_distrito"] = ccpp["ubigeo"].astype(str).str.zfill(6)
    dist = (
        ccpp.groupby("ubigeo_distrito")
        .apply(lambda g: pd.Series({
            "poblacion": g[pop].sum(),
            "tiempo_min_ponderado": np.average(g[tcol].dropna(),
                weights=g.loc[g[tcol].notna(), pop]) if g[tcol].notna().any() else np.nan,
            "pct_fuera_hora_dorada": g.loc[g[tcol] > M["golden_hour_min"], pop].sum() / g[pop].sum() * 100,
        }))
        .reset_index()
    )

    ccpp.to_parquet(out / "metrics_ccpp.parquet", index=False)
    dist.to_parquet(out / "metrics_distrito.parquet", index=False)
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("Fase 3 completa. Resumen:\n%s", json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    build()
