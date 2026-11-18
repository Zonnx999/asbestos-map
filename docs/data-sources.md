# Data sources for the nationwide asbestos-building map

Checked 2026-11-18. Everything below was fetched with sequential requests (1 s sleeps), a generic User-Agent, no login, no cookies beyond the anonymous session. Machine-readable version: `data/sources.json` (112 entries). Scripts: `scripts/search_datago.py` (enumerate), `scripts/fetch_datago.py` (download), `scripts/analyze_raw.py`, `scripts/build_sources.py`.

## 1. Bottom line

| What | Verdict |
|---|---|
| data.go.kr search "석면" (+5 other keywords) | 112 unique hits: 107 fileData, 4 OpenAPI, 1 표준데이터셋. 55 are asbestos-**building** lists (환경공단 national file + 53 district fileData + the national standard set); the rest are removal-work records, 피해구제, 업자 lists, etc. |
| Backbone A: `15092323` 한국환경공단 석면조사대상건축물현황(공공건축물) | **33,482 rows, all 16 시·도 (광주 is now inside 전남광주), public buildings only**, has 석면건축물 여부 (해당 7,205 / 미해당 26,277) and 석면자재 면적. **Address only, no coordinates.** Updated 2026-11-03. Keyless download OK. |
| Backbone B: `15155665` 전국석면조사건축물표준데이터 | 1,525 rows from only 19 시·군·구 so far (merged monthly), **has lat/lng + Y/N flag + area**. Keyless JSON endpoint OK. |
| Per-district files | 53 district fileData lists: 49 downloaded, 4 only link out (Seoul/강동). They overlap A but add 다중이용시설/의료/어린이집 that A lacks. Only 2 have coordinates natively (3047280 달서구, 15103460 부산시) besides the standard set. |
| Download without login | **Yes**, verified on 53 files (the 3 requested + 50 more) with no captcha. Mechanism in section 2. |
| Open APIs (odcloud / api.data.go.kr) | Need a `serviceKey` (confirmed: HTTP 401 `-401 인증키는 필수 항목 입니다` / `SERVICE_KEY_IS_NULL`). Not needed for bulk files. |
| 석면관리 종합정보망 (asbestos.me.go.kr) | Official national register incl. private buildings, but **view-only** and its TLS certificate is expired (curl refuses; browser warning). Not scraped. |
| 학교 석면 | **Nothing on data.go.kr** (keywords 학교/교육청/어린이집 석면 return no school datasets). Figures only via 정보공개청구/news. Schools in the 환경공단 set only when classified 공공건축물. |
| Blockers | (1) No coordinates for ~97% of rows, need geocoding (key needed for good quality). (2) 사립/다중이용시설/의료시설 coverage only where a 지자체 published. (3) No fresh national private-building list. |

## 2. How data.go.kr fileData download works (verified)

Plain HTTP, no login. The page says "파일데이터는 로그인 없이 다운로드를 통해 이용하실 수 있습니다."

```
1. GET  https://www.data.go.kr/data/{id}/fileData.do
        -> sets JSESSIONID; HTML has  fileDetailObj.fn_fileDataDown('{id}','uddi:...','','1','1')   (button onclick)
           and a <script type="application/ld+json"> block with name, publisher, dateModified, column names (variableMeasured)
2. POST https://www.data.go.kr/tcs/dss/selectFileDataDownload.do
        form: publicDataPk={id}&publicDataDetailPk={uddi}&atchFileId=&fileDetailSn=1&publicDataTyCode=PR0051
        headers: X-Requested-With: XMLHttpRequest, Referer: the fileData page
        -> JSON {status:true, atchFileId:"FILE_0000...", fileDetailSn:"1",
                 fileDataRegistVO:{atchFileExtsn:"csv", orginlFileNm, atchFileCo:"87" (row count), ...}}
3. POST https://www.data.go.kr/cmm/cmm/check-limit.json   atchFileId=..&fileDetailSn=..   -> {"needCaptcha":false}
        (if true a captcha is required; never hit in ~60 downloads at 1 req/s)
4. GET  https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId={atchFileId}&fileDetailSn={sn}&dataNm={anything}
        -> raw bytes, Content-Disposition attachment
```

Worked example (3047280 → 12,174 bytes, 87 rows):
```
curl -s -c cj -b cj -A "Mozilla/5.0 (compatible; x)" https://www.data.go.kr/data/3047280/fileData.do -o /dev/null
curl -s -c cj -b cj -X POST https://www.data.go.kr/tcs/dss/selectFileDataDownload.do -H "X-Requested-With: XMLHttpRequest" \
  --data-urlencode "publicDataDetailPk=uddi:6ff47b0c-90f0-47a7-a4a6-cb47369f9c24" -d "publicDataPk=3047280&atchFileId=&fileDetailSn=1&publicDataTyCode=PR0051"
curl -s -c cj -b cj "https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=FILE_000000003639102&fileDetailSn=1&dataNm=x" -o 3047280.csv
```
`scripts/fetch_datago.py get <id>...` does all four steps; `meta` does steps 1-2 only. The `atchFileId` changes when the provider re-uploads, so always re-run step 2 (do not hard-code it).

Encoding: the response says `charset=UTF-8` but that is wrong. Sniffed by decoding: **CP949 for most CSVs**, UTF-8 with BOM for 15000828, 15038683, 15166534; 15155665 (JSON) is UTF-8. XLSX for 3057407, 3073551. 3077221 uses CR-only line endings (use `splitlines()`). Header row is row 0 in all CSVs; row counts: files carry a trailing space/blank in some cells (e.g. `1 ` in 3068148).

Other mechanics found:
- Search page is server-rendered: `/tcs/dss/selectDataSetList.do?sType=detail&keyword=석면&dType=FILE&perPage=100&currentPage=1` (dType = FILE | API | STD | LINKED). Parse `<div class="apply-result-link">` blocks.
- `https://www.data.go.kr/data/{id}/fileData.do?format=md` returns a short markdown metadata page (no file link).
- `https://www.data.go.kr/catalog/{id}/fileData.json` is the Schema.org/DCAT record (metadata only).
- Every fileData also has an odcloud REST API (`https://api.odcloud.kr/api/{id}/v1/uddi:...`); swagger is public at `https://infuser.odcloud.kr/oas/docs?namespace={id}/v1`, calls need `serviceKey`. Not needed.

### Nationwide standard dataset 15155665 (keyless JSON, bypasses UI)
The page's download buttons are built in JS (`/js/biz/mvc/std/std-download-manager.js`) from two JSON endpoints:
```
GET https://www.data.go.kr/download/columList.json?pk=15155665&ext=JSON   -> {columList:[...], totalCount:1525, tableVO:{colNmList:[...], svcTableNm:"tn_pubr_public_asbestos_survey_building_svc"}}
GET https://www.data.go.kr/download/standard.json?publicDataPk=15155665&svcTableNm=tn_pubr_public_asbestos_survey_building_svc&totalCount=1525&perPage=10000&page=1&colNmList=BDST_NM&colNmList=EMD_NM&...   -> JSON array of records (page through while page*10000 < totalCount)
```
Columns: BDST_NM, EMD_NM, LCTN_ROAD_NM_ADDR, LCTN_LOTNO_ADDR, LAT, LOT, SBST_INSP_YN (Y/N), GFA, SBST_AREA, BDST_TYPE, MNG_INST_NM, MNG_INST_TELNO (agency phone, do not publish), DATA_CRTR_YMD, plus INSTT_CODE, INSTT_NM. Saved as `data/raw/15155665_전국석면조사건축물표준데이터.json` (UTF-8). Rows by provider: 천안 243, 수원 231, 화순 145, 진주 141, 진안 103, 안양 100, 서귀포 87, 금정 73, 충주 69, 동해 58, 대구중구 43, 가평 42, 군포 41, 연제 31, 합천 28, 영도 28, 수영 22, 부안 22, 고성 18. Coordinates present for all 1,525. Several of these providers (수원, 안양, 진주, 진안, 충주, 동해, 가평, 금정, 수영, 부안, 고성, 합천, 화순) are **not** found by the keyword search, so this set is the only keyless source for them. The same data is available via OpenAPI `https://api.data.go.kr/openapi/tn_pubr_public_asbestos_survey_building_api` (serviceKey; dev account 10,000 calls/day).

### Seoul (not on data.go.kr as files)
15072370 (OA-1119 서울시 공공 건축물 석면 현황, updated 2026-03-04 on the Seoul site), 15048930 (OA-15383 공공건축물 석면정보), 15048932 (OA-15384 석면시료 정보) only link to `data.seoul.go.kr`; 3074878 강동구 links to a gangdong.go.kr bulletin board (PDF). Seoul's file download is a POST to `datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do` with a per-file `seq` that the page injects dynamically; I did not resolve it (my guessed seq returned an HTML error). Seoul's OpenAPI (`openapi.seoul.go.kr:8088/{key}/json/{service}/1/1000/`) needs a free key. Seoul public buildings are already in 15092323 (3,993 rows), so this is low priority; 서초 (15052329), 강서 (15166534), 용산 (3077850) are downloadable on data.go.kr.

## 3. Backbone and per-district file table

`flag` = explicit asbestos status column (해당/미해당, Y/N, 석면건축물/무석면건물, 위해성 grade). Files with `-` flag are "조사대상" lists: presence of asbestos is unconfirmed (treat as "survey target"). `rows` counted from the file. Local copies in `data/raw/{id}_{publisher}.{ext}` are original bytes.

### 15092323 한국환경공단 (national, public buildings)
Header: 석면건축물_일련번호, 도로명주소, 건축물명, 건축물_동명, 건축물구분-대분류 (공공건축물 only), 건축물구분-소분류 (행정기관 19,935 / 공공기관 7,585 / 특수법인 4,310 / 지방공사·공단 1,652), 연면적, 석면건축물 여부, 석면자재 면적. CP949. By 시·도 (address prefix, note old names 강원도/전라북도/광주광역시 still used): 경기 5,252, 서울 3,993, 경북 3,241, 경남 3,041, 전남광주 2,754, 강원 2,710, 충남 2,114, 전북 1,793, 부산 1,788, 충북 1,606, 인천 1,434, 대구 1,205, 대전 1,180, 제주 656, 울산 597, 세종 118.

### Per-district lists (CSV/XLSX)

| id | publisher | rows | coords | addr | flag | enc | updated | note |
|---|---|---|---|---|---|---|---|---|
| 15053446 | 강원특별자치도 영월군 | 64 | n | Y | n | cp949 | 2025-09-02 | survey-target list, no flag |
| 3046237 | 강원특별자치도 원주시 | 638 | n | Y | n | cp949 | 2025-12-03 | survey-target list, no flag |
| 3046238 | 강원특별자치도 춘천시 | 138 | n | Y | Y | cp949 | 2026-09-28 |  |
| 15012282 | 경기도 고양시 | 1266 | n | Y | Y | cp949 | 2026-05-08 |  |
| 3073849 | 경기도 군포시 | 389 | n | Y | n | cp949 | 2026-09-30 | survey-target list, no flag |
| 15000828 | 경기도 성남시 | 148 | n | Y | n | utf8-bom | 2026-11-04 | survey-target list, no flag |
| 3047316 | 경기도 안산시 | 204 | n | Y | n | cp949 | 2026-02-20 | survey-target list, no flag |
| 3047293 | 경기도 하남시 | 241 | n | Y | Y | cp949 | 2026-08-24 |  |
| 15093011 | 경상남도 김해시 | 79 | n | Y | n | cp949 | 2026-10-12 | survey-target list, no flag |
| 3068544 | 경상남도 창원시 | 332 | n | Y | Y | cp949 | 2026-05-28 |  |
| 3047216 | 경상북도 김천시 | 35 | n | Y | n | cp949 | 2026-09-14 | survey-target list, no flag |
| 3047155 | 경상북도 문경시 | 49 | n | Y | n | cp949 | 2023-06-23 | survey-target list, no flag |
| 3047099 | 경상북도 안동시 | 434 | n | Y | Y | cp949 | 2025-11-25 |  |
| 3068391 | 경상북도 영양군 | 12 | n | Y | n | cp949 | 2026-11-17 | survey-target list, no flag |
| 3046945 | 경상북도 영주시 | 86 | n | Y | n | cp949 | 2025-06-24 | survey-target list, no flag |
| 3047310 | 경상북도 예천군 | 30 | n | Y | n | cp949 | 2026-08-12 | survey-target list, no flag |
| 3068114 | 경상북도 청송군 | 50 | n | Y | Y | cp949 | 2024-06-27 |  |
| 3073551 | 경상북도 칠곡군 | 112 | n | Y | n | xlsx | 2014-04-28 | survey-target list, no flag |
| 15113223 | 경상북도 포항시 | 89 | n | Y | n | cp949 | 2026-09-16 | survey-target list, no flag |
| 3046092 | 대구광역시 남구 | 234 | n | Y | Y | cp949 | 2026-10-02 |  |
| 3047280 | 대구광역시 달서구 | 87 | Y | Y | n | cp949 | 2026-05-12 | survey-target list, no flag |
| 3046188 | 대구광역시 동구 | 360 | n | Y | n | cp949 | 2026-05-06 | survey-target list, no flag |
| 3068216 | 대구광역시 북구 | 173 | n | Y | Y | cp949 | 2026-08-25 |  |
| 3068390 | 대구광역시 서구 | 39 | n | Y | n | cp949 | 2026-07-14 | survey-target list, no flag |
| 15052017 | 대구광역시 수성구 | 71 | n | Y | n | cp949 | 2026-09-18 | survey-target list, no flag |
| 3077221 | 대구광역시 수성구 | 10 | n | Y | n | cp949 | 2025-07-25 | survey-target list, no flag; CR-only line endings; lists buildings where the survey was not yet done |
| 3057407 | 대구광역시 중구 | 234 | n | Y | Y | xlsx | 2025-07-14 |  |
| 15103460 | 부산광역시 | 78 | Y | Y | Y | cp949 | 2026-09-20 |  |
| 3038626 | 부산광역시 영도구 | 11 | n | n | n | cp949 | 2026-09-04 | survey-target list, no flag |
| 3069195 | 부산광역시 영도구 | 33 | n | Y | n | cp949 | 2026-09-10 | survey-target list, no flag |
| 15026377 | 부산광역시 중구 | 99 | n | Y | Y | cp949 | 2026-07-09 |  |
| 15048930 | 서울특별시 | - | - | - | - | - | 2021-05-18 | external link: http://data.seoul.go.kr/dataList/OA-15383/S/1/datasetView.do |
| 15048932 | 서울특별시 | - | - | - | - | - | 2021-05-18 | external link: http://data.seoul.go.kr/dataList/OA-15384/S/1/datasetView.do |
| 15072370 | 서울특별시 | - | - | - | - | - | 2020-10-29 | external link: http://data.seoul.go.kr/dataList/OA-1119/S/1/datasetView.do |
| 3074878 | 서울특별시 강동구 | - | - | - | - | - | 2026-04-15 | external link: https://www.gangdong.go.kr/web/newportal/bbs/b_098 |
| 15166534 | 서울특별시 강서구 | 81 | n | Y | Y | utf8-bom | 2026-09-19 |  |
| 15052329 | 서울특별시 서초구 | 203 | n | Y | Y | cp949 | 2026-02-02 |  |
| 3077850 | 서울특별시 용산구 | 65 | n | Y | n | cp949 | 2026-07-01 | survey-target list, no flag |
| 3076234 | 울산광역시 남구 | 111 | n | Y | n | cp949 | 2026-09-21 | survey-target list, no flag |
| 3073073 | 인천광역시 계양구 | 361 | n | Y | n | cp949 | 2026-04-20 | survey-target list, no flag |
| 3073220 | 인천광역시 연수구 | 36 | n | Y | Y | cp949 | 2026-03-17 |  |
| 15038683 | 인천광역시 영종구 | 375 | n | Y | Y | utf8-bom | 2025-07-24 |  |
| 3072921 | 인천광역시 제물포구 | 359 | n | Y | Y | cp949 | 2026-08-20 |  |
| 15086967 | 전남광주통합특별시 광산구 | 47 | n | n | n | cp949 | 2026-06-23 | survey-target list, no flag; no street address: only 시도/시군구/법정동/행정동 + name |
| 3074513 | 전북특별자치도 고창군 | 27 | n | Y | n | cp949 | 2026-06-02 | survey-target list, no flag |
| 3068148 | 전북특별자치도 군산시 | 109 | n | Y | n | cp949 | 2026-04-28 | survey-target list, no flag |
| 3077425 | 전북특별자치도 장수군 | 119 | n | Y | n | cp949 | 2026-05-29 | survey-target list, no flag |
| 15054349 | 전북특별자치도 정읍시 | 96 | n | Y | n | cp949 | 2025-12-09 | survey-target list, no flag |
| 15054350 | 전북특별자치도 정읍시 | 205 | n | Y | n | cp949 | 2025-12-09 | survey-target list, no flag |
| 3075177 | 충청남도 계룡시 | 9 | n | Y | n | cp949 | 2026-10-08 | survey-target list, no flag |
| 3073034 | 충청남도 아산시 | 486 | n | Y | Y | cp949 | 2025-11-27 |  |
| 3071052 | 충청남도 천안시 | 243 | n | Y | n | cp949 | 2026-03-11 | survey-target list, no flag |
| 3046085 | 충청북도 괴산군 | 53 | n | Y | n | cp949 | 2022-09-28 | survey-target list, no flag |

Not downloaded (same search, different content, 57 entries in `sources.json` with `category` != building_list): 석면해체·제거 작업/사업장 records (부산 남·동·북·중·동래·금정, 대구 서·동·북, 대전, 인천 남동·미추홀·부평, 경기 하남·남양주, 경북 경주, 제주 서귀포, 산업안전보건공단 15087454 etc.), 비산 측정, 석면조사기관/해체제거업자 lists (고용노동부 15068766, 15002428), 피해구제 stats (한국환경산업기술원), 슬레이트 (양주 15160950, 충남 15096348), 폐기물 신고현황 (keyword noise). 4 OpenAPI hits (15110469 대구 동구 해체제거, 15095998 대구 북구 해체제거, 3069084 전주 석면해체 일정, 15158928 부산교통공사 공기질) are removal-work/air-quality, not building inventories. Four fileData that look like building lists are not: 3046208 달성군 (removal jobs, has lat/lng), 3043637 부산 연제구 (demolition sites), 3046763 인천 (per-district counts only), plus 3038626 영도 공공건축물 석면조사결과 (11 rows, name + 용도 only, no address). 15086967 광산구 has no street address (시도/시군구/법정동/행정동 + name).

Name hazards for the join: 2026 administrative renames appear in the data: 전남광주통합특별시 (광주광역시 + 전라남도; also appears as 광주광역시 in older files), 강원특별자치도 vs 강원도, 전북특별자치도 vs 전라북도, 인천 영종구/제물포구 (formerly 중구; datasets titled "중구" are published under these new names). Use 시군구 code or alias tables, and expect 2018 boundary files to lack the new districts.

## 4. National / non-data.go.kr sources

| Source | Content | Access | Verdict |
|---|---|---|---|
| 석면관리 종합정보망 (환경부; 한국환경공단 operates; host `asbestos.me.go.kr`, resolves to 210.99.81.87) | Statutory register: 석면건축물 관리대장 for all surveyed buildings incl. private, owners must keep it updated | HTTP 301 to HTTPS; **TLS certificate expired** (as of 2026-11-18) so curl refuses; no public bulk download or API found | **View-only / do not rely.** The public-building subset is exactly what 환경공단 republishes as 15092323. Private-building data only via 지자체 files. Not scraped (would require bypassing TLS verification and likely a login). |
| 한국환경공단 15092323 | public buildings nationwide | keyless file download | **Use** (backbone). |
| 전국석면조사건축물표준데이터 15155665 | merged 지자체 standard data, with coordinates | keyless JSON; OpenAPI with key | **Use**; grows as more 지자체 submit (19 now). |
| 지자체 files (53) | 다중이용시설, 의료, 어린이집 etc. | keyless file download | **Use**. |
| 학교 석면 (교육부 / 시도교육청 / 학교알리미) | school removal status | No dataset on data.go.kr; no bulk file found; reported figures come from 정보공개청구 (e.g. 전국학교석면학부모네트워크) and news | **Not available**; would need a 정보공개 request. |
| 서울 열린데이터광장 OA-1119 / OA-15383 | Seoul public buildings | page 200 keyless; API needs free key; file download mechanism not resolved | Optional, overlaps 15092323. |
| 공공건축물 (other) | covered by 15092323 | - | - |

How to get a data.go.kr serviceKey (only if the OpenAPIs are wanted): sign up (free) at data.go.kr, open the dataset's 오픈API tab, click 활용신청 (use purpose, auto-approved for most), then 마이페이지 > 개발계정 > 일반 인증키 (Encoding/Decoding). Keys can take up to ~1 h to activate; development accounts are typically capped at 10,000 calls/day.

## 5. Geocoding address-only rows

About 97% of rows (all of 15092323 and most district files) have an address but no coordinates. Key/limit facts below marked (verified) were probed on 2026-11-18; the rest are from provider documentation as I know it and should be confirmed when signing up.

| Option | Key | Free quota / limit | Notes |
|---|---|---|---|
| 행안부 juso 도로명주소 API (business.juso.go.kr): 검색 `addrLinkApi.do` then 좌표제공 `addrCoordApi.do` | Yes, free `confmKey` per API (verified: endpoint answers E0002 without admCd; needs a key) | No hard daily cap documented beyond fair use; request pacing advised | Best accuracy for road addresses (entrance coordinates, in UTM-K/EPSG:5179, convert to WGS84 with proj4). Two-step: search returns admCd, rnMgtSn, udrtYn, buldMnnm, buldSlno which the coordinate API needs. Takes road-name address only (not lot/jibun). |
| VWorld geocoder (`api.vworld.kr/req/address?service=address&request=getcoord&type=road|parcel`) | Yes, free key from vworld.kr (verified: `PARAM_REQUIRED key`) | ~40,000 requests/day on a standard key (unverified) | One call, accepts road and parcel addresses, returns WGS84 (EPSG:4326) directly. **Recommended primary.** |
| Kakao Local `dapi.kakao.com/v2/local/search/address.json` | Yes, REST API key via Kakao Developers (verified: `AccessDeniedError ... KakaoAK header`) | ~100,000 calls/day for local search (unverified); one app free | Good fuzzy matching, also handles "건물명" via keyword search (`/search/keyword.json`). Terms restrict storing/using results outside Kakao maps, check before publishing a map on another base layer. |
| Nominatim (OSM) `nominatim.openstreetmap.org/search?format=jsonv2&countrycodes=kr&q=...` | **None** (verified) | 1 request/s max, identifying User-Agent, no bulk use | **Keyless fallback only**: for 3047280's "공원순환로 201" and 3068148's "수산길 71-21" it returned the *street* geometry, not the building, so precision is street-level (tens to hundreds of metres), and house numbers are often ignored. Fine to place a dot at district level, not at the building. |
| Provider coordinates | - | - | 3047280, 15103460 and the 15155665 rows already have WGS84 lat/lng; use them as-is and as a test set to measure geocoder error. |

Recommendation: (1) keep native coordinates; (2) strip bracket suffixes `(성당동)`/trailing building names, normalise 시도 aliases, de-duplicate addresses (15092323 has 22,402 distinct addresses for 33,482 rows, so ~33% fewer calls); (3) geocode the unique road addresses with VWorld (fallback Kakao, then juso for failures); (4) cache results in `data/processed/geocode_cache.json` keyed by normalised address; (5) use Nominatim only for the 1-2 s/row emergency path. Ask the user for a VWorld key (and optionally Kakao) before the geocoding step.

## 6. Boundary GeoJSON

Verified downloads (GitHub raw, no key), saved under `data/boundaries/`:

| File | URL | Size | Notes |
|---|---|---|---|
| 시·군·구 (TopoJSON, simplified) | https://raw.githubusercontent.com/southkorea/southkorea-maps/master/kostat/2018/json/skorea-municipalities-2018-topo-simple.json | 553 KB (downloaded) | 250 features, properties `code` (5-digit, e.g. 11010), `name`, `name_eng`; object name `skorea_municipalities_2018_geo`. 2018 vintage: no 2026 renames. |
| 시·도 (TopoJSON, simplified) | https://raw.githubusercontent.com/southkorea/southkorea-maps/master/kostat/2018/json/skorea-provinces-2018-topo-simple.json | 219 KB (downloaded) | |
| 시·군·구 full-resolution GeoJSON | https://raw.githubusercontent.com/southkorea/southkorea-maps/master/kostat/2018/json/skorea-municipalities-2018-geo.json | 18 MB (exists per GitHub API, not downloaded) | Note filename ends `-geo.json`, not `-geojson.json` (the latter 404s). |
| 읍면동 current (admdongkor) | https://raw.githubusercontent.com/vuski/admdongkor/master/ver20261001/HangJeongDong_ver20261001.geojson | 34.7 MB (first bytes verified) | Properties `adm_nm`, `adm_cd2`, `sgg` (시군구 5-digit), `sido`, `sidonm`, `sggnm`, `adm_cd`. Newest versions (ver20260701, ver20261001) should carry the 2026 district changes; dissolve by `sgg` to get 시·군·구. Older dated folders exist back to 2012. |

TopoJSON must be converted in the browser (topojson-client `feature()` from cdn.jsdelivr.net) or with a small script. Prefer admdongkor dissolved by `sgg` if the new 전남광주/인천 districts matter.

## 7. Open items

- Verify Seoul `datafile.seoul.go.kr` download seq (low priority, overlaps 15092323).
- Dedupe across sources (15092323 vs district files vs 15155665) by normalised road address + building name.
- Re-run `scripts/fetch_datago.py get <ids>` periodically; `updated` is in `sources.json`. The `atchFileId` is re-resolved on every run.
- Keys needed before geocoding: VWorld (primary), optionally Kakao and juso.
