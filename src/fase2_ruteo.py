"""Fase 2 — Ruteo y cálculo de tiempos de viaje (3.0 pts).

Motor primario: **OSRM** (`/table` con `sources`/`destinations`), servidor público
`router.project-osrm.org`. Una sola tanda de ~150 peticiones cubre los
11 080 × 24 pares O-D; se cachea en SQLite para reejecución offline.

  * ``driving`` : duración devuelta por OSRM (perfil car).
  * ``walking`` : el servidor público no expone perfil foot; se deriva de la
    **distancia** OSRM sobre la misma traza vial a velocidad peatonal
    (``config.md``). Es una aproximación conservadora y se documenta como tal.

Fallback documentado para pares sin ruta (``duration``/``distance`` nulos):
``t = haversine · detour_factor / velocidad_media``  (cota inferior, marcada en
la columna ``source``).

Motor alternativo: grafo OSM local (`osmnx` + `networkx`), seleccionable con
``routing.engine: osmnx`` en ``config.md`` (ver ``get_graph`` /
``matrix_via_graph``). Útil sin conexión pero su descarga desde Overpass para
3 departamentos es lenta.

Salida: ``data/processed/od_matrix.parquet``
    ccpp_id, facility_id, profile, seconds, minutes, meters, source
y reporte en ``reports/quality/ruteo.md``.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from src.config import CFG, get_path
from src.utils import get_logger, haversine_m, timestamp

log = get_logger("fase2")

R = CFG["routing"]
_OSRM_MAX_COORDS = 100           # límite del servidor público
_RETRY, _PAUSE = 4, 1.0          # reintentos y pausa de cortesía (s)


# ========================================================================= #
# Entradas
# ========================================================================= #
def load_inputs():
    proc = get_path("processed")
    fac = pd.read_parquet(proc / "facilities_validated.parquet")
    dem = pd.read_parquet(proc / "demand_validated.parquet")

    fac = fac.loc[fac["apto_oferta"]].copy()
    fac["facility_id"] = fac["codigo"].astype(str)
    dem = dem.loc[dem["apto_demanda"]].copy()
    dem["ccpp_id"] = dem["codigo"].astype(str)

    fac = fac.dropna(subset=["latitud", "longitud"]).drop_duplicates("facility_id")
    dem = dem.dropna(subset=["latitud", "longitud"]).drop_duplicates("ccpp_id")
    log.info("Entradas: %d establecimientos resolutivos, %d centros poblados",
             len(fac), len(dem))
    return dem.reset_index(drop=True), fac.reset_index(drop=True)


# ========================================================================= #
# Caché
# ========================================================================= #
def _cache() -> sqlite3.Connection:
    con = sqlite3.connect(get_path("processed") / Path(R["cache_path"]).name)
    con.execute(
        """CREATE TABLE IF NOT EXISTS od(
               ccpp_id TEXT, facility_id TEXT,
               duration_s REAL, distance_m REAL,
               PRIMARY KEY (ccpp_id, facility_id))"""
    )
    return con


# ========================================================================= #
# Motor OSRM
# ========================================================================= #
def _osrm_table(coords: list[tuple[float, float]], src_idx: list[int],
                dst_idx: list[int]) -> tuple[list, list]:
    cs = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in coords)
    url = (
        f"{R['osrm_base_url'].rstrip('/')}/table/v1/driving/{cs}"
        f"?sources={';'.join(map(str, src_idx))}"
        f"&destinations={';'.join(map(str, dst_idx))}"
        f"&annotations=duration,distance"
    )
    for attempt in range(1, _RETRY + 1):
        try:
            r = requests.get(url, timeout=90)
            if r.status_code == 429:
                time.sleep(_PAUSE * 2 * attempt)
                continue
            r.raise_for_status()
            j = r.json()
            if j.get("code") != "Ok":
                raise RuntimeError(j.get("code"))
            return j["durations"], j["distances"]
        except Exception as exc:  # noqa: BLE001
            log.warning("OSRM intento %d/%d falló: %s", attempt, _RETRY, exc)
            time.sleep(_PAUSE * attempt)
    return None, None


def matrix_via_osrm(dem: pd.DataFrame, fac: pd.DataFrame) -> pd.DataFrame:
    con = _cache()
    have = pd.read_sql("SELECT ccpp_id, facility_id, duration_s, distance_m FROM od", con)
    have_keys = set(zip(have["ccpp_id"], have["facility_id"]))
    log.info("Caché: %d pares ya resueltos", len(have_keys))

    fac_coords = list(zip(fac["longitud"], fac["latitud"]))
    n_fac = len(fac)
    chunk = min(R.get("max_table_sources", 75), _OSRM_MAX_COORDS - n_fac)
    pending = dem[~dem["ccpp_id"].isin({k for k, _ in have_keys})] if have_keys else dem

    for start in range(0, len(pending), chunk):
        part = pending.iloc[start:start + chunk]
        coords = fac_coords + list(zip(part["longitud"], part["latitud"]))
        src = list(range(n_fac, len(coords)))          # orígenes = CCPP
        dst = list(range(n_fac))                        # destinos = establecimientos
        dur, dist = _osrm_table(coords, src, dst)
        rows = []
        for i, cid in enumerate(part["ccpp_id"].to_numpy()):
            for j, fid in enumerate(fac["facility_id"].to_numpy()):
                d = dur[i][j] if dur else None
                m = dist[i][j] if dist else None
                rows.append((cid, fid, d, m))
        con.executemany("INSERT OR REPLACE INTO od VALUES (?,?,?,?)", rows)
        con.commit()
        log.info("OSRM %d/%d CCPP", min(start + chunk, len(pending)), len(pending))
        time.sleep(_PAUSE)

    od = pd.read_sql("SELECT * FROM od", con)
    con.close()
    return _finalize(od, dem, fac)


def _finalize(od: pd.DataFrame, dem: pd.DataFrame, fac: pd.DataFrame) -> pd.DataFrame:
    """De duración/distancia OSRM -> filas largas por perfil, con fallback."""
    coord = {r.facility_id: (r.longitud, r.latitud) for r in fac.itertuples()}
    dcoord = {r.ccpp_id: (r.longitud, r.latitud) for r in dem.itertuples()}
    v_drive = R["fallback_speed_kmh"]["driving"] / 3.6
    v_walk = R["fallback_speed_kmh"]["walking"] / 3.6
    k = R["detour_factor"]

    out = []
    for r in od.itertuples():
        clon, clat = dcoord.get(r.ccpp_id, (np.nan, np.nan))
        flon, flat = coord.get(r.facility_id, (np.nan, np.nan))
        hav = haversine_m(clon, clat, flon, flat)
        # driving
        if r.duration_s is not None and np.isfinite(r.duration_s):
            out.append((r.ccpp_id, r.facility_id, "driving", r.duration_s, r.distance_m, "osrm"))
        else:
            out.append((r.ccpp_id, r.facility_id, "driving", hav * k / v_drive, hav * k, "fallback"))
        # walking (de la distancia vial; si falta, haversine)
        if r.distance_m is not None and np.isfinite(r.distance_m):
            out.append((r.ccpp_id, r.facility_id, "walking", r.distance_m / v_walk, r.distance_m, "osrm_dist"))
        else:
            out.append((r.ccpp_id, r.facility_id, "walking", hav * k / v_walk, hav * k, "fallback"))

    m = pd.DataFrame(out, columns=["ccpp_id", "facility_id", "profile", "seconds", "meters", "source"])
    m["minutes"] = m["seconds"] / 60.0
    return m


# ========================================================================= #
# Motor alternativo: grafo OSM local (osmnx + networkx)
# ========================================================================= #
def _graph_path() -> Path:
    return get_path("processed") / "graph_drive.graphml"


def get_graph(force: bool = False):
    import osmnx as ox

    gp = _graph_path()
    if gp.exists() and not force:
        log.info("Grafo de caché: %s", gp)
        return ox.load_graphml(gp)
    depts = [f"{d.title()}, Peru" for d in CFG["departments"].values()]
    log.info("Descargando polígonos: %s", ", ".join(depts))
    gdf = ox.geocoder.geocode_to_gdf(depts)
    poly = gdf.geometry.union_all().buffer(0.03)
    log.info("Descargando red vial 'drive' (lento: Overpass, 3 dptos.)…")
    G = ox.graph_from_polygon(poly, network_type="drive", simplify=True)
    G = ox.routing.add_edge_speeds(G)
    G = ox.routing.add_edge_travel_times(G)
    ox.save_graphml(G, gp)
    log.info("Grafo: %d nodos, %d aristas", G.number_of_nodes(), G.number_of_edges())
    return G


def matrix_via_graph(dem: pd.DataFrame, fac: pd.DataFrame) -> pd.DataFrame:
    import networkx as nx
    import osmnx as ox

    G = get_graph()
    for _, _, d in G.edges(data=True):
        d["tt_walk"] = d.get("length", 0.0) / (R["fallback_speed_kmh"]["walking"] / 3.6)

    dnode, dsnap = ox.distance.nearest_nodes(
        G, X=list(dem["longitud"]), Y=list(dem["latitud"]), return_dist=True)
    fnode, fsnap = ox.distance.nearest_nodes(
        G, X=list(fac["longitud"]), Y=list(fac["latitud"]), return_dist=True)
    dnode, dsnap = np.asarray(dnode), np.asarray(dsnap)

    rows = []
    for prof, w in (("driving", "travel_time"), ("walking", "tt_walk")):
        sp = R["fallback_speed_kmh"][prof] / 3.6
        for k, fid in enumerate(fac["facility_id"].to_numpy()):
            L = nx.single_source_dijkstra_path_length(G, fnode[k], weight=w)
            base = np.array([L.get(n, np.inf) for n in dnode]) + fsnap[k] / sp + dsnap / sp
            for i, cid in enumerate(dem["ccpp_id"].to_numpy()):
                sec, src = base[i], "graph"
                if not np.isfinite(sec):
                    hav = haversine_m(dem["longitud"].iat[i], dem["latitud"].iat[i],
                                      fac["longitud"].iat[k], fac["latitud"].iat[k])
                    sec, src = hav * R["detour_factor"] / sp, "fallback"
                rows.append((cid, fid, prof, sec, np.nan, src))
            log.info("  [%s] %d/%d", prof, k + 1, len(fac))
    m = pd.DataFrame(rows, columns=["ccpp_id", "facility_id", "profile", "seconds", "meters", "source"])
    m["minutes"] = m["seconds"] / 60.0
    return m


# ========================================================================= #
def build_matrix() -> pd.DataFrame:
    dem, fac = load_inputs()
    engine = R.get("engine", "osrm").lower()
    log.info("Motor de ruteo: %s", engine)
    matrix = matrix_via_graph(dem, fac) if engine == "osmnx" else matrix_via_osrm(dem, fac)

    out = get_path("processed") / Path(R["matrix_path"]).name
    matrix.to_parquet(out, index=False)
    log.info("Matriz O-D: %s (%d filas)", out, len(matrix))
    _report(matrix, dem, fac, engine)
    return matrix


def _report(matrix: pd.DataFrame, dem, fac, engine: str) -> None:
    out = get_path("quality_reports")
    lines = [
        "# Reporte de ruteo — Fase 2", "",
        f"_Generado: {timestamp()}_", "",
        f"- Motor: **{engine}**  ·  perfiles: {', '.join(R['profiles'])}",
        f"- Pares O-D por perfil: {len(dem)} CCPP × {len(fac)} establecimientos "
        f"= {len(dem) * len(fac):,}", "",
        "| Perfil | % ruta real | % fallback | mediana min. acceso (min) |",
        "|---|---|---|---|",
    ]
    for p in R["profiles"]:
        sub = matrix[matrix["profile"] == p]
        nearest = sub.groupby("ccpp_id")["seconds"].min() / 60
        fb = sub["source"].eq("fallback").mean() * 100
        lines.append(f"| {p} | {100 - fb:.1f} | {fb:.1f} | {nearest.median():.1f} |")
    lines += ["", "> `walking` se estima de la distancia vial OSRM a velocidad "
              f"{R['fallback_speed_kmh']['walking']} km/h (el servidor público no "
              "tiene perfil peatonal). El `fallback` es una cota inferior."]
    (out / "ruteo.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("Reporte de ruteo -> %s", out / "ruteo.md")


def run() -> None:
    log.info("=== Fase 2: ruteo y matriz O-D ===")
    build_matrix()
    log.info("Fase 2 completa.")


if __name__ == "__main__":
    run()
