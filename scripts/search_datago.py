#!/usr/bin/env python3
"""Enumerate data.go.kr search results for asbestos keywords (HTML scrape, no login).
Usage: python3 -I scripts/search_datago.py  -> data/search/results.json
"""
import json, re, sys, time, urllib.parse, urllib.request, html, os
UA = "Mozilla/5.0 (compatible; asbestos-map-research/0.1)"
BASE = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"
KEYWORDS = ["석면", "석면건축물", "석면조사대상", "석면 건축물", "석면조사", "석면 현황"]
DTYPES = ["FILE", "API", "STD", "LINKED"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "search")

def get(params):
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")

def strip(s):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s))).strip()

def parse(page):
    items = []
    for blk in re.split(r'<div class="apply-result-link">', page)[1:]:
        m = re.search(r'<a href="/data/(\d+)/(\w+)\.do">(.*?)</a>', blk, re.S)
        if not m: continue
        ext = re.search(r'data-ext="([^"]*)"', blk)
        org = re.search(r"<strong>제공기관</strong>(.*?)</li>", blk, re.S)
        upd = re.search(r"<strong>수정일</strong>(.*?)</li>", blk, re.S)
        kw = re.search(r"<strong>키워드</strong>(.*?)</li>", blk, re.S)
        items.append(dict(id=m.group(1), kind=m.group(2), title=strip(m.group(3)),
                          ext=ext.group(1) if ext else "", publisher=strip(org.group(1)) if org else "",
                          updated=strip(upd.group(1)) if upd else "", keywords=strip(kw.group(1)) if kw else ""))
    return items

def main():
    seen = {}
    for kw in KEYWORDS:
        for dt in DTYPES:
            page_no = 1
            while True:
                p = dict(sType="detail", keyword=kw, beforeKeyword=kw, size=100, perPage=100, dType=dt,
                         sort="score", currentPage=page_no)
                page = get(p)
                items = parse(page)
                tab = re.search(r'data-type="%s"[^>]*>\s*[^<]*<span>([^<]*)</span>' % dt, page)
                print(kw, dt, page_no, len(items), tab.group(1) if tab else "?", file=sys.stderr)
                for it in items:
                    it.setdefault("hit_by", [])
                    d = seen.setdefault(it["id"] + it["kind"], it)
                    if kw not in d["hit_by"]: d["hit_by"].append(kw)
                time.sleep(1.0)
                if len(items) < 100 or page_no >= 6: break
                page_no += 1
    os.makedirs(OUT, exist_ok=True)
    json.dump(list(seen.values()), open(os.path.join(OUT, "results.json"), "w"), ensure_ascii=False, indent=1)
    print(len(seen), "unique", file=sys.stderr)
main()
