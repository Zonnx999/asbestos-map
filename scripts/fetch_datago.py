#!/usr/bin/env python3
"""Download data.go.kr fileData datasets without login (Python 3.9 stdlib).

Mechanism (verified 2026-11-18):
 1. GET  /data/{id}/fileData.do                       -> cookie (JSESSIONID) + publicDataDetailPk (uddi:...) + JSON-LD metadata
 2. POST /tcs/dss/selectFileDataDownload.do            (publicDataPk, publicDataDetailPk, atchFileId='', fileDetailSn, publicDataTyCode=PR0051)
        -> JSON {status:true, atchFileId:"FILE_...", fileDetailSn, fileDataRegistVO:{atchFileExtsn, orginlFileNm, atchFileCo(rows)}}
 3. POST /cmm/cmm/check-limit.json (atchFileId, fileDetailSn) -> {needCaptcha:false}  (true => stop; captcha after many downloads)
 4. GET  /cmm/cmm/fileDownload.do?atchFileId=..&fileDetailSn=..&dataNm=..  -> raw bytes
Usage: fetch_datago.py meta  IDS...   (metadata only)   |   fetch_datago.py get IDS...   (download)
IDs are read from data/search/results.json when none given.
"""
import json, os, re, sys, time, html, http.cookiejar, urllib.parse, urllib.request

UA = "Mozilla/5.0 (compatible; asbestos-map-research/0.1)"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAW = os.path.join(ROOT, "data", "raw")
META = os.path.join(ROOT, "data", "search", "meta.json")
SLEEP = 1.0

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.addheaders = [("User-Agent", UA)]

def req(url, data=None, headers=None, timeout=90):
    r = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None,
                               headers=headers or {})
    return op.open(r, timeout=timeout)

def page_info(pk):
    url = "https://www.data.go.kr/data/%s/fileData.do" % pk
    t = req(url).read().decode("utf-8", "replace")
    ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', t, re.S)
    ld = json.loads(ld.group(1)) if ld else {}
    files = re.findall(r"fn_fileDataDown\('(\d+)',\s*'([^']*)',\s*'([^']*)',\s*'(\d*)',\s*'(\d*)'\)", t)
    return dict(ld=ld, files=files, html=t)

def file_info(pk, uddi, sn):
    h = {"X-Requested-With": "XMLHttpRequest", "Referer": "https://www.data.go.kr/data/%s/fileData.do" % pk}
    d = json.loads(req("https://www.data.go.kr/tcs/dss/selectFileDataDownload.do",
        dict(publicDataPk=pk, publicDataDetailPk=uddi, atchFileId="", fileDetailSn=sn, publicDataTyCode="PR0051"), h).read().decode())
    return d

def download(pk, atch, sn, name):
    h = {"X-Requested-With": "XMLHttpRequest"}
    lim = json.loads(req("https://www.data.go.kr/cmm/cmm/check-limit.json", dict(atchFileId=atch, fileDetailSn=sn), h).read().decode())
    if lim.get("needCaptcha"):
        raise RuntimeError("captcha required (download limit)")
    u = "https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=%s&fileDetailSn=%s&dataNm=%s" % (atch, sn, urllib.parse.quote(name))
    resp = req(u)
    return resp.read(), resp.headers

def sniff_encoding(b):
    if b[:3] == b"\xef\xbb\xbf": return "utf-8-sig"
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"): return "utf-16"
    if b[:2] == b"PK": return "zip(xlsx/hwpx)"
    if b[:4] == b"\xd0\xcf\x11\xe0": return "ole(xls/hwp)"
    if b[:4] == b"%PDF": return "pdf"
    try: b.decode("utf-8"); return "utf-8"
    except UnicodeDecodeError: pass
    try: b.decode("cp949"); return "cp949"
    except UnicodeDecodeError: return "unknown"

def safe(s):
    return re.sub(r'[\\/:*?"<>|\s]+', "", s)

def main():
    mode = sys.argv[1]
    ids = sys.argv[2:]
    meta = json.load(open(META)) if os.path.exists(META) else {}
    os.makedirs(RAW, exist_ok=True)
    for pk in ids:
        try:
            pi = page_info(pk)
            time.sleep(SLEEP)
            ld = pi["ld"]
            rec = meta.setdefault(pk, {})
            rec.update(title=ld.get("name"), publisher=(ld.get("creator") or {}).get("name"),
                       updated=ld.get("dateModified"), created=ld.get("dateCreated"),
                       columns=[v["name"] for v in ld.get("variableMeasured", [])], format=ld.get("encodingFormat"),
                       description=ld.get("description"), files=[])
            for (p, uddi, atch, sn, hist) in pi["files"] or []:
                fi = file_info(pk, uddi, sn or "1"); time.sleep(SLEEP)
                v = fi.get("fileDataRegistVO", {})
                ent = dict(atchFileId=fi.get("atchFileId"), sn=fi.get("fileDetailSn"), ext=v.get("atchFileExtsn"),
                           orig_name=v.get("orginlFileNm"), rows=v.get("atchFileCo"), dataNm=v.get("dataNm"),
                           etc=(fi.get("dataSetFileDetailInfo") or {}).get("etcAtentMatter"))
                if mode == "get" and fi.get("status"):
                    b, hd = download(pk, ent["atchFileId"], ent["sn"], ent["dataNm"] or pk)
                    ext = (ent["ext"] or "csv").lower()
                    pub = safe(rec["publisher"] or "")
                    suffix = "" if len(pi["files"]) == 1 else "_f%s" % ent["sn"]
                    fn = os.path.join(RAW, "%s_%s%s.%s" % (pk, pub, suffix, ext))
                    open(fn, "wb").write(b)
                    ent.update(saved=os.path.relpath(fn, ROOT), bytes=len(b), encoding=sniff_encoding(b))
                    time.sleep(SLEEP)
                rec["files"].append(ent)
            print(pk, rec["publisher"], "|", rec["title"], "|", [(f["ext"], f["rows"], f.get("encoding")) for f in rec["files"]], flush=True)
        except Exception as e:
            print(pk, "ERROR", repr(e), flush=True)
        json.dump(meta, open(META, "w"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
