"""Genera las figuras y tablas del informe (Fase 5) a partir de data/outputs/.

    python -m src.figures

Escribe PNG (300 dpi) y fragmentos .tex en data/outputs/figs/ y data/outputs/tabs/.
"""
from __future__ import annotations

import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.config import CFG, get_path
from src.utils import get_logger

log = get_logger("figures")

GOLDEN = CFG["metrics"]["golden_hour_min"]
BANDS = CFG["metrics"]["coverage_bands_min"]
INK = "#1f3b70"
ACCENT = "#c1121f"

plt.rcParams.update({
    "figure.dpi": 300, "savefig.dpi": 300, "font.size": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.titleweight": "bold", "figure.autolayout": True,
})


def _out():
    figs = get_path("outputs") / "figs"
    tabs = get_path("outputs") / "tabs"
    figs.mkdir(exist_ok=True)
    tabs.mkdir(exist_ok=True)
    return figs, tabs


def _wmean(v, w):
    m = np.isfinite(v) & np.isfinite(w) & (w > 0)
    return float(np.average(v[m], weights=w[m])) if m.any() else float("nan")


def build() -> None:
    out = get_path("outputs", create=False)
    figs, tabs = _out()
    ccpp = pd.read_parquet(out / "metrics_ccpp.parquet")
    dist = pd.read_parquet(out / "metrics_distrito.parquet")
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    t = ccpp["min_driving"]
    w = ccpp["poblacion"].fillna(0)
    dep_col = "departamento" if "departamento" in ccpp.columns else "dep"

    # --- Fig 1: histograma del tiempo de acceso -------------------------- #
    fig, ax = plt.subplots(figsize=(5.4, 3.2))
    ax.hist(t.clip(upper=240), bins=40, color=INK, alpha=0.85)
    ax.axvline(GOLDEN, color=ACCENT, ls="--", lw=1.5, label=f"Hora dorada ({GOLDEN} min)")
    ax.set_xlabel("Tiempo al hospital resolutivo más cercano (min, en coche)")
    ax.set_ylabel("N.º de centros poblados")
    ax.set_title("Distribución del tiempo de acceso")
    ax.legend(frameon=False)
    fig.savefig(figs / "fig_hist_acceso.png"); plt.close(fig)

    # --- Fig 2: bandas de cobertura ponderadas ------------------------- #
    bands = summary["cobertura_bandas_pct"]
    fig, ax = plt.subplots(figsize=(5.4, 3.0))
    ax.bar(list(bands), list(bands.values()), color=INK)
    for i, v in enumerate(bands.values()):
        ax.text(i, v + 1, f"{v:.0f}%", ha="center", fontsize=9)
    ax.set_ylabel("% de población")
    ax.set_title("Cobertura poblacional por banda de tiempo")
    ax.set_ylim(0, 100)
    fig.savefig(figs / "fig_cobertura.png"); plt.close(fig)

    # --- Fig 3: acceso medio por departamento -------------------------- #
    d = summary["por_departamento"]
    fig, ax = plt.subplots(figsize=(5.4, 3.0))
    names = list(d)
    vals = [d[k]["acceso_min_medio"] for k in names]
    ax.barh(names, vals, color=INK)
    for i, v in enumerate(vals):
        ax.text(v + 1, i, f"{v:.0f}", va="center", fontsize=9)
    ax.axvline(GOLDEN, color=ACCENT, ls="--", lw=1.2)
    ax.set_xlabel("Tiempo medio de acceso, ponderado por población (min)")
    ax.set_title("Acceso por departamento")
    fig.savefig(figs / "fig_departamento.png"); plt.close(fig)

    # --- Fig 4: acceso por tercil de altitud + dispersión ------------- #
    fig, ax = plt.subplots(figsize=(5.4, 3.2))
    if "altitud" in ccpp.columns and ccpp["altitud"].notna().any():
        ax.scatter(ccpp["altitud"], t.clip(upper=300), s=6, alpha=0.25, color=INK)
        ax.set_xlabel("Altitud (msnm)")
        ax.set_ylabel("Tiempo de acceso (min)")
        ax.set_title("Acceso frente a altitud")
        ax.axhline(GOLDEN, color=ACCENT, ls="--", lw=1.2)
    fig.savefig(figs / "fig_altitud.png"); plt.close(fig)

    # --- Fig 5: choropleth distrital estático ------------------------- #
    gpath = out / "metrics_distrito.gpkg"
    if gpath.exists():
        import geopandas as gpd
        g = gpd.read_file(gpath)
        col = "acceso_min_ponderado" if "acceso_min_ponderado" in g.columns else "acceso_min"
        fig, ax = plt.subplots(figsize=(5.6, 5.2))
        g.plot(column=col, cmap="YlOrRd", legend=True, ax=ax,
               edgecolor="white", linewidth=0.3,
               missing_kwds={"color": "lightgray"},
               legend_kwds={"label": "Minutos al hospital resolutivo", "shrink": 0.6})
        ax.set_axis_off()
        ax.set_title("Tiempo de acceso por distrito", fontweight="bold")
        fig.savefig(figs / "fig_choropleth.png", bbox_inches="tight"); plt.close(fig)

    # --- Tablas .tex ------------------------------------------------- #
    def _clean(s: str) -> str:
        s = s.replace("<=", r"$\le$").replace(">=", r"$\ge$").replace("> ", r"$>$ ")
        s = s.replace("_", r"\_")
        # escapar % solo si no está ya escapado
        return s.replace(r"\%", "%").replace("%", r"\%")

    def _tex(df: pd.DataFrame, name: str, caption: str, label: str, float_fmt="%.1f"):
        d = df.copy()
        d.columns = [_clean(str(c)) for c in d.columns]
        for c in d.columns:
            if not pd.api.types.is_numeric_dtype(d[c]):
                d[c] = d[c].map(lambda x: _clean(str(x)))
        body = d.to_latex(index=False, escape=False, float_format=float_fmt,
                          column_format="l" + "r" * (d.shape[1] - 1))
        (tabs / name).write_text(
            "% auto-generado por src/figures.py — no editar a mano\n"
            f"\\begin{{table}}[htbp]\\centering\n\\caption{{{caption}}}\\label{{{label}}}\n"
            f"{body}\\end{{table}}\n", encoding="utf-8")

    cov = pd.DataFrame({"Banda": list(bands), "Poblacion (\\%)": list(bands.values())})
    _tex(cov, "tab_cobertura.tex", "Cobertura poblacional por banda de tiempo de acceso "
         "(perfil conducci\\'on).", "tab:cobertura")

    dep = pd.DataFrame([
        {"Departamento": k, "CCPP": v["n_ccpp"],
         "Acceso medio (min)": v["acceso_min_medio"],
         "Fuera hora dorada (\\%)": v["pct_fuera_hora_dorada"]}
        for k, v in summary["por_departamento"].items()
    ])
    _tex(dep, "tab_departamento.tex",
         "Resumen por departamento: tiempo medio de acceso ponderado por poblaci\\'on "
         "y poblaci\\'on fuera de la hora dorada.", "tab:departamento")

    peores = (dist.sort_values("acceso_min_ponderado", ascending=False)
              .head(10)[["ubigeo_distrito", dep_col, "n_ccpp", "acceso_min_ponderado",
                         "pct_fuera_hora_dorada", "gini_acceso", "pct_pobreza_total"]]
              .rename(columns={"ubigeo_distrito": "UBIGEO", dep_col: "Depto.",
                               "n_ccpp": "CCPP", "acceso_min_ponderado": "Acceso (min)",
                               "pct_fuera_hora_dorada": "Fuera HD (\\%)",
                               "gini_acceso": "Gini", "pct_pobreza_total": "Pobreza (\\%)"}))
    _tex(peores, "tab_distritos_peores.tex",
         "Diez distritos con mayor tiempo de acceso ponderado por poblaci\\'on.",
         "tab:peores", float_fmt="%.2f")

    for k in ("oferta", "demanda"):
        csv = get_path("quality_reports") / f"calidad_{k}.csv"
        if csv.exists():
            q = pd.read_csv(csv)
            q.columns = ["Regla", "Registros", "\\%"]
            _tex(q, f"tab_calidad_{k}.tex",
                 f"Reglas de validaci\\'on y registros marcados --- {k}.", f"tab:calidad-{k}",
                 float_fmt="%.2f")

    # --- copiar a report/ para que el .tex sea autocontenido (Overleaf) --- #
    import shutil
    from src.config import ROOT
    for sub, src_dir in (("figs", figs), ("tabs", tabs)):
        dst = ROOT / "report" / sub
        dst.mkdir(parents=True, exist_ok=True)
        for f in src_dir.iterdir():
            shutil.copy2(f, dst / f.name)

    log.info("Figuras/tablas -> %s, %s  y copiadas a report/", figs, tabs)


if __name__ == "__main__":
    build()
