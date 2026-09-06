# Fuentes de datos — qué descargar y dónde ponerlo

> Ámbito: **Tumbes, Apurímac y Amazonas**. Igual conviene bajar los padrones
> **nacionales** y filtrar por código de departamento en la Fase 1.

## Reparto de trabajo

| # | Dato | Lo hace | Archivo destino |
|---|------|---------|-----------------|
| 1 | RENIPRESS (establecimientos de salud) | **Karelys (manual)** | `data/raw/renipress.csv` |
| 2 | Centros poblados con población y coordenadas | **Karelys (manual)** | `data/raw/centros_poblados.csv` |
| 3 | Límites distritales (shapefile / GeoJSON) | **Claude (script)** | `data/raw/limites_distritales.gpkg` |
| 4 | Red vial OSM de los 3 departamentos | **Claude (osmnx, automático)** | `data/processed/red_vial_*.graphml` |
| 5 | `peru-latest.osm.pbf` (solo si montamos OSRM local) | **Opcional — Karelys** | `data/raw/peru-latest.osm.pbf` |

---

## 1. RENIPRESS — Registro Nacional de IPRESS  *(manual)*

Establecimientos de salud con categoría (I-1 … III-2), estado y coordenadas.

- Portal datos abiertos SUSALUD: <https://datos.susalud.gob.pe/dataset/registro-nacional-de-ipress-renipress>
- Alternativa: <https://www.datosabiertos.gob.pe/> → buscar «RENIPRESS».
- Descargar el **CSV nacional** (suele llamarse `RENIPRESS.csv` o similar).
- Guardar como **`data/raw/renipress.csv`** (déjalo con el nombre exacto).

Columnas que necesitamos (los nombres reales varían; el script las normaliza):
`código RENAES / RENIPRESS`, `nombre del establecimiento`, `clasificación / categoría`,
`departamento`, `provincia`, `distrito`, `estado`, `latitud`, `longitud`.

## 2. Centros poblados  *(manual)*

Puntos de **demanda**: nombre, población y coordenadas.

Mejor fuente gratuita (elige la que puedas descargar):

- **INEI – Centros Poblados** (censo 2017), capa de puntos con población:
  <https://www.datosabiertos.gob.pe/> → «centros poblados INEI», o el
  Sistema de Consulta de Centros Poblados del INEI.
- **MINEDU – Padrón / SIGMED**: <http://sigmed.minedu.gob.pe/mapaeducativo/>
  (exporta centros poblados con código de local y coordenadas).
- **geoGPS Perú** (mirror con shapefiles de CCPP por departamento):
  <https://www.geogpsperu.com/>

Guardar como **`data/raw/centros_poblados.csv`** (o `.shp`/`.gpkg`; el script
acepta ambos). Debe tener: `ubigeo` (6 díg.), `nombre del CCPP`, `población`,
`latitud`, `longitud`.

## 3. Límites distritales  *(Claude, script)*

Lo bajo por API (geoBoundaries / INEI). Si prefieres hacerlo tú:
<https://www.geogpsperu.com/> → «Límite distrital Perú».
Guardar como **`data/raw/limites_distritales.gpkg`**.

## 4. Red vial OSM  *(Claude, automático con `osmnx`)*

No necesitas descargar nada: `osmnx` baja la red vial de Tumbes, Apurímac y
Amazonas directamente desde Overpass y la cachea en `data/processed/`.

## 5. `peru-latest.osm.pbf`  *(opcional)*

Solo si decidimos montar un **servidor OSRM local** (más fiable que la API
pública para miles de pares). ~450 MB.

- <https://download.geofabrik.de/south-america/peru-latest.osm.pbf>

Guardar como **`data/raw/peru-latest.osm.pbf`**.
Descárgalo en segundo plano mientras avanzamos; si al final usamos la API
pública de OSRM o `osmnx`, no se usa y no pasa nada.

---

## Checklist para Karelys

- [ ] `data/raw/renipress.csv`
- [ ] `data/raw/centros_poblados.csv`
- [ ] *(opcional, en segundo plano)* `data/raw/peru-latest.osm.pbf`

Avísame cuando el 1 y el 2 estén en su sitio y sigo con la Fase 1 sobre datos reales.
