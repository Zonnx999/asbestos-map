#!/usr/bin/env python3
"""Classify rows still without a point after applying the geocode cache -> data/processed/unplaced.csv + counts.
Categories: A_no_addr, B_no_query (addr without number), C_never_queried, D_miss, E_reject.
Usage: classify_unplaced.py [--quiet]"""
import csv, json, os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PROC, CACHE, Geo, Region, geocode_query, place_key, PLACE_OK


def main():
    geo, region = Geo(), Region(Geo())
    cache = json.load(open(os.path.join(CACHE, "geocode.json"), encoding="utf-8"))
    out, cnt = [], collections.Counter()
    for r in csv.DictReader(open(os.path.join(PROC, "buildings.csv"), encoding="utf-8")):
        if r["lat"]:
            continue
        q = geocode_query(region, geo, r["addr"], r["sido"], r["sgg"]) if r["addr"] else ""
        hit = cache.get(q) if q else None
        if not hit or hit.get("p") in ("miss", "reject"):
            hit2 = cache.get(place_key(r["sgg"], r["name"]))
            if hit2 and hit2.get("p") in PLACE_OK and geo.in_bbox(hit2["lat"], hit2["lng"], sgg=r["sgg"] or None, sido=r["sido"], pad=0.02):
                hit = hit2
        if hit and hit.get("lat") is not None and hit["p"] in ("vworld", "kakao", "vworld-place") and \
                geo.in_bbox(hit["lat"], hit["lng"], sgg=r["sgg"] or None, sido=r["sido"], pad=0.02):
            continue
        if not r["addr"]:
            cat = "A_no_addr"
        elif not q:
            cat = "B_no_query"
        elif q not in cache:
            cat = "C_never_queried"
        elif cache[q]["p"] == "reject":
            cat = "E_reject"
        else:
            cat = "D_miss"
        cnt[cat] += 1
        out.append([cat, r["src"], r["sido"], r["sgg"], r["name"], r["addr"], q])
    w = csv.writer(open(os.path.join(PROC, "unplaced.csv"), "w", encoding="utf-8", newline=""))
    w.writerow(["cat", "src", "sido", "sgg", "name", "addr", "query"])
    w.writerows(out)
    print(len(out), dict(sorted(cnt.items())))


if __name__ == "__main__":
    main()
