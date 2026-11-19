#!/usr/bin/env python3
"""Download every building-list dataset in data/sources.json into data/raw/ (keyless, 1 req/s).

 - fileData ids: 4-step data.go.kr flow from fetch_datago.py; skipped when the live page's dateModified equals
   the recorded `updated` and the local file exists.
 - 15155665 (전국석면조사건축물표준데이터): keyless /download/standard.json endpoint, paged.
 - State: data/raw/_fetch_state.json  {id: {updated, file, bytes, rows, fetched_at}}.
Usage: fetch_all.py [--force] [--dry-run] [--ids ID ...]

Seoul Open Data Plaza sheets (15048930=OA-15383, 15048932=OA-15384, 15072370=OA-1119; data.go.kr only links out):
 keyless. The plaza's own grid reads /dataList/dataView.do (JS-object JSON, 1000 rows/page) after the
 datasetView + sheetView pages set the session; the CSV button's datafile.seoul.go.kr POST rejects scripted calls
 ("잘못된 접근"), so rows are paged and written as UTF-8-BOM CSV with the grid's Korean column labels.
 Optional: env SEOUL_KEY (openapi.seoul.go.kr:8088/{KEY}/json/{SERVICE}/..) is NOT needed and not used.
강동구 3074878: the bulletin board gangdong.go.kr/web/newportal/bbs/b_098 (구소유 석면조사 결과, one post per building:
 title = building name, 석면여부 = 석면검출/미검출; no address) is scraped to a CSV.
"""
import argparse, json, os, re, sys, time, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_datago as fd
from common import RAW, ROOT, SOURCES_JSON, write_json

STATE = os.path.join(RAW, "_fetch_state.json")
STD_ID = "15155665"
SLEEP = 1.0
SEOUL_SHEETS = {"15048930": "OA-15383", "15048932": "OA-15384", "15072370": "OA-1119"}
GANGDONG_ID = "3074878"
GANGDONG_URL = "https://www.gangdong.go.kr/web/newportal/bbs/b_098"


def log(*a):
    print(*a, flush=True)


def fetch_file(pk, src, state, force, dry):
    pi = fd.page_info(pk)
    time.sleep(SLEEP)
    ld = pi["ld"]
    live = ld.get("dateModified")
    publisher = (ld.get("creator") or {}).get("name") or src.get("publisher") or ""
    if not pi["files"]:
        log("%-9s external link only, nothing to download (%s)" % (pk, src.get("notes") or ""))
        return "external"
    rec = state.get(pk)
    local = os.path.join(ROOT, src["local_file"]) if src.get("local_file") else None
    known = (rec or {}).get("updated") or (src.get("updated") if local and os.path.exists(local) else None)
    have = (rec and os.path.exists(os.path.join(RAW, rec["file"]))) or (local and os.path.exists(local))
    if not force and have and live and known == live:
        if not rec and local:  # bootstrap state from scout's download
            state[pk] = dict(updated=live, file=os.path.basename(local), bytes=os.path.getsize(local), fetched_at="bootstrap")
        log("%-9s unchanged (%s), skipped" % (pk, live))
        return "skipped"
    if dry:
        log("%-9s would download (live %s, known %s)" % (pk, live, known))
        return "would"
    p, uddi, atch, sn, hist = pi["files"][0]
    fi = fd.file_info(pk, uddi, sn or "1")
    time.sleep(SLEEP)
    if not fi.get("status"):
        log("%-9s file_info failed: %s" % (pk, str(fi)[:120]))
        return "error"
    v = fi.get("fileDataRegistVO", {})
    ext = (v.get("atchFileExtsn") or "csv").lower()
    b, hd = fd.download(pk, fi["atchFileId"], fi.get("fileDetailSn") or "1", v.get("dataNm") or pk)
    time.sleep(SLEEP)
    fn = "%s_%s.%s" % (pk, fd.safe(publisher), ext)
    old = rec["file"] if rec else (os.path.basename(local) if local else None)
    if old and old != fn and os.path.exists(os.path.join(RAW, old)):
        os.remove(os.path.join(RAW, old))
    open(os.path.join(RAW, fn), "wb").write(b)
    state[pk] = dict(updated=live, file=fn, bytes=len(b), rows=v.get("atchFileCo"), encoding=fd.sniff_encoding(b),
                     fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    log("%-9s downloaded %s (%d bytes, %s)" % (pk, fn, len(b), state[pk]["encoding"]))
    return "downloaded"


def fetch_standard(pk, state, force, dry):
    page = fd.req("https://www.data.go.kr/data/%s/standard.do" % pk).read().decode("utf-8", "replace")
    time.sleep(SLEEP)
    m = re.search(r'"dateModified"\s*:\s*"([^"]+)"', page)
    live = m.group(1) if m else None
    cl = json.loads(fd.req("https://www.data.go.kr/download/columList.json?pk=%s&ext=JSON" % pk).read().decode())
    time.sleep(SLEEP)
    total = int(cl.get("totalCount") or 0)
    tv = cl.get("tableVO") or {}
    cols, tbl = tv.get("colNmList") or cl.get("columList"), tv.get("svcTableNm")
    rec = state.get(pk)
    fn = "%s_전국석면조사건축물표준데이터.json" % pk
    exists = os.path.exists(os.path.join(RAW, fn))
    if not force and exists and live and rec and rec.get("updated") == live and rec.get("rows") == total:
        log("%-9s unchanged (%s, %d rows), skipped" % (pk, live, total))
        return "skipped"
    if not force and exists and not rec and live and live == "2026-11-02" and total == 1525:
        state[pk] = dict(updated=live, file=fn, rows=total, bytes=os.path.getsize(os.path.join(RAW, fn)), fetched_at="bootstrap")
        log("%-9s unchanged (%s), skipped" % (pk, live))
        return "skipped"
    if dry:
        log("%-9s would download (live %s, %d rows)" % (pk, live, total))
        return "would"
    out, page_no, per = [], 1, 10000
    while (page_no - 1) * per < max(total, 1):
        q = [("publicDataPk", pk), ("svcTableNm", tbl), ("totalCount", total), ("perPage", per), ("page", page_no)]
        q += [("colNmList", c) for c in cols]
        data = json.loads(fd.req("https://www.data.go.kr/download/standard.json?" + urllib.parse.urlencode(q)).read().decode("utf-8"))
        time.sleep(SLEEP)
        out.extend(data if isinstance(data, list) else data.get("data", []))
        page_no += 1
    write_json(os.path.join(RAW, fn), out)
    state[pk] = dict(updated=live, file=fn, rows=len(out), bytes=os.path.getsize(os.path.join(RAW, fn)),
                     fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    log("%-9s downloaded %s (%d rows)" % (pk, fn, len(out)))
    return "downloaded"


def _js_obj(text):
    """The plaza returns a JS object literal ({result:"ok",page:{..},list:[{K:"v"}]}); quote bare keys -> json."""
    fixed = re.sub(r'"(?:\\.|[^"\\])*"|([{,]\s*)([A-Za-z_]\w*)\s*:',
                   lambda m: m.group(0) if m.group(2) is None else '%s"%s":' % (m.group(1), m.group(2)), text)
    return json.loads(re.sub(r',\s*([\]}])\s*$', r'\1', fixed.strip()).replace(',]}', ']}'), strict=False)


def _write_csv(path, header, rows):
    import csv
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def fetch_seoul(pk, state, force, dry):
    inf = SEOUL_SHEETS[pk]
    ref = "https://data.seoul.go.kr/dataList/%s/L/1/datasetView.do" % inf
    if dry:
        log("%-9s would page %s from data.seoul.go.kr" % (pk, inf))
        return "would"
    view = fd.req(ref).read().decode("utf-8", "replace")  # session cookie + 갱신일 (last update)
    time.sleep(SLEEP)
    um = re.search(r"갱신일[^0-9]{0,300}?(\d{4})\.(\d{2})\.(\d{2})", re.sub(r"<[^>]+>", " ", view), re.S)
    upd = "%s-%s-%s" % um.groups() if um else time.strftime("%Y-%m-%d")
    hdrs = {"Referer": ref, "X-Requested-With": "XMLHttpRequest"}
    form = dict(srvType="S", infId=inf, serviceKind="1")
    sv = fd.req("https://data.seoul.go.kr/dataList/sheetView.do?infId=%s&srvType=S" % inf, form, hdrs).read().decode("utf-8", "replace")
    time.sleep(SLEEP)
    cols = re.findall(r'\{key:"([A-Z_0-9]+)",\s*label:"([^"]*)"', sv)
    # the plaza's default ORDER BY (a date) has many ties, so offset paging repeats/misses rows:
    # order by (nearly) every column instead, which makes the order total and the paging exact
    order = ",".join(k for k, _ in cols if k not in ("MTRL_PHOTO_1", "MTRL_PHOTO_2", "MNG_MSRS", "RPTP"))
    if not cols:
        log("%-9s sheet columns not found" % pk)
        return "error"
    rows, page, total, got = [], 1, None, set()
    while True:
        q = dict(onepagerow=1000, srvType="S", infId=inf, serviceKind="1", pageNo=page, ssUserId="SAMPLE_VIEW",
                 strWhere="", strOrderby=order, filterCol="", txtFilter="")
        raw = fd.req("https://data.seoul.go.kr/dataList/dataView.do?" + urllib.parse.urlencode(q), None, hdrs).read().decode("utf-8", "replace")
        time.sleep(SLEEP)
        j = _js_obj(raw.strip())
        total = int(j["page"]["totalCount"])
        rows += j["list"]
        if len(rows) >= total or not j["list"] or page > 400:
            break
        page += 10  # pageNo offsets by 100 rows even with onepagerow=1000 (max 1000 rows/page)0  # the plaza's pageNo offsets by 100 rows even with onepagerow=1000
    # personal data (owner, contact person, phone) is never written to disk
    cols = [(k, l) for k, l in cols if k not in ("OWNR", "PIC_NM", "CNPL")]
    keys = [k for k, _ in cols]
    out = [[("" if r.get(c) in (None, "null") else str(r.get(c))) for c in keys] for r in rows]
    if len(out) != total:
        log("%-9s WARNING: got %d rows, plaza says %d" % (pk, len(out), total))
    fn = "%s_서울특별시.csv" % pk
    _write_csv(os.path.join(RAW, fn), [l for _, l in cols], out)
    state[pk] = dict(updated=upd, file=fn, rows=len(out), total=total, bytes=os.path.getsize(os.path.join(RAW, fn)),
                     encoding="utf-8-sig", fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S"), via="data.seoul.go.kr dataView.do")
    log("%-9s downloaded %s (%d of %d rows)" % (pk, fn, len(out), total))
    return "downloaded"


def fetch_gangdong(pk, state, force, dry):
    import html as H
    if dry:
        log("%-9s would scrape %s" % (pk, GANGDONG_URL))
        return "would"
    url = GANGDONG_URL + "?cp=1&pageSize=100&sortOrder=BA_REGDATE&sortDirection=DESC&bcId=b_098&baNotice=false&baCommSelec=false&baOpenDay=true&baUse=true"
    out, cp = [], 1
    while True:
        t = fd.req(url.replace("cp=1", "cp=%d" % cp)).read().decode("utf-8", "replace")
        time.sleep(SLEEP)
        total = int(re.search(r"총\s*(?:<[^>]+>\s*)*(\d+)", t).group(1))
        n0 = len(out)
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
            m = re.search(r'<td[^>]*>\s*(\d+)\s*</td>.*?bbs/b_098/(\d+)[^"]*"[^>]*>(.*?)</a>.*?(미검출|석면검출)\s*</td>.*?<td[^>]*>\s*([^<]*?)\s*</td>', tr, re.S)
            if m:
                out.append([m.group(1), H.unescape(re.sub(r"<[^>]+>|\s+", " ", m.group(3))).strip(), m.group(4), m.group(5).strip(), m.group(2)])
        if len(out) >= total or len(out) == n0 or cp >= 10:
            break
        cp += 1
    if not out:
        log("%-9s no rows parsed" % pk)
        return "error"
    fn = "%s_서울특별시강동구.csv" % pk
    _write_csv(os.path.join(RAW, fn), ["번호", "건축물명", "석면여부", "작성부서", "게시물ID"], out)
    state[pk] = dict(updated=time.strftime("%Y-%m-%d"), file=fn, rows=len(out), total=total, bytes=os.path.getsize(os.path.join(RAW, fn)),
                     encoding="utf-8-sig", fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S"), via="gangdong.go.kr bbs b_098")
    log("%-9s downloaded %s (%d of %d rows)" % (pk, fn, len(out), total))
    return "downloaded"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ids", nargs="*")
    a = ap.parse_args()
    srcs = [s for s in json.load(open(SOURCES_JSON)) if s["category"] == "building_list" and s["kind"] in ("fileData", "standard")]
    if a.ids:
        srcs = [s for s in srcs if s["id"] in a.ids]
    os.makedirs(RAW, exist_ok=True)
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    res = {}
    for s in srcs:
        pk = s["id"]
        try:
            if pk in SEOUL_SHEETS:
                r = fetch_seoul(pk, state, a.force, a.dry_run)
            elif pk == GANGDONG_ID:
                r = fetch_gangdong(pk, state, a.force, a.dry_run)
            elif pk == STD_ID or s["kind"] == "standard":
                r = fetch_standard(pk, state, a.force, a.dry_run)
            else:
                r = fetch_file(pk, s, state, a.force, a.dry_run)
        except Exception as e:  # keep going, report at the end
            log("%-9s ERROR %r" % (pk, e))
            r = "error"
        res[r] = res.get(r, 0) + 1
        json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)
    log("done:", res)
    return 1 if res.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
