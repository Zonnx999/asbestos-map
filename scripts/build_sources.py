#!/usr/bin/env python3
"""Merge data/search/{results,meta,raw_summary}.json -> data/sources.json"""
import json, os, re
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
L = lambda n: json.load(open(os.path.join(ROOT, "data", "search", n)))
res, meta, raw = L("results.json"), L("meta.json"), L("raw_summary.json")
SIDO = r"^(서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|경기도|강원특별자치도|강원도|충청북도|충청남도|전북특별자치도|전라북도|전남광주통합특별시|전라남도|경상북도|경상남도|제주특별자치도)\s*(.*)$"
def split_pub(p):
    m = re.match(SIDO, p or "")
    if m: return m.group(1), m.group(2) or None
    return None, None
CAT_OTHER = r"해체|제거|비산|업자|피해|정도관리|기관 지정|감리|분담금|폐기물|교역|공기질|슬레이트|광종|홍보|공사 대가|보건관리|안전점검|농도|산업보건|실내공기질|컨설팅|평가항목|안정성|실시공고|특별유족|인정|헤체|측정결과"
OVERRIDE = {"3043637": "demolition_work", "3046208": "demolition_work", "3046763": "summary_stats",
            "15172450": "demolition_work", "15172454": "other_asbestos"}
EXT_URL = {"15072370": "http://data.seoul.go.kr/dataList/OA-1119/S/1/datasetView.do",
           "15048930": "http://data.seoul.go.kr/dataList/OA-15383/S/1/datasetView.do",
           "15048932": "http://data.seoul.go.kr/dataList/OA-15384/S/1/datasetView.do",
           "3074878": "https://www.gangdong.go.kr/web/newportal/bbs/b_098"}
has = lambda hs, rx: any(re.search(rx, h) for h in hs)
out = []
for r in sorted(res, key=lambda x: (x["kind"], x["publisher"], x["id"])):
    pk = r["id"]; m = meta.get(pk, {}); rw = raw.get(pk, {})
    hs = rw.get("header") or m.get("columns") or []
    sido, sgg = split_pub(r["publisher"])
    if r["publisher"] == "한국환경공단": sido, sgg = "전국", None
    if r["kind"] == "standard": sido, sgg = "전국", None
    cat = OVERRIDE.get(pk) or ("building_list" if not re.search(CAT_OTHER, r["title"]) else "other_asbestos")
    if r["kind"] == "openapi": cat = "other_asbestos"
    if pk == "15155665" or r["kind"] == "standard": cat = "building_list"
    page = {"fileData": "https://www.data.go.kr/data/%s/fileData.do", "openapi": "https://www.data.go.kr/data/%s/openapi.do",
            "standard": "https://www.data.go.kr/data/%s/standard.do"}[r["kind"]] % pk
    f = (m.get("files") or [None])[0]
    dl = None
    if r["kind"] == "fileData" and f and f.get("atchFileId"):
        dl = "https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=%s&fileDetailSn=%s&dataNm=x" % (f["atchFileId"], f["sn"])
    if pk == "15155665": dl = "https://www.data.go.kr/download/standard.json?colNmList=...&totalCount=1525&svcTableNm=tn_pubr_public_asbestos_survey_building_svc&perPage=10000&page=1&publicDataPk=15155665 (see docs)"
    if r["kind"] == "openapi": dl = "API key required (data.go.kr serviceKey)"
    note = []
    if pk in EXT_URL: note.append("data.go.kr only links out to the provider's own site: " + EXT_URL[pk])
    if rw.get("encoding") == "cp949": note.append("CP949")
    if rw.get("encoding") == "utf-8-sig": note.append("UTF-8 with BOM")
    if cat == "demolition_work": note.append("asbestos removal/demolition work sites, not a building inventory")
    if cat == "summary_stats": note.append("per-district counts only, no building rows")
    if cat == "building_list" and hs and not has(hs, r"석면\s*건축(물)?\s*여부|석면함유|SBST_INSP_YN|조사결과|위해성") and pk != "15155665":
        note.append("no asbestos yes/no column: list of survey-target buildings (presence unconfirmed)")
    if pk == "3077221": note.append("CR-only line endings; lists buildings where the survey was not yet done")
    if pk == "15086967": note.append("no street address: only 시도/시군구/법정동/행정동 + name")
    if has(hs, r"면적") and cat == "building_list": note.append("has asbestos-area/floor-area column")
    out.append(dict(
        id=pk, publisher=r["publisher"] or None, sido=sido, sigungu=sgg, title=r["title"],
        kind=r["kind"], category=cat, format=(r["ext"] or None),
        url_page=page, url_download=dl,
        has_coords=(has(hs, r"위도|경도|LAT|LOT") if hs else None),
        has_address=(has(hs, r"주소|소재지|도로명|대지위치|세부위치|ADDR") if hs else None),
        has_asbestos_flag=(has(hs, r"석면\s*건축(물)?\s*여부|석면함유|SBST_INSP_YN|조사결과|위해성") if hs else None) if cat == "building_list" else None,
        row_count_if_known=(rw.get("rows") or (int(f["rows"]) if f and (f.get("rows") or "").isdigit() else None)),
        updated=r["updated"] or None, encoding=rw.get("encoding"), local_file=("data/raw/" + rw["file"]) if rw.get("file") else None,
        columns=hs or None, notes="; ".join(note) or None))
json.dump(out, open(os.path.join(ROOT, "data", "sources.json"), "w"), ensure_ascii=False, indent=1)
print(len(out), "entries;", sum(1 for o in out if o["local_file"]), "downloaded")
