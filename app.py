"""Fase 4 — Dashboard interactivo (2.5 pts).

    streamlit run app.py

Funciona 100 % offline con los artefactos precomputados de ``data/outputs/`` y la
matriz O-D versionada (``data/processed/od_matrix.parquet``).

Contiene: cabecera de KPIs, choropleth distrital con tooltip, capa de
establecimientos de salud (toggle), histograma de la distribución, análisis de
equidad (Gini + acceso por ámbito urbano/rural, cuartil de pobreza y tercil de
altitud), tabla de la oferta, tabla de distritos, simulador de escenarios
(cerrar / añadir) con resumen de impacto, y panel de calidad de datos.
Todos los indicadores se recalculan según el escenario seleccionado.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from src.config import CFG, get_path

st.set_page_config(page_title="Acceso a emergencias — Perú", layout="wide")

OUT = get_path("outputs", create=False)
PROC = get_path("processed", create=False)
QUAL = get_path("quality_reports", create=False)
GOLDEN = CFG["metrics"]["golden_hour_min"]
BANDS = CFG["metrics"]["coverage_bands_min"]
_R_EARTH = 6_371_000.0


# ------------------------------------------------------------------ helpers
def fmt(x, dec: int = 1) -> str:
    """Número con separador de miles '.' y decimal ',' (convención es-PE)."""
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    s = f"{x:,.{dec}f}"
    return s.replace(",", " ").replace(".", ",").replace(" ", ".")


def weighted_bands(t: pd.Series, w: pd.Series) -> dict:
    tot = w.sum()
    d, prev = {}, 0
    for b in BANDS:
        d[f"≤ {b} min"] = w[t <= b].sum() / tot * 100
        prev = b
    d[f"> {prev} min"] = w[t > prev].sum() / tot * 100
    return d


def weighted_gini(v: pd.Series, w: pd.Series) -> float:
    v, w = np.asarray(v, float), np.asarray(w, float)
    m = np.isfinite(v) & np.isfinite(w) & (w > 0)
    v, w = v[m], w[m]
    if v.size == 0 or v.sum() == 0:
        return float("nan")
    o = np.argsort(v)
    v, w = v[o], w[o]
    cw, cvw = np.cumsum(w), np.cumsum(v * w)
    return float(1 - np.sum((cvw[1:] + cvw[:-1]) * np.diff(cw)) / (cvw[-1] * cw[-1]))


def haversine_vec(lon, lat, lon0, lat0):
    """Distancia (m) de un vector de puntos a un punto fijo (grados decimales)."""
    p1, p2 = np.radians(lat), np.radians(lat0)
    dphi, dlmb = np.radians(lat0 - lat), np.radians(lon0 - lon)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * _R_EARTH * np.arcsin(np.sqrt(a))


@st.cache_data
def load():
    ccpp = pd.read_parquet(OUT / "metrics_ccpp.parquet").drop_duplicates("ccpp_id")
    dist = pd.read_parquet(OUT / "metrics_distrito.parquet")
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    matrix = pd.read_parquet(PROC / "od_matrix.parquet")
    matrix = matrix[matrix["profile"] == "driving"].drop_duplicates(["ccpp_id", "facility_id"])
    fac = pd.read_parquet(PROC / "facilities_validated.parquet")
    fac = fac.loc[fac["apto_oferta"]].copy()
    fac["facility_id"] = fac["codigo"].astype(str)
    gjson = None
    gpath = OUT / "metrics_distrito.gpkg"
    if gpath.exists():
        import geopandas as gpd
        gjson = gpd.read_file(gpath)
    return ccpp.reset_index(drop=True), dist, summary, matrix, fac, gjson


# ================================================================ carga
st.title("Accesibilidad vial a servicios de emergencia resolutivos")
st.caption(
    f"Departamentos: {', '.join(CFG['departments'].values())}  ·  "
    f"Establecimientos de salud resolutivos (categoría II-1+)  ·  "
    f"Hora dorada = {GOLDEN} min  ·  Perfil: conducción  ·  "
    "Fuentes: RENIPRESS ago-2026, Censo 2017, OSM/OSRM"
)

try:
    ccpp, dist, summary, drive, fac, gjson = load()
except FileNotFoundError as exc:
    st.warning(f"Faltan resultados ({exc}). Ejecuta las fases 1–3:\n\n"
               "`python -m src.fase1_datos && python -m src.fase2_ruteo && "
               "python -m src.fase3_metricas`")
    st.stop()

with st.expander("ℹ️  Cómo leer este panel"):
    st.markdown(
        f"""
- **Unidad de demanda:** centro poblado (Censo 2017), ponderado por su población.
- **Acceso:** tiempo de viaje **por carretera** (OSRM) al establecimiento de
  salud resolutivo —categoría II-1 o superior— **más cercano**.
- **Hora dorada:** umbral de **{GOLDEN} min**; "fuera de la hora dorada" =
  población cuyo acceso supera ese tiempo.
- **Gini de acceso:** 0 = todos tardan lo mismo; cuanto más alto, más desigual.
- **Simulador (barra lateral):** *Cerrar* recalcula con la matriz real de rutas;
  *Añadir* estima el efecto de un nuevo establecimiento con distancia directa
  desde el centroide del distrito (aproximación). Todos los números de esta
  página se actualizan con el escenario; las columnas marcadas
  *(sit. actual)* son de referencia y no cambian.
"""
    )

pop = ccpp.set_index("ccpp_id")["poblacion"] if "poblacion" in ccpp else None

# ================================================================ simulador
st.sidebar.header("Simulador de escenarios")
mode = st.sidebar.radio("Escenario", ["Situación actual",
                                      "Cerrar establecimientos",
                                      "Añadir un establecimiento resolutivo"])
closed, added_latlon, added_txt = [], None, ""
if mode == "Cerrar establecimientos":
    opts = fac.assign(lbl=fac["nombre"] + "  ·  " + fac["categoria"] + "  ·  " + fac["provincia"])
    closed = st.sidebar.multiselect(
        "Establecimientos fuera de servicio", opts["facility_id"],
        format_func=lambda i: opts.set_index("facility_id").loc[i, "lbl"])
elif mode == "Añadir un establecimiento resolutivo":
    _dname = (dist.set_index("ubigeo_distrito")["distrito"].to_dict()
              if "distrito" in dist.columns else {})
    _dprov = (dist.set_index("ubigeo_distrito")["provincia"].to_dict()
              if "provincia" in dist.columns else {})
    sel = st.sidebar.selectbox(
        "Distrito donde se instala",
        sorted(dist["ubigeo_distrito"], key=lambda u: (_dprov.get(u, ""), _dname.get(u, u))),
        format_func=lambda u: f"{_dname.get(u, u)} — {_dprov.get(u, '')} ({u})".strip())
    if gjson is not None and sel in set(gjson["ubigeo_distrito"]):
        c = gjson.loc[gjson["ubigeo_distrito"] == sel].geometry.iloc[0].representative_point()
        added_latlon = (c.y, c.x)
        added_txt = dist.set_index("ubigeo_distrito").loc[sel, "distrito"] \
            if "distrito" in dist.columns else sel
        st.sidebar.caption(f"Ubicación aproximada: {c.y:.3f}, {c.x:.3f} "
                           "(centroide distrital; delta estimado con distancia directa).")

scenario_active = bool(closed) or added_latlon is not None

# ------------------------------------------------------------------ recompute
_lon = ccpp["longitud"].to_numpy()
_lat = ccpp["latitud"].to_numpy()
_ids = ccpp["ccpp_id"].to_numpy()


def nearest(closed, added_latlon) -> pd.Series:
    sub = drive[~drive["facility_id"].isin(closed)]
    best = sub.groupby("ccpp_id")["minutes"].min().reindex(_ids)
    if added_latlon is not None:
        v = CFG["routing"]["fallback_speed_kmh"]["driving"] / 60.0     # km/min
        k = CFG["routing"]["detour_factor"]
        est = pd.Series(
            haversine_vec(_lon, _lat, added_latlon[1], added_latlon[0]) / 1000.0 * k / v,
            index=_ids)
        best = pd.concat([best, est], axis=1).min(axis=1)
    return best


base_best = nearest([], None)
scen_best = nearest(closed, added_latlon)
w = (pop.reindex(_ids).fillna(0) if pop is not None
     else pd.Series(1.0, index=_ids))

pct_out_base = w[base_best > GOLDEN].sum() / w.sum() * 100
pct_out_scen = w[scen_best > GOLDEN].sum() / w.sum() * 100
mean_base = float(np.average(base_best, weights=w))
mean_scen = float(np.average(scen_best, weights=w))
ppl_out_base = float(w[base_best > GOLDEN].sum())
ppl_out_scen = float(w[scen_best > GOLDEN].sum())

# ================================================================ KPIs
d_estab = -len(closed) + (1 if added_latlon is not None else 0)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Centros poblados", fmt(len(base_best), 0))
c2.metric("Establecimientos de salud",
          int(summary["n_establecimientos_resolutivos"]) + d_estab,
          delta=(d_estab or None))
c3.metric("Tiempo medio de acceso", f"{fmt(mean_scen)} min",
          delta=f"{fmt(mean_scen - mean_base)} min" if scenario_active else None,
          delta_color="inverse")
c4.metric("Población fuera de la hora dorada", f"{fmt(pct_out_scen)} %",
          delta=f"{fmt(pct_out_scen - pct_out_base)} pp" if scenario_active else None,
          delta_color="inverse")

# ------------------------------------------------------------------ impacto
if scenario_active:
    diff = ppl_out_scen - ppl_out_base
    verbo = "deja fuera de la hora dorada a" if diff >= 0 else "incorpora a la hora dorada a"
    if closed:
        quien = f"Cerrar {len(closed)} establecimiento(s)"
    else:
        quien = f"Añadir un establecimiento resolutivo en {added_txt}"
    box = st.warning if diff >= 0 else st.success
    box(f"**{quien}** {verbo} **{fmt(abs(diff), 0)} personas** "
        f"({fmt(pct_out_scen - pct_out_base)} pp) y "
        f"{'sube' if mean_scen >= mean_base else 'baja'} el tiempo medio de acceso "
        f"en {fmt(abs(mean_scen - mean_base))} min.")

# ================================================================ mapa + distribución
# métricas distritales del escenario
_dsc = pd.DataFrame({"ccpp_id": _ids, "min": scen_best.to_numpy(), "w": w.to_numpy()})
_dsc = _dsc.merge(ccpp[["ccpp_id", "ubigeo_distrito"]], on="ccpp_id")
dmet = (_dsc.groupby("ubigeo_distrito")
        .apply(lambda g: pd.Series({
            "acceso_min": np.average(g["min"], weights=g["w"]) if g["w"].sum() else np.nan,
            "pct_out": (g["w"][g["min"] > GOLDEN].sum() / g["w"].sum() * 100)
            if g["w"].sum() else np.nan,
        }), include_groups=False).reset_index())

left, right = st.columns([3, 2])
with left:
    st.subheader("Tiempo de acceso por distrito")
    if gjson is not None:
        import branca.colormap as cm
        import folium
        from streamlit_folium import st_folium

        gg = gjson.merge(dmet, on="ubigeo_distrito", how="left")
        gg["acc_txt"] = gg["acceso_min"].map(lambda x: f"{x:.0f} min" if pd.notna(x) else "s/d")
        gg["out_txt"] = gg["pct_out"].map(lambda x: f"{x:.0f} %" if pd.notna(x) else "s/d")
        vals = gg["acceso_min"].dropna()
        vmax = float(np.ceil(vals.quantile(0.97) / 30) * 30) if len(vals) else 120
        cmap = cm.linear.YlOrRd_09.scale(float(vals.min()) if len(vals) else 0, vmax)
        cmap.caption = "Minutos al establecimiento resolutivo (tope al p97)"

        def _style(feat):
            v = feat["properties"]["acceso_min"]
            return {"fillColor": cmap(min(v, vmax)) if v is not None else "#e0e0e0",
                    "color": "white", "weight": 0.4, "fillOpacity": 0.8}

        x0, y0, x1, y1 = gg.total_bounds
        m = folium.Map(tiles="OpenStreetMap")
        m.fit_bounds([[y0, x0], [y1, x1]])
        folium.GeoJson(
            gg.to_json(), style_function=_style,
            highlight_function=lambda _f: {"weight": 2, "color": "#333"},
            tooltip=folium.GeoJsonTooltip(
                fields=["distrito", "provincia", "acc_txt", "out_txt"],
                aliases=["Distrito", "Provincia", "Acceso medio", "Fuera hora dorada"],
                sticky=True),
        ).add_to(m)
        cmap.add_to(m)

        if st.checkbox("Mostrar establecimientos", value=True):
            live = fac[~fac["facility_id"].isin(closed)]
            for r in live.itertuples():
                folium.CircleMarker(
                    [r.latitud, r.longitud], radius=4, color="#1f3b70", fill=True,
                    fill_opacity=0.9,
                    tooltip=f"{r.nombre} · {r.categoria} · {r.institucion}").add_to(m)
            if added_latlon:
                folium.Marker(added_latlon, icon=folium.Icon(color="green", icon="plus"),
                              tooltip=f"Establecimiento simulado · {added_txt}").add_to(m)
        st_folium(m, height=480, width='stretch', returned_objects=[])
    else:
        st.info("Falta `data/outputs/metrics_distrito.gpkg` (Fase 3). Se muestra tabla.")
        st.dataframe(dmet.sort_values("acceso_min", ascending=False), hide_index=True,
                     width='stretch')

with right:
    st.subheader("Distribución del tiempo de acceso")
    import plotly.express as px

    CAP = 240
    n_over = int((scen_best > CAP).sum())
    fig = px.histogram(scen_best[scen_best <= CAP], nbins=40,
                       labels={"value": "minutos", "count": "centros poblados"})
    fig.add_vline(x=GOLDEN, line_dash="dash", line_color="red",
                  annotation_text="hora dorada")
    fig.update_layout(showlegend=False, height=250, margin=dict(l=0, r=0, t=10, b=0),
                      yaxis_title="centros poblados")
    st.plotly_chart(fig, width='stretch')
    st.caption(f"{n_over} centros poblados superan {CAP} min y quedan fuera del eje.")

    st.subheader("Cobertura ponderada por población")
    cov = (pd.Series(weighted_bands(scen_best, w)).round(1)
           .rename("% población").to_frame())
    st.dataframe(cov, width='stretch',
                 column_config={"% población": st.column_config.NumberColumn(format="%.1f %%")})

# ================================================================ equidad
st.divider()
st.subheader("Análisis de equidad")
st.caption("Tiempo medio de acceso ponderado por población, recalculado según el escenario.")

eq = ccpp[["ccpp_id"]].copy()
for col in ("ambito_uro", "pobreza_cuartil", "altitud_tercil"):
    if col in ccpp.columns:
        eq[col] = ccpp[col].astype("string").values
eq["min"] = eq["ccpp_id"].map(scen_best)
eq["pob"] = eq["ccpp_id"].map(w)
eq = eq.dropna(subset=["min", "pob"])

_QSHORT = {"Q1 (menos pobre)": "Q1\nmenos pobre", "Q4 (más pobre)": "Q4\nmás pobre"}


def _wm_by(col: str, orden=None) -> pd.DataFrame:
    g = eq.dropna(subset=[col])
    r = (g.groupby(col, observed=True)
           .apply(lambda x: np.average(x["min"], weights=x["pob"]), include_groups=False)
           .rename("min").reset_index())
    if orden:
        r[col] = pd.Categorical(r[col], categories=orden, ordered=True)
        r = r.sort_values(col)
    r["lbl"] = r[col].astype(str).map(lambda s: _QSHORT.get(s, s))
    return r


def _bar(df, titulo):
    import plotly.express as px
    fig = px.bar(df, x="lbl", y="min", text_auto=".0f",
                 color="min", color_continuous_scale="YlOrRd")
    fig.add_hline(y=GOLDEN, line_dash="dash", line_color="red")
    fig.update_layout(height=250, showlegend=False, coloraxis_showscale=False,
                      title=titulo, margin=dict(l=0, r=0, t=34, b=0),
                      xaxis_title=None, yaxis_title="min")
    return fig


def _val(df, key) -> float:
    m = df[df["min"].notna()]
    hit = m[m[df.columns[0]].astype(str).str.contains(key, case=False, na=False)]
    return float(hit["min"].iloc[0]) if len(hit) else float("nan")


e1, e2, e3, e4 = st.columns([1, 1.25, 1.5, 1.25])
with e1:
    st.metric("Gini de acceso", fmt(weighted_gini(scen_best, w), 3))
    st.caption(
        f"Correlación con % de pobreza: r = {summary.get('correlacion_acceso_pobreza', '—')}"
        f"  \nCorrelación con IDH: r = {summary.get('correlacion_acceso_idh', '—')}"
    )
with e2:
    if "ambito_uro" in eq.columns:
        d = _wm_by("ambito_uro", ["urbano", "rural"])
        st.plotly_chart(_bar(d, "Urbano vs. rural"), width='stretch')
        u, r = _val(d, "urbano"), _val(d, "rural")
        if np.isfinite(u) and np.isfinite(r) and u > 0:
            st.caption(f"**Resumen:** la poblacion rural tarda **{r/u:.1f} veces** lo "
                       f"que la urbana ({r:.0f} vs {u:.0f} min). Sin vehiculo, la "
                       "emergencia rural es inalcanzable.")
with e3:
    if "pobreza_cuartil" in eq.columns:
        d = _wm_by("pobreza_cuartil",
                   ["Q1 (menos pobre)", "Q2", "Q3", "Q4 (más pobre)"])
        st.plotly_chart(_bar(d, "Por cuartil de pobreza distrital"), width='stretch')
        q1, q4 = _val(d, "Q1"), _val(d, "Q4")
        if np.isfinite(q1) and np.isfinite(q4) and q1 > 0:
            st.caption(f"**Resumen:** gradiente monotono. Del cuartil menos pobre al mas "
                       f"pobre el acceso pasa de **{q1:.0f}** a **{q4:.0f} min** "
                       f"(**{q4/q1:.1f}x**): la carencia se concentra donde ya hay "
                       "menos recursos.")
with e4:
    if "altitud_tercil" in eq.columns:
        d = _wm_by("altitud_tercil", ["bajo", "medio", "alto"])
        st.plotly_chart(_bar(d, "Por tercil de altitud"), width='stretch')
        lo, hi = _val(d, "bajo"), _val(d, "alto")
        if np.isfinite(lo) and np.isfinite(hi):
            st.caption(f"**Resumen:** el acceso empeora con la altura (**{lo:.0f} a "
                       f"{hi:.0f} min** del tercil bajo al alto). La altitud opera como "
                       "proxy de una red vial mas lenta y sinuosa.")

# ================================================================ oferta
st.divider()
with st.expander(f"Oferta: {len(fac)} establecimientos de salud resolutivos"):
    sup = (fac[["nombre", "categoria", "institucion", "departamento", "provincia", "distrito"]]
           .rename(columns=str.capitalize)
           .sort_values(["Departamento", "Provincia", "Nombre"]))
    st.dataframe(sup, hide_index=True, width='stretch')

# ================================================================ tabla distritos
st.divider()
st.subheader("Distritos con peor acceso (escenario actual)")
tbl = dmet.merge(dist, on="ubigeo_distrito", how="left").sort_values("acceso_min", ascending=False)
tbl = tbl.rename(columns={
    "ubigeo_distrito": "UBIGEO", "departamento": "Departamento", "provincia": "Provincia",
    "distrito": "Distrito", "n_ccpp": "CCPP", "poblacion": "Población",
    "acceso_min": "Acceso (min)", "pct_out": "Fuera HD %",
    "pct_fuera_hora_dorada": "Fuera HD % (sit. actual)",
    "gini_acceso": "Gini (sit. actual)", "pct_pobreza_total": "Pobreza %",
})
cols = [c for c in ["UBIGEO", "Departamento", "Provincia", "Distrito", "CCPP", "Población",
                    "Acceso (min)", "Fuera HD %", "Gini (sit. actual)", "Pobreza %"]
        if c in tbl.columns]
st.dataframe(
    tbl[cols].head(20), hide_index=True, width='stretch',
    column_config={
        "Población": st.column_config.NumberColumn(format="%d"),
        "Acceso (min)": st.column_config.NumberColumn(format="%.0f"),
        "Fuera HD %": st.column_config.NumberColumn(format="%.0f %%"),
        "Gini (sit. actual)": st.column_config.NumberColumn(format="%.2f"),
        "Pobreza %": st.column_config.NumberColumn(format="%.1f %%"),
    })

# ================================================================ calidad
st.divider()
with st.expander("Panel de calidad de datos (Fase 1)"):
    for kind in ("oferta", "demanda", "ruteo"):
        md = QUAL / (f"calidad_{kind}.md" if kind != "ruteo" else "ruteo.md")
        if md.exists():
            st.markdown(md.read_text(encoding="utf-8"))
            st.divider()
