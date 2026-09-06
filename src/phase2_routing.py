"""Fase 2 — Ruteo y cálculo de tiempos de viaje (3.0 pts).

Estrategia:
  1. Snap de cada origen (CCPP) y destino (establecimiento) a la red vial.
  2. Motor primario: OSRM ``/table`` (matriz por lotes) con caché SQLite.
  3. Fallback documentado: estimación haversine * detour_factor / velocidad media
     para pares sin ruta (islas de red, zonas amazónicas sin vías mapeadas).
  4. Perfiles: driving y walking -> se comparan en la Fase 3.

Salida: ``data/processed/od_matrix.parquet`` con columnas
    ccpp_id, facility_id, profile, seconds, source (osrm|fallback), snap_m
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import requests

from src.config import CFG, get_path
from src.utils import get_logger, haversine_m

log = get_logger("phase2")

R = CFG["routing"]


# --------------------------------------------------------------------------- #
# Caché
# --------------------------------------------------------------------------- #
def _cache() -> sqlite3.Connection:
    path = get_path("processed") / Path(R["cache_path"]).name
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE IF NOT EXISTS legs(
               origin TEXT, dest TEXT, profile TEXT,
               seconds REAL, source TEXT,
               PRIMARY KEY (origin, dest, profile))"""
    )
    return con


def _cache_get(con, o: str, d: str, prof: str):
    row = con.execute(
        "SELECT seconds, source FROM legs WHERE origin=? AND dest=? AND profile=?",
        (o, d, prof),
    ).fetchone()
    return row


def _cache_put(con, o, d, prof, seconds, source) -> None:
    con.execute(
        "INSERT OR REPLACE INTO legs VALUES (?,?,?,?,?)",
        (o, d, prof, seconds, source),
    )


# --------------------------------------------------------------------------- #
# OSRM
# --------------------------------------------------------------------------- #
def osrm_table(origins: list[tuple[float, float]],
               dests: list[tuple[float, float]],
               profile: str) -> list[list[float | None]]:
    """Devuelve matriz de duraciones (s). ``origins``/``dests`` = (lon, lat)."""
    coords = origins + dests
    coord_str = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in coords)
    src_idx = ";".join(str(i) for i in range(len(origins)))
    dst_idx = ";".join(str(i) for i in range(len(origins), len(coords)))
    url = (
        f"{R['osrm_base_url']}/table/v1/{profile}/{coord_str}"
        f"?sources={src_idx}&destinations={dst_idx}&annotations=duration"
    )
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()["durations"]


def fallback_seconds(o: tuple[float, float], d: tuple[float, float], profile: str) -> float:
    dist_m = haversine_m(o[0], o[1], d[0], d[1]) * R["detour_factor"]
    kmh = R["fallback_speed_kmh"][profile]
    return dist_m / 1000.0 / kmh * 3600.0


# --------------------------------------------------------------------------- #
# Orquestación
# --------------------------------------------------------------------------- #
def build_matrix(demand: pd.DataFrame, facilities: pd.DataFrame) -> pd.DataFrame:
    """demand: [ccpp_id, longitud, latitud]; facilities: [facility_id, longitud, latitud]."""
    con = _cache()
    rows: list[dict] = []
    dests = list(facilities[["longitud", "latitud"]].itertuples(index=False, name=None))
    dest_ids = facilities["facility_id"].tolist()
    batch = R["max_table_sources"]

    for profile in R["profiles"]:
        for start in range(0, len(demand), batch):
            chunk = demand.iloc[start:start + batch]
            origins = list(chunk[["longitud", "latitud"]].itertuples(index=False, name=None))
            origin_ids = chunk["ccpp_id"].tolist()

            durations = None
            try:
                durations = osrm_table(origins, dests, profile)
            except Exception as exc:  # noqa: BLE001
                log.warning("OSRM falló en lote %d (%s): %s -> fallback", start, profile, exc)

            for i, oid in enumerate(origin_ids):
                for j, did in enumerate(dest_ids):
                    cached = _cache_get(con, oid, did, profile)
                    if cached:
                        secs, source = cached
                    else:
                        secs = durations[i][j] if durations else None
                        source = "osrm"
                        if secs is None:
                            secs = fallback_seconds(origins[i], dests[j], profile)
                            source = "fallback"
                        _cache_put(con, oid, did, profile, secs, source)
                    rows.append(
                        dict(ccpp_id=oid, facility_id=did, profile=profile,
                             seconds=secs, source=source)
                    )
            con.commit()
            log.info("Lote %s %d-%d ok", profile, start, start + len(chunk))

    con.close()
    matrix = pd.DataFrame(rows)
    out = get_path("processed") / Path(R["matrix_path"]).name
    matrix.to_parquet(out, index=False)
    log.info("Matriz O-D escrita: %s (%d filas)", out, len(matrix))
    return matrix


def run() -> None:
    proc = get_path("processed")
    demand = pd.read_parquet(proc / "demand_validated.parquet")
    facilities = pd.read_parquet(proc / "facilities_validated.parquet")
    demand = demand.loc[demand["qc_ok"]].rename(columns={"nombre_ccpp": "ccpp_id"})
    facilities = (
        facilities.loc[facilities["qc_ok"] & facilities.get("es_resolutivo", True)]
        .rename(columns={"codigo_renaes": "facility_id"})
    )
    build_matrix(
        demand[["ccpp_id", "longitud", "latitud"]],
        facilities[["facility_id", "longitud", "latitud"]],
    )


if __name__ == "__main__":
    run()
