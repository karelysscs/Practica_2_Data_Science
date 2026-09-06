# Práctica 2 · Data Science — Accesibilidad a servicios de emergencia en Perú

Análisis geoespacial del tiempo de viaje por carretera de la población hacia
establecimientos de salud **resolutivos** (categoría II-1 o superior) en tres
departamentos que representan costa, sierra y selva: **Tumbes, Apurímac y
Amazonas**. Basado en el concepto de la *hora dorada* en medicina de emergencia.

> Enunciado: `d2cml-ai/Data-Science-Python` issue #186 · Entrega: **2026-09-09**

## Estructura

```
config.md                 Parámetros del análisis (única fuente de verdad)
requirements.txt
src/
  config.py               Lee config.md
  utils.py                Logging, geometría (haversine)
  phase1_data.py          Adquisición + validación (reportes de calidad)
  phase2_routing.py       Ruteo OSRM /table + caché SQLite + matriz O-D + fallback
  phase3_metrics.py       Tiempo de acceso, bandas, Gini, urbano-rural, altitud
  figures.py              Figuras (.png) y tablas (.tex) del informe
app.py                    Dashboard Streamlit (Fase 4)
report/main.tex           Informe LaTeX (Fase 5)
data/raw|processed|outputs/
reports/quality/          Reportes de calidad (oferta, demanda, ruteo)
docs/DATA_SOURCES.md      Fuentes de datos y estado de descargas
```

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows  (bash: source .venv/bin/activate)
pip install -r requirements.txt
```

## Datos

Descarga manual (detalle y enlaces en [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md)):

| Ruta | Fuente | Contenido |
|------|--------|-----------|
| `data/raw/renipress.csv` | SUSALUD — RENIPRESS (Ago 2026) | oferta: establecimientos + categoría + coords |
| `data/raw/cpp_población/cpp_{tumbes,apurimac,amazonas}/*.shp` | geogpsperu — CCPP Censo 2017 | demanda: población + altitud + coords |

Los límites distritales (`data/raw/peru_distritos.geojson`) se descargan solos en
la Fase 3. El ruteo usa el OSRM público (`router.project-osrm.org`), sin descarga.

## Ejecución

```bash
python -m src.phase1_data       # valida -> reports/quality/*.md + data/processed/*_validated.parquet
python -m src.phase2_routing    # OSRM -> data/processed/od_matrix.parquet (+ caché reanudable)
python -m src.phase3_metrics    # -> data/outputs/summary.json, metrics_*.parquet, metrics_distrito.gpkg
python -m src.figures           # -> data/outputs/figs/*.png, data/outputs/tabs/*.tex
streamlit run app.py            # dashboard interactivo
```

La matriz O-D (`data/processed/od_matrix.parquet`) y los resultados
(`data/outputs/`) se versionan para que el dashboard funcione **sin conexión**.

## Fases y puntaje

| Fase | Entregable | Pts |
|------|-----------|-----|
| 1 | Validación y reportes de calidad | 3.0 |
| 2 | Ruteo, caché, matriz | 3.0 |
| 3 | Métricas y cruces | 2.0 |
| 4 | Dashboard con simulador | 2.5 |
| 5 | Informe LaTeX + limitaciones | 1.5 |
| — | Presentación en video (≤12 min) | 8.0 |
