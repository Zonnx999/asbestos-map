# Map patterns for the nationwide asbestos-building map

Scope: static site, Leaflet, ~100k-500k points, rows with and without coordinates.
Evidence level is marked: [V] verified in this research, [K] from general knowledge, not re-checked (verify before shipping).
Many target sites are JS apps behind logins/keys; web search returned little on their internal UX, so "pattern" columns are partly inferred.

## 1. Survey

| Site | Pattern worth copying | Source |
|---|---|---|
| 드론원스톱 (drone.onestop.go.kr), Ready to Fly | Restricted-zone polygons drawn as colored overlays (prohibition = pink) on a VWorld base, with a legend in the search panel; map says "check on the day, zones change" [V] | https://www.molit.go.kr/USR/policyTarget/dtl.jsp?idx=584 |
| VWorld (vworld.kr) | Free Korean base map (Base/white/midnight/Satellite/Hybrid), WMTS in EPSG:3857, works in Leaflet, needs a registered key + allowed domain [V for 3857/Leaflet/key; URL template K] | https://www.vworld.kr/dev/v4dv_wmtsguide_s001.do (404 on fetch; try v4dv_wmtsguide_s001 variants), https://github.com/V-world/V-world_API_sample |
| 바로e맵 (NGII) | Plain neutral topographic base, minimal chrome; good model for "base map stays quiet, data is colored" [K] | https://map.ngii.go.kr |
| 생활안전지도 (safemap.go.kr) | Theme toggles (one hazard theme per layer group), point icons at high zoom, region aggregation at low zoom [K, not confirmed by search] | https://www.safemap.go.kr |
| 공적 마스크 지도 (2020) | Pin color = stock state (many/few/none/arriving), explicit "data lags 5-10 min" note, users asked to hide empty points [V] | https://www.mt.co.kr/tech/2020/03/16/2020031216464796760 |
| 코로나 동선 지도 | Aggregate-then-drill clustering; per-point date text in popup [K] | n/a |
| EPA EJScreen | Choropleth by percentile bins, per-layer description page, explicit "screening tool, substantial uncertainty at small areas, never fully up to date" disclaimer, change log with dataset year [V] | https://19january2025snapshot.epa.gov/ejscreen/ejscreen-map-descriptions , https://19january2021snapshot.epa.gov/ejscreen/frequent-questions-about-ejscreen_.html |
| CARTO basemaps | Light Positron/Voyager base lets colored points pop; raster Voyager needs a free key now, vector style recommended; attribution "(c) OpenStreetMap contributors (c) CARTO" mandatory [V] | https://docs.carto.com/faqs/carto-basemaps |
| Leaflet.markercluster | Works but README suggests 10k-50k markers; >100k loads slowly (tens of seconds reported); use `chunkedLoading`, `removeOutsideVisibleBounds` [V] | https://github.com/Leaflet/Leaflet.markercluster |
| Supercluster | Index-based clustering for 100k-500k points in the browser, fast build, cluster lookup by bbox+zoom [K] | https://github.com/mapbox/supercluster |
| MapLibre + PMTiles | Vector tiles from a single static file via HTTP range requests; the way to go beyond ~500k without a server [K] | https://docs.protomaps.com/pmtiles/ |
| OSM tile policy | tile.openstreetmap.org: light use only, attribution required, no heavy/bulk or prefetch; not for a launch-scale public site [K] | https://operations.osmfoundation.org/policies/tiles/ |

Not found: a UK asbestos hazard map worth copying (nothing surfaced); 석면관리 종합정보망 map and 토지이음 not confirmed.

## 2. Spec for our map

### Base map
1. Default: CARTO Voyager or Positron (light, labels in English/local; Korean labels are weak). Needs free key [V].
2. Korean option: VWorld `Base` (good Korean labels). Key + domain registration needed. Template [K, verify]:
   `https://api.vworld.kr/req/wmts/1.0.0/{KEY}/Base/{z}/{y}/{x}.png` (note y before x), also `white`, `midnight`, `Satellite`, `Hybrid`. Max zoom ~18-19.
   Attribution: `VWorld` / (c) 국토교통부 (check guide text). A key is visible in page source: restrict by domain in the VWorld console.
3. Fallback with no key: OSM standard tiles, only while traffic is small; attribution `(c) OpenStreetMap contributors`.
4. Layer-switcher with 2-3 bases; remember last choice in localStorage.

### Layers by zoom (Leaflet zoom)
| Zoom | Show | Why |
|---|---|---|
| 6-8 | Province (sido) choropleth, count label per sido; rows with no coordinates are included here | everything has a sido; honest about missing coords |
| 8-10 | Sigungu choropleth or supercluster clusters (cluster radius 60) | transition; load sido chunk on first entry |
| 11-13 | Clusters from supercluster, colored by dominant class, count badge | avoid hairball |
| 14+ | Individual points, circle markers on one canvas renderer | click targets; popup |
Heatmap (leaflet.heat) only as an optional toggle: density of 석면 buildings, not of risk; it hides the uncertain class, so default OFF. Choropleth bins: quantile 5 classes, sequential single hue, legend shows numbers.

### Color / legend
- Red `#d32f2f` 석면건축물; amber `#f9a825` 여부 미표기 (unverified); grey `#9e9e9e` 비해당. Keep the prototype colors.
- Unverified = amber fill + dashed ring (do not rely on color alone; color-blind safe, and print safe). Grey drawn first (bottom), amber, red on top.
- Cluster colors: red if any red inside; otherwise amber if any amber; else grey. Badge shows total, small subscript "N red".
- Legend: fixed collapsible card, counts per class (live for current filter), one line each: "여부 미표기 = 자료에 표기가 없음, 석면이 없다는 뜻이 아님".
- Choropleth legend: bins + "좌표 없는 N건 포함".

### Layout
- Desktop: left side panel 340px (stats, legend, filters, search, results list), map fills rest. Panel collapsible.
- Mobile (<768px): map full screen; search bar floating on top; bottom sheet with 3 snap points (peek 72px = counts + legend chip, half, full list). Popups become the sheet content on tap (no Leaflet popup on mobile). Controls >= 44px. Zoom buttons bottom right above the sheet.
- Filters: class chips (red/amber/grey), building use, sido select. Applied client side to the loaded chunk; cluster index rebuilt on change (debounced).

### Popup / detail card
Building name, full address (road + lot), class badge, 지정/조사 year and use, area/floors if present, source dataset name + 기준일, "민원/정정 요청" link, copy-address button. Missing field: show "정보 없음", never blank.

### Search
- Address/building-name search over a small per-load index: `data/search-index.json` (name, sido, sigungu, lat, lon, id) or lazily per sido; use Fuse.js (threshold 0.3, keys name/addr) for <= ~50k rows per chunk; for nationwide, prefix match on pre-sorted array.
- Choose a result: flyTo zoom 16, open the card. Rows without coordinates: list them under the sigungu with "위치 미상" and zoom to sigungu.
- Optional place geocoding (not buildings): skip in v1; if needed VWorld geocoder API needs key and is not CORS-friendly (JSONP) [K].

### Data loading
```
web/data/
  manifest.json            # version, built_at, source name, per-sido {file,count,bbox,counts by class}
  sido-summary.json        # counts per sido (+ sigungu) for the choropleth; tiny
  sido/{code}.json         # points for one sido: columnar arrays [lon,lat,class,id] + props by id
  detail/{code}-{n}.json   # optional: popup fields, chunks of 2k rows, fetched on click
  nocoord/{code}.json      # rows with no coordinates (list only)
  boundaries/sido.geojson, sigungu.geojson   # simplified (mapshaper 1-2%), < 300 KB / < 2 MB
```
- Coordinates rounded to 5 decimals; classes as ints; short keys; gzip handled by host (GitHub Pages/Cloudflare serve gzip/br). Target <= 1.5 MB gz per sido; Gyeonggi/Seoul split by sigungu if larger.
- Fetch a sido only when the viewport intersects its bbox at zoom >= 8; cache in memory; at most 3 in flight; show a thin progress bar.
- Build one Supercluster index per loaded sido (or merged); run in a Web Worker if build >200 ms.
- Always show `manifest.built_at` and source in the footer ("자료 기준일").
- If total points exceed ~500k or choppy on mobile: switch to PMTiles + MapLibre GL (same data, one static file, no chunk logic).

### Rendering performance
- `L.map(..., {preferCanvas: true})`; draw points as `L.circleMarker` (radius 5-6, weight 1) not `L.marker` with divIcon (DOM per point is the prototype's bottleneck).
- Render only points in view (supercluster `getClusters(bbox, zoom)` on `moveend`), usually < 3k objects.
- If keeping markercluster for the first version: `chunkedLoading: true`, `removeOutsideVisibleBounds: true`, `disableClusteringAtZoom: 16`, `spiderfyOnMaxZoom: true`.

### Disclaimers and freshness (footer card + first-visit banner, dismissible)
1. Data source, dataset name, 기준일 (from manifest), update frequency.
2. "참고용입니다. 법적 효력 없음. 석면 여부 미표기 건물은 석면이 없다는 뜻이 아닙니다." (EJScreen-style screening caveat).
3. Coordinates are geocoded from addresses and can be off; N rows have none.
4. Contact / 정정 요청 link; license of the data (공공누리 type) and base map attributions in the Leaflet attribution control.
5. No personal info; private homes: show address only as published.

### Library picks (jsDelivr, exact versions; all returned HTTP 200 on 2026-11-18)
| Need | URL |
|---|---|
| Leaflet 1.9.4 | `https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js` (+ `leaflet.css`) |
| Supercluster 8.0.1 | `https://cdn.jsdelivr.net/npm/supercluster@8.0.1/dist/supercluster.min.js` |
| Leaflet.markercluster 1.5.3 (fallback, as in prototype) | `https://cdn.jsdelivr.net/npm/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js` (+ `MarkerCluster.css`, `MarkerCluster.Default.css`) |
| Fuse.js 7.0.0 | `https://cdn.jsdelivr.net/npm/fuse.js@7.0.0/dist/fuse.min.js` |
| leaflet.heat 0.2.0 (optional) | `https://cdn.jsdelivr.net/npm/leaflet.heat@0.2.0/dist/leaflet-heat.js` |
| MapLibre GL 4.7.1 + PMTiles 3.2.1 (scale-up path) | `https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js`, `https://cdn.jsdelivr.net/npm/pmtiles@3.2.1/dist/pmtiles.js` |
Prototype also uses papaparse 5.4.1 and proj4 2.9.0: needed only in the build step (Python/Node), not in the public page, since data are pre-converted to WGS84 JSON.

### Open questions for the team
- VWorld key: register domain (GitHub Pages hostname) and confirm attribution text in the portal guide.
- Confirm data license (공공데이터포털 terms) before showing building-level addresses.
- Check real point counts from `data/processed` to pick Supercluster (<= 500k) vs PMTiles.
