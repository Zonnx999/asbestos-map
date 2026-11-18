# Data contract (pipeline → web)  — owner: team lead

All files UTF-8 JSON under `web/data/`. Codes: 2-digit 시·도 code and 5-digit 시·군·구 code as in `data/boundaries/skorea-*-2018-topo-simple.json` (`code` property). New 2026 names (전남광주통합특별시, 강원/전북 특별자치도, 인천 영종구/제물포구, …) map to these codes via an alias table in the pipeline.

Class `c`: `2` = 석면건축물 (flag says yes) · `1` = 여부 미표기 (survey-target list, no flag) · `0` = 비해당 (flag says no).

## manifest.json
```json
{"built_at":"2026-11-18T12:00:00+09:00",
 "totals":{"c2":0,"c1":0,"c0":0,"points":0,"nocoord":0},
 "sources":[{"id":"15092323","publisher":"한국환경공단","title":"…","updated":"2026-11-03","rows":33482,"url":"https://www.data.go.kr/data/15092323/fileData.do"}],
 "sido":[{"code":"11","name":"서울특별시","bbox":[w,s,e,n],"c2":0,"c1":0,"c0":0,"points":0,"nocoord":0}]}
```

## summary/sigungu.json
`{"11110":{"name":"종로구","sido":"11","c2":0,"c1":0,"c0":0,"points":0,"nocoord":0}}` — counts include ALL rows (with or without coordinates). Rows whose 시·군·구 can't be resolved are counted only at 시·도 level (`"sido-only"` bucket: key `"{sido}000"`).

## sido/{code}.json — buildings WITH coordinates (columnar)
```json
{"fields":["lat","lng","c","name","addr","use","year","area","src","sgg","geo"],
 "rows":[[35.85123,128.53211,2,"○○초등학교","대구 달서구 …","교육연구시설",1985,1234.5,"3047280","27290","src"]]}
```
lat/lng rounded to 5 decimals; missing → `null`. `geo` = how the point was obtained: `"src"` (coords in source), `"vworld"`, `"kakao"`, `"nominatim"`.

## nocoord/{code}.json — rows still without coordinates
`{"fields":["c","name","addr","use","year","src","sgg"],"rows":[…]}`

## boundaries/sido.geojson, boundaries/sigungu.geojson
GeoJSON FeatureCollection (WGS84), properties `{code,name}`, simplified to ≤1.5 MB each.

## Dedupe
Same building from several sources → keep one row; prefer the source with an explicit flag, then the one with coordinates; `src` lists ids joined by `,`.
