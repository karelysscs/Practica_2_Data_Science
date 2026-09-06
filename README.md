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
  utils.py                Logging, geometría
  phase1_data.py          Adquisición + validación (reportes de calidad)
  phase2_routing.py       Ruteo OSRM + caché + matriz O-D + fallback
  phase3_metrics.py       Tiempo de acceso, bandas, Gini, urbano-rural
app.py                    Dashboard Streamlit (Fase 4)
report/main.tex           Informe LaTeX (Fase 5)
data/raw|processed|outputs/
reports/quality/          Reportes de calidad de datos
docs/DATA_SOURCES.md      Qué descargar y dónde ponerlo
```

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

## Datos

Descarga manual mínima (ver [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md)):

| Archivo | Fuente |
|---------|--------|
| `data/raw/renipress.csv` | SUSALUD — RENIPRESS |
| `data/raw/centros_poblados.csv` | INEI / MINEDU SIGMED |

## Ejecución

```bash
python -m src.phase1_data       # valida y genera reports/quality/*
python -m src.phase2_routing    # construye data/processed/od_matrix.parquet
python -m src.phase3_metrics    # genera data/outputs/summary.json + métricas
streamlit run app.py            # dashboard
```

La matriz O-D (`data/processed/od_matrix.parquet`) se versiona para que el
dashboard funcione sin conexión.

## Fases y puntaje

| Fase | Entregable | Pts |
|------|-----------|-----|
| 1 | Validación y reportes de calidad | 3.0 |
| 2 | Ruteo, caché, matriz | 3.0 |
| 3 | Métricas y cruces | 2.0 |
| 4 | Dashboard con simulador | 2.5 |
| 5 | Informe LaTeX + limitaciones | 1.5 |
| — | Presentación en video (≤12 min) | 8.0 |
