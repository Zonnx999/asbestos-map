# 전국 석면 건축물 지도

Static Leaflet map of asbestos (survey-target) buildings in Korea, built from data.go.kr files.

- Run the map: `python3 -m http.server -d web 8765` → http://localhost:8765/ (`?data=fixture` for test data)
- Rebuild data: `make offline` (no download) · `make all` (download + build) · geocode: `VWORLD_KEY=… make geocode web`
- Docs: `docs/data-sources.md` (sources, download method) · `docs/pipeline.md` · `docs/data-contract.md` · `docs/map-patterns.md` (UX spec)
- Reference prototype: `reference/석면건축물 지도.html`
