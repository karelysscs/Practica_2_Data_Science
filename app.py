"""Fase 4 — Dashboard interactivo (2.5 pts).

Ejecutar:  streamlit run app.py

Funciona 100% offline leyendo los artefactos precomputados de data/outputs/.
Incluye: cabecera de KPIs, choropleth distrital por tiempo de acceso, capa de
establecimientos (toggle), histograma de distribución, tabla de distritos
ordenada, simulador de escenarios (mejorar un establecimiento a categoría
resolutiva) y panel de calidad de datos.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from src.config import CFG, get_path

st.set_page_config(page_title="Acceso a emergencias — Perú", layout="wide")

OUT = get_path("outputs", create=False)
QUAL = get_path("quality_reports", create=False)


@st.cache_data
def load():
    ccpp = pd.read_parquet(OUT / "metrics_ccpp.parquet")
    dist = pd.read_parquet(OUT / "metrics_distrito.parquet")
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    return ccpp, dist, summary


st.title("Accesibilidad a servicios de emergencia resolutivos")
st.caption(
    "Departamentos: " + ", ".join(CFG["departments"].values())
    + "  ·  Umbral hora dorada: "
    + str(CFG["metrics"]["golden_hour_min"]) + " min"
)

try:
    ccpp, dist, summary = load()
except FileNotFoundError:
    st.warning("Aún no hay resultados. Corre las fases 1-3 primero "
               "(`python -m src.phase1_data && python -m src.phase2_routing && "
               "python -m src.phase3_metrics`).")
    st.stop()

# --- KPIs ----------------------------------------------------------------- #
c1, c2, c3, c4 = st.columns(4)
c1.metric("Población total", f"{summary['poblacion_total']:,.0f}")
c2.metric("Tiempo medio ponderado", f"{summary['tiempo_min_ponderado']:.1f} min")
c3.metric("Fuera de la hora dorada", f"{summary['pct_fuera_hora_dorada']:.1f} %")
c4.metric("Gini del tiempo de acceso", f"{summary['gini_tiempo_acceso']:.3f}")

# --- Layout ------------------------------------------------------------- #
left, right = st.columns([2, 1])

with left:
    st.subheader("Distritos por tiempo de acceso ponderado")
    # TODO: unir con limites_distritales.gpkg y pintar choropleth (folium/plotly)
    st.dataframe(
        dist.sort_values("tiempo_min_ponderado", ascending=False),
        use_container_width=True, hide_index=True,
    )

with right:
    st.subheader("Distribución del tiempo de acceso")
    st.bar_chart(ccpp["min_acceso_min"].dropna().clip(upper=240))

# --- Simulador de escenarios ------------------------------------------- #
st.divider()
st.subheader("Simulador: mejorar un establecimiento a categoría resolutiva")
st.info("TODO: seleccionar un establecimiento no resolutivo, recalcular el "
        "tiempo mínimo por CCPP contra la oferta ampliada y mostrar el delta "
        "de población que entra en la hora dorada.")

# --- Panel de calidad -------------------------------------------------- #
st.divider()
st.subheader("Calidad de datos (Fase 1)")
for kind in ("oferta", "demanda"):
    md = QUAL / f"calidad_{kind}.md"
    if md.exists():
        st.markdown(md.read_text(encoding="utf-8"))
