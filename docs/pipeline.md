# Pipeline (fetch -> normalize -> geocode -> build_web)

Python 3.9 stdlib only (no pip/uv needed; the 2 xlsx files are read with zipfile+xml). Run from the repo root.

## Run
```
make            # = fetch normalize geocode web   (fetch ~3-4 min: 1 req/s, skips unchanged files)
make offline    # no network: normalize geocode web  (~5 s)
python3 scripts/fetch_all.py [--force] [--dry-run] [--ids 3046092 ...]
```
| step | script | in -> out |
|---|---|---|
| fetch | `fetch_all.py` | `data/sources.json` (category building_list) -> `data/raw/*` + `data/raw/_fetch_state.json`. Re-downloads only when the data.go.kr page `dateModified` changed. 15155665 comes from the keyless `/download/standard.json` endpoint. |
| normalize | `normalize.py` | `data/raw/*` -> `data/processed/buildings.csv`, `sources_used.json`, `normalize-report.md` (per-source counts, column mapping, unmatched sgg, rejects) |
| geocode | `geocode.py` | `buildings.csv` -> `data/cache/geocode.json` (no key: prints a notice, exits 0) |
| build | `build_web.py` | `buildings.csv` + cache -> `web/data/*` per `docs/data-contract.md`, then asserts totals == sum(sigungu) == rows |
Shared code: `scripts/common.py` (readers, TopoJSON decode/simplify, sido/sgg resolver + alias table, TM->WGS84).

## Add a geocoding key
```
export VWORLD_KEY=...      # primary (vworld.kr, free); VWORLD_DOMAIN=... if the key is domain-bound
export KAKAO_KEY=...       # optional fallback (Kakao REST API key)
python3 scripts/geocode.py --dry-run    # shows how many queries
make geocode web
```
- ~24.7k unique address queries (37.9k rows without coordinates): about 80 min at the default 5 req/s (`--rate`), more with the fallback.
- Cache is written every 200 queries and on Ctrl-C; reruns continue. `--retry-misses` retries not-found ones; `--limit N` for a trial.
- A hit must lie in its sgg bbox (+0.02 deg) or it is cached as `reject` and ignored (build_web re-checks).
- Nominatim is intentionally not used (OSM policy forbids bulk geocoding). Kakao terms: check before showing results on a non-Kakao map.
- Query = modern sido name + 2018-canonical sgg name + road/lot core (renamed regions are translated, so geocoders see names they know).

## How rows are built
- `c`: explicit flag column (해당/미해당, Y/N, 석면함유건물...) -> 2/0; else 위해성 grade present -> 2; else asbestos-material area >0 -> 2, =0 -> 0; else 1. Columns like 정읍 `석면조사여부` (survey done) are NOT flags.
- Region: address text -> sido/sgg via 2018 boundary names + aliases (전남광주통합특별시 split by district, 강원/전북 특별자치도, 인천 영종구/제물포구->중구 (제물포구 동구 법정동->동구), 서해구/검단구->서구, 미추홀구->남구, 군위군->경북 37310). Missing sido prefix -> publisher region. Rows with coordinates but no sgg get it by point-in-polygon.
- Dedupe: across sources by (sgg, road/lot core, name; sub-building tolerant), then same address + one name containing the other; best row = explicit flag, then coordinates, then newest. Inside one source only fully identical rows collapse (several 동 under one name stay separate).
- Name = building name (+ 동명 when different). Phone numbers and owners are never carried over.

## Known data issues
- ~96% of rows have no coordinates until a key is used (only 15155665, 15103460, 3047280 carry WGS84 coordinates; no source uses projected coordinates, but TM 5174/5179/5186 conversion is implemented and used automatically for 5179-sized values).
- Class mix is skewed: 15092323 lists 26k 미해당 public buildings; ~2.3k rows (survey-target lists without a flag) stay c=1.
- Same building, different answers: 166 merged buildings have 0 vs 2 in different sources (newest explicit flag wins; see report).
- Seoul 15048930 (OA-15383), 15072370 (OA-1119) and 15048932 (OA-15384) are fetched keyless from data.seoul.go.kr (`fetch_all.py fetch_seoul`: datasetView + sheetView set the session, `/dataList/dataView.do` pages 1000 rows with a total ORDER BY; the CSV button's datafile.seoul.go.kr POST rejects scripts). No SEOUL_KEY needed. Owner/contact columns are dropped before writing. 15048932 (12,857 per-sample records, no address) is downloaded but not used (NOT_BUILDINGS in normalize.py). 강동 3074878 is the gangdong.go.kr board b_098 (106 buildings, name + 검출/미검출, no address; scraped, joined to same-name rows by sgg). Seoul-owned sites outside Seoul (서울대공원 etc.) keep their real sido (FALLBACK_SIDO).
- 학교 석면 is not on data.go.kr; private buildings only where a 지자체 published.
- Boundaries are the 2018 KOSTAT vintage (250 sgg): 2026 new districts have no polygon of their own; 군위 stays under 경북 even though addresses say 대구. Simplified per arc (sigungu tolerance ~0.0003 deg, ~1.5 MB).
- Source dates range 2014 (칠곡) to 2026-11; per-row `updated` is the source's 데이터기준일자 (fallback: dataset `updated`).
- Fixed-size per-source quirks are handled in `normalize.py` OVERRIDES (안동 has no name column, 광산구 has no street address, CR-only line endings, CP949 vs UTF-8-BOM).
