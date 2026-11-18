#!/usr/bin/env python3
"""Download every building-list dataset in data/sources.json into data/raw/ (keyless, 1 req/s).

 - fileData ids: 4-step data.go.kr flow from fetch_datago.py; skipped when the live page's dateModified equals
   the recorded `updated` and the local file exists.
 - 15155665 (전국석면조사건축물표준데이터): keyless /download/standard.json endpoint, paged.
 - State: data/raw/_fetch_state.json  {id: {updated, file, bytes, rows, fetched_at}}.
Usage: fetch_all.py [--force] [--dry-run] [--ids ID ...]
"""
import argparse, json, os, re, sys, time, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_datago as fd
from common import RAW, ROOT, SOURCES_JSON, write_json

STATE = os.path.join(RAW, "_fetch_state.json")
STD_ID = "15155665"
SLEEP = 1.0


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
            r = fetch_standard(pk, state, a.force, a.dry_run) if pk == STD_ID or s["kind"] == "standard" \
                else fetch_file(pk, s, state, a.force, a.dry_run)
        except Exception as e:  # keep going, report at the end
            log("%-9s ERROR %r" % (pk, e))
            r = "error"
        res[r] = res.get(r, 0) + 1
        json.dump(state, open(STATE, "w"), ensure_ascii=False, indent=1)
    log("done:", res)
    return 1 if res.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
