# config.md — Parámetros del proyecto

Todos los parámetros del análisis se declaran en el bloque `yaml` de abajo.
El módulo [`src/config.py`](src/config.py) lo lee en tiempo de ejecución;
**no hay rutas, umbrales ni departamentos hardcodeados** dentro del código de `src/`.

Para cambiar el ámbito de estudio, edita únicamente este archivo.

## Ámbito geográfico seleccionado

| Región      | Departamento | UBIGEO (dpto.) | Justificación                                  |
|-------------|--------------|----------------|-----------------------------------------------|
| Costa       | Tumbes       | 24             | Departamento costeño pequeño, red vial densa. |
| Sierra      | Apurímac     | 03             | Andino, orografía marcada, alta ruralidad.    |
| Selva       | Amazonas     | 01             | Amazónico, baja densidad vial (casos sin ruta).|

```yaml
# ------------------------------------------------------------------
# Ámbito geográfico
# ------------------------------------------------------------------
departments:
  coast:  "TUMBES"
  andes:  "APURIMAC"
  amazon: "AMAZONAS"

# UBIGEO de nivel departamento (2 dígitos) para filtrar padrones y shapefiles
department_ubigeo:
  TUMBES:   "24"
  APURIMAC: "03"
  AMAZONAS: "01"

# ------------------------------------------------------------------
# Oferta: establecimientos de salud (RENIPRESS)
# ------------------------------------------------------------------
facilities:
  # Categorías "resolutivas" para emergencias: II-1 y superiores
  resolutive_categories: ["II-1", "II-2", "II-E", "III-1", "III-2", "III-E"]
  only_active: true            # descartar establecimientos no activos

# ------------------------------------------------------------------
# Demanda: centros poblados (MINEDU SIGMED)
# ------------------------------------------------------------------
demand:
  min_population: 1            # descartar CCPP con población <= 0
  urban_population_threshold: 2000   # >= urbano ; < rural (INEI usa 2000)

# ------------------------------------------------------------------
# Validación de datos (Fase 1)
# ------------------------------------------------------------------
validation:
  # bbox de Perú: lon_min, lat_min, lon_max, lat_max
  peru_bbox: [-81.4, -18.4, -68.6, 0.1]
  duplicate_distance_m: 50     # dos puntos a < 50 m => posible duplicado
  max_snap_distance_m: 2000    # si el snap a la red vial excede esto => fallback
  required_facility_cols: ["codigo_renaes", "nombre", "categoria", "latitud", "longitud"]
  required_demand_cols: ["ubigeo", "nombre_ccpp", "poblacion", "latitud", "longitud"]

# ------------------------------------------------------------------
# Ruteo (Fase 2)
# ------------------------------------------------------------------
routing:
  engine: "osrm"              # osrm | osmnx | haversine_fallback
  osrm_base_url: "http://router.project-osrm.org"
  profiles: ["driving", "walking"]
  cache_path: "data/processed/route_cache.sqlite"
  matrix_path: "data/processed/od_matrix.parquet"
  max_table_sources: 90       # límite de orígenes por request a /table
  fallback_speed_kmh:         # velocidad media para estimar tiempo si no hay ruta
    driving: 30
    walking: 4.5
  detour_factor: 1.3          # factor de rodeo para la estimación haversine

# ------------------------------------------------------------------
# Métricas (Fase 3)
# ------------------------------------------------------------------
metrics:
  coverage_bands_min: [30, 60, 120]   # bandas de cobertura en minutos
  golden_hour_min: 60                 # umbral de la "hora dorada"

# ------------------------------------------------------------------
# Rutas de salida
# ------------------------------------------------------------------
paths:
  raw:             "data/raw"
  processed:       "data/processed"
  outputs:         "data/outputs"
  logs:            "logs"
  quality_reports: "reports/quality"
```
