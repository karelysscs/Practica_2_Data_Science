# Fuentes de datos — qué descargar y dónde ponerlo

> Ámbito: **Tumbes, Apurímac y Amazonas**. Igual conviene bajar los padrones
> **nacionales** y filtrar por código de departamento en la Fase 1.

## Reparto de trabajo

| # | Dato | Estado | Archivo destino |
|---|------|--------|-----------------|
| 1 | RENIPRESS — Agosto 2026 (establecimientos + categoría + coords) | ✅ descargado | `data/raw/renipress.csv` |
| 2 | SIGMED `CP_P.shp` — centros poblados (coords + altitud `Z`, **sin población**) | ✅ descargado | `data/raw/centros_poblados/CP_P.*` |
| 3 | **Centros poblados con POBLACIÓN** (Censo 2017) | ⏳ pendiente — Karelys | `data/raw/ccpp_poblacion/` |
| 4 | Límites distritales (shapefile / GeoJSON) | Claude (script) | `data/raw/limites_distritales.gpkg` |
| 5 | Red vial OSM de los 3 departamentos | Claude (osmnx, automático) | caché de `osmnx` |
| 6 | `peru-latest.osm.pbf` (solo si montamos OSRM local) | opcional — Karelys | `data/raw/peru-latest.osm.pbf` |

### ⏳ Pendiente #3 — población por centro poblado

El shapefile de SIGMED (#2) trae coordenadas y altitud pero **no población**, y su
enlace al código INEI (`CPINEI`) está vacío en el 32 % de los casos. Necesitamos
una capa de centros poblados del **Censo 2017 que incluya `POB_TOTAL`**:

- **geogpsperu → «Centros Poblados - Censo 2017»**
  <https://www.geogpsperu.com/2019/05/centros-poblados-censo-2017-shapefile_29.html>
  Descargar **Tumbes, Apurímac y Amazonas** de la capa
  «Centros Poblados (Urbano y Rural)» o «(Categorías)».
- Alternativa: INEI, Directorio Nacional de Centros Poblados 2017 (son PDF por tomo).

Descomprimir **todo** dentro de `data/raw/ccpp_poblacion/`. El script hace el
*join* con #2 por código de centro poblado (`CODCP`) o por `CPINEI`.

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

- [x] `data/raw/renipress.csv`
- [x] `data/raw/centros_poblados/CP_P.*` (SIGMED)
- [ ] `data/raw/ccpp_poblacion/` — centros poblados Censo 2017 **con población**
- [ ] *(opcional, en segundo plano)* `data/raw/peru-latest.osm.pbf`
