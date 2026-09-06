"""Fase 4 — Dashboard interactivo (2.5 pts).

    streamlit run app.py

Funciona 100 % offline con los artefactos precomputados de ``data/outputs/`` y la
matriz O-D versionada (``data/processed/od_matrix.parquet``).

Contiene: cabecera de KPIs, choropleth distrital por tiempo de acceso, capa de
establecimientos (toggle), histograma de la distribución, tabla de distritos
ordenable, simulador de escenarios (cerrar establecimientos / añadir uno nuevo) y
panel de calidad de datos.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from src.config import CFG, get_path
from src.utils import haversine_m

st.set_page_config(page_title="Acceso a emergencias — Perú", layout="wide")

OUT = get_path("outputs", create=False)
PROC = get_path("processed", create=False)
QUAL = get_path("quality_reports", create=False)
GOLDEN = CFG["metrics"]["golden_hour_min"]
BANDS = CFG["metrics"]["coverage_bands_min"]


@st.cache_data
def load():
    ccpp = pd.read_parquet(OUT / "metrics_ccpp.parquet")
    dist = pd.read_parquet(OUT / "metrics_distrito.parquet")
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    matrix = pd.read_parquet(PROC / "od_matrix.parquet")
    fac = pd.read_parquet(PROC / "facilities_validated.parquet")
    fac = fac.loc[fac["apto_oferta"]].copy()
    fac["facility_id"] = fac["codigo"].astype(str)
    gdf = None
    gpath = OUT / "metrics_distrito.gpkg"
    if gpath.exists():
        import geopandas as gpd
        gdf = gpd.read_file(gpath)
    return ccpp, dist, summary, matrix, fac, gdf


def weighted_bands(t: pd.Series, w: pd.Series) -> dict:
    tot = w.sum()
    d, prev = {}, 0
    for b in BANDS:
        d[f"≤ {b}"] = w[t <= b].sum() / tot * 100
        prev = b
    d[f"> {prev}"] = w[t > prev].sum() / tot * 100
    return d


st.title("Accesibilidad vial a servicios de emergencia resolutivos")
st.caption(
    f"Departamentos: {', '.join(CFG['departments'].values())}  ·  "
    f"Establecimientos II-1+  ·  Hora dorada = {GOLDEN} min  ·  "
    "Perfil: conducción"
)

try:
    ccpp, dist, summary, matrix, fac, gdf = load()
except FileNotFoundError as exc:
    st.warning(f"Faltan resultados ({exc}). Ejecuta las fases 1–3:\n\n"
               "`python -m src.phase1_data && python -m src.phase2_routing && "
               "python -m src.phase3_metrics`")
    st.stop()

ccpp = ccpp.drop_duplicates("ccpp_id").reset_index(drop=True)
drive = (matrix[matrix["profile"] == "driving"]
         .drop_duplicates(["ccpp_id", "facility_id"]).copy())
pop = ccpp.set_index("ccpp_id")["poblacion"] if "poblacion" in ccpp else None
tcol = "min_driving" if "min_driving" in ccpp.columns else "min_" + summary["perfil_primario"]

# ------------------------------------------------------------------ sidebar
st.sidebar.header("Simulador de escenarios")
mode = st.sidebar.radio("Escenario", ["Situación actual",
                                      "Cerrar establecimientos",
                                      "Añadir un establecimiento resolutivo"])

closed = []
added_latlon = None
if mode == "Cerrar establecimientos":
    opts = fac.assign(lbl=fac["nombre"] + " (" + fac["categoria"] + ", " +
                      fac["provincia"] + ")")
    closed = st.sidebar.multiselect("Establecimientos fuera de servicio",
                                    opts["facility_id"],
                                    format_func=lambda i: opts.set_index("facility_id")
                                    .loc[i, "lbl"])
elif mode == "Añadir un establecimiento resolutivo":
    d = st.sidebar.selectbox("Distrito donde se instala", sorted(dist["ubigeo_distrito"]))
    if gdf is not None and d in set(gdf["ubigeo_distrito"]):
        c = gdf.loc[gdf["ubigeo_distrito"] == d].geometry.iloc[0].representative_point()
        added_latlon = (c.y, c.x)
        st.sidebar.caption(f"Ubicación aproximada: {c.y:.3f}, {c.x:.3f} "
                           "(centroide distrital; delta estimado con distancia directa)")

# ------------------------------------------------------------------ recompute
def nearest_after(closed, added_latlon):
    d = drive[~drive["facility_id"].isin(closed)]
    best = d.groupby("ccpp_id")["minutes"].min()
    if added_latlon is not None:
        v = CFG["routing"]["fallback_speed_kmh"]["driving"] / 60  # km/min
        k = CFG["routing"]["detour_factor"]
        cc = ccpp.set_index("ccpp_id")
        est = cc.apply(lambda r: haversine_m(r["longitud"], r["latitud"],
                                             added_latlon[1], added_latlon[0]) / 1000 * k / v,
                       axis=1)
        best = pd.concat([best, est], axis=1).min(axis=1)
    return best


base_best = nearest_after([], None)
scen_best = nearest_after(closed, added_latlon)

w = pop.reindex(base_best.index).fillna(1) if pop is not None else pd.Series(1, index=base_best.index)
pct_out_base = (w[base_best > GOLDEN].sum() / w.sum()) * 100
pct_out_scen = (w[scen_best > GOLDEN].sum() / w.sum()) * 100
mean_base = np.average(base_best, weights=w)
mean_scen = np.average(scen_best, weights=w)

# ------------------------------------------------------------------ KPIs
c1, c2, c3, c4 = st.columns(4)
c1.metric("Centros poblados", f"{len(base_best):,}")
c2.metric("Establecimientos resolutivos", int(summary["n_establecimientos_resolutivos"]) - len(closed),
          delta=(-len(closed) or None))
c3.metric("Tiempo medio de acceso", f"{mean_scen:.1f} min",
          delta=f"{mean_scen - mean_base:+.1f}", delta_color="inverse")
c4.metric(f"Población fuera de la hora dorada", f"{pct_out_scen:.1f} %",
          delta=f"{pct_out_scen - pct_out_base:+.1f} pp", delta_color="inverse")

left, right = st.columns([3, 2])

# ------------------------------------------------------------------ choropleth
with left:
    st.subheader("Tiempo de acceso por distrito")
    dsc = (scen_best.rename("min_scen").reset_index()
           .merge(ccpp[["ccpp_id", "ubigeo_distrito"]], on="ccpp_id"))
    dsc["w"] = w.reindex(dsc["ccpp_id"]).values
    dmet = (dsc.groupby("ubigeo_distrito")
            .apply(lambda g: np.average(g["min_scen"], weights=g["w"]), include_groups=False)
            .rename("acceso_min").reset_index())
    if gdf is not None:
        import folium
        from streamlit_folium import st_folium
        gg = gdf.merge(dmet, on="ubigeo_distrito", how="left")
        m = folium.Map(location=[-6.5, -78.0], zoom_start=6, tiles="OpenStreetMap")
        folium.Choropleth(
            geo_data=gg.to_json(), data=gg, columns=["ubigeo_distrito", "acceso_min"],
            key_on="feature.properties.ubigeo_distrito", fill_color="YlOrRd",
            nan_fill_color="lightgray", legend_name="Minutos al hospital resolutivo",
        ).add_to(m)
        show_fac = st.checkbox("Mostrar establecimientos", value=True)
        if show_fac:
            live = fac[~fac["facility_id"].isin(closed)]
            for r in live.itertuples():
                folium.CircleMarker([r.latitud, r.longitud], radius=4, color="#1f3b70",
                                    fill=True, fill_opacity=0.9,
                                    tooltip=f"{r.nombre} ({r.categoria})").add_to(m)
            if added_latlon:
                folium.Marker(added_latlon, icon=folium.Icon(color="green", icon="plus"),
                              tooltip="Establecimiento simulado").add_to(m)
        st_folium(m, height=480, use_container_width=True)
    else:
        st.info("Falta `data/outputs/metrics_distrito.gpkg` (se genera en la Fase 3 "
                "con `peru_distritos.geojson`). Se muestra tabla en su lugar.")
        st.dataframe(dmet.sort_values("acceso_min", ascending=False), hide_index=True,
                     use_container_width=True)

# ------------------------------------------------------------------ right column
with right:
    st.subheader("Distribución del tiempo de acceso")
    import plotly.express as px
    fig = px.histogram(scen_best.clip(upper=240), nbins=40,
                       labels={"value": "minutos", "count": "centros poblados"})
    fig.add_vline(x=GOLDEN, line_dash="dash", line_color="red")
    fig.update_layout(showlegend=False, height=260, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Cobertura ponderada por población")
    st.dataframe(pd.Series(weighted_bands(scen_best, w)).round(1).rename("% población")
                 .to_frame(), use_container_width=True)

# ------------------------------------------------------------------ ranked table
st.divider()
st.subheader("Distritos con peor acceso")
tbl = dmet.merge(dist, on="ubigeo_distrito", how="left").sort_values("acceso_min", ascending=False)
show = [c for c in ["ubigeo_distrito", "departamento", "n_ccpp", "poblacion",
                    "acceso_min", "pct_fuera_hora_dorada", "gini_acceso"] if c in tbl.columns]
st.dataframe(tbl[show].head(20), hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ quality
st.divider()
with st.expander("Panel de calidad de datos (Fase 1)"):
    for kind in ("oferta", "demanda", "ruteo"):
        md = QUAL / (f"calidad_{kind}.md" if kind != "ruteo" else "ruteo.md")
        if md.exists():
            st.markdown(md.read_text(encoding="utf-8"))
            st.divider()
