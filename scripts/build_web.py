#!/usr/bin/env python3
"""Build web/data/ per docs/data-contract.md from data/processed/buildings.csv (+ data/cache/geocode.json if present).

Writes: manifest.json, summary/sigungu.json, sido/{code}.json, nocoord/{code}.json,
        boundaries/sido.geojson, boundaries/sigungu.geojson (simplified to <= 1.5 MB each).
Then validates that totals == sum(sigungu) == sum(sido) == rows in buildings.csv.
"""
import csv, datetime, json, os, shutil, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (PROC, CACHE, WEBDATA, TOPO_SGG, TOPO_SIDO, Geo, Region, geocode_query, place_key, PLACE_OK, load_topo, feat_bbox, write_json, CURRENT_SIDO_NAME)

MAX_BOUNDARY = 1_500_000
PLACE_FACILITY_HITS = False # accept name-search hits stored with "m":"head" (sub-building placed at its facility; ~65% precise)
SIDO_FIELDS = ["lat", "lng", "c", "name", "addr", "use", "year", "area", "src", "sgg", "geo"]
NOCOORD_FIELDS = ["c", "name", "addr", "use", "year", "src", "sgg"]


def geojson(topo, name, ndigits=4):
    """Simplify per-arc (topology preserving) until the file is <= MAX_BOUNDARY; write compact GeoJSON."""
    out = os.path.join(WEBDATA, "boundaries", name)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tol = 0.0
    for tol in (0.0, 0.0003, 0.0006, 0.001, 0.0015, 0.002, 0.003, 0.005):
        feats = load_topo(topo, simplify_deg=tol, ndigits=ndigits)
        fc = dict(type="FeatureCollection", features=[
            dict(type="Feature", properties=dict(code=f["properties"]["code"], name=CURRENT_SIDO_NAME.get(f["properties"]["code"], f["properties"]["name"])),
                 geometry=dict(type=f["type"], coordinates=f["coordinates"])) for f in feats])
        s = json.dumps(fc, ensure_ascii=False, separators=(",", ":"))
        if len(s.encode("utf-8")) <= MAX_BOUNDARY:
            break
    open(out, "w", encoding="utf-8").write(s)
    return len(s.encode("utf-8")), tol, len(feats)


def main():
    geo = Geo()
    region = Region(geo)
    src_path = os.path.join(PROC, "buildings.csv")
    rows = list(csv.DictReader(open(src_path, encoding="utf-8")))
    used = json.load(open(os.path.join(PROC, "sources_used.json"), encoding="utf-8"))
    cache_path = os.path.join(CACHE, "geocode.json")
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}

    sido_rows = collections.defaultdict(list)
    nocoord = collections.defaultdict(list)
    sgg_stat = {}
    sido_stat = {}
    dropped_geo = 0
    geo_counts = collections.Counter()

    def stat(d, key):
        return d.setdefault(key, dict(c2=0, c1=0, c0=0, points=0, nocoord=0))

    # fresh output dirs for the generated files
    for sub in ("sido", "nocoord", "summary"):
        shutil.rmtree(os.path.join(WEBDATA, sub), ignore_errors=True)
    os.makedirs(WEBDATA, exist_ok=True)

    for r in rows:
        c = int(r["c"])
        sido = r["sido"]
        sgg = r["sgg"] or (sido + "000")
        lat = float(r["lat"]) if r["lat"] else None
        lng = float(r["lng"]) if r["lng"] else None
        how = "src" if lat is not None else None
        if lat is None and cache:
            cands = []
            if r["addr"]:
                cands.append((cache.get(geocode_query(region, geo, r["addr"], sido, r["sgg"])), ("vworld", "kakao")))
            cands.append((cache.get(place_key(r["sgg"], r["name"])), PLACE_OK))  # building-name search (rows w/o usable address)
            for hit, ok in cands:
                if hit and hit.get("lat") is not None and hit["p"] in ok and (PLACE_FACILITY_HITS or hit.get("m") != "head"):
                    if geo.in_bbox(hit["lat"], hit["lng"], sgg=r["sgg"] or None, sido=sido, pad=0.02):
                        lat, lng, how = hit["lat"], hit["lng"], hit["p"]
                        break
                    dropped_geo += 1
        key = "c%d" % c
        for d, k in ((sgg_stat, sgg), (sido_stat, sido)):
            s = stat(d, k)
            s[key] += 1
            s["points" if lat is not None else "nocoord"] += 1
        year = int(r["year"]) if r["year"] else None
        if lat is not None:
            geo_counts[how] += 1
            area = float(r["area"]) if r["area"] else None
            sido_rows[sido].append([round(lat, 5), round(lng, 5), c, r["name"], r["addr"], r["use"] or None, year, area,
                                    r["src"], sgg, how])
        else:
            nocoord[sido].append([c, r["name"], r["addr"], r["use"] or None, year, r["src"], sgg])

    # ---- per-sido files (all 17, even when empty)
    for code in geo.sido_name:
        write_json(os.path.join(WEBDATA, "sido", code + ".json"), dict(fields=SIDO_FIELDS, rows=sido_rows.get(code, [])))
        write_json(os.path.join(WEBDATA, "nocoord", code + ".json"), dict(fields=NOCOORD_FIELDS, rows=nocoord.get(code, [])))

    # ---- summary
    summary = {}
    for code, name in geo.sgg_name.items():
        summary[code] = dict(name=name, sido=code[:2], **stat(sgg_stat, code))
    for sd in geo.sido_name:
        k = sd + "000"
        if k in sgg_stat:
            summary[k] = dict(name="(시·군·구 미확인)", sido=sd, **sgg_stat[k])
    stray = set(sgg_stat) - set(summary)
    assert not stray, "unknown sgg codes: %s" % sorted(stray)
    write_json(os.path.join(WEBDATA, "summary", "sigungu.json"), summary)

    # ---- boundaries
    b1 = geojson(TOPO_SIDO, "sido.geojson")
    b2 = geojson(TOPO_SGG, "sigungu.geojson")

    # ---- manifest
    tot = dict(c2=0, c1=0, c0=0, points=0, nocoord=0)
    sido_list = []
    for f in geo.sido:
        code = f["properties"]["code"]
        s = stat(sido_stat, code)
        for k in tot:
            tot[k] += s[k]
        sido_list.append(dict(code=code, name=CURRENT_SIDO_NAME.get(code, f["properties"]["name"]), bbox=[round(v, 4) for v in feat_bbox(f)], **s))
    kst = datetime.timezone(datetime.timedelta(hours=9))
    manifest = dict(built_at=datetime.datetime.now(kst).replace(microsecond=0).isoformat(), totals=tot, sources=used,
                    sido=sido_list)
    write_json(os.path.join(WEBDATA, "manifest.json"), manifest, compact=False)

    # ---- validation: everything adds up
    n = len(rows)
    ssum = {k: sum(v[k] for v in summary.values()) for k in tot}
    n_sido = sum(len(v) for v in sido_rows.values())
    n_nc = sum(len(v) for v in nocoord.values())
    assert sum(tot[k] for k in ("c2", "c1", "c0")) == n, "manifest class totals != rows"
    assert tot["points"] + tot["nocoord"] == n
    assert ssum == tot, "sum(sigungu) %s != manifest %s" % (ssum, tot)
    assert n_sido == tot["points"] and n_nc == tot["nocoord"], "files rows != totals"
    print("rows %d | c2 %d c1 %d c0 %d | points %d nocoord %d (geo: %s; geocode dropped by bbox: %d)" % (
        n, tot["c2"], tot["c1"], tot["c0"], tot["points"], tot["nocoord"], dict(geo_counts), dropped_geo))
    print("boundaries: sido.geojson %d B (%d feats, tol %g), sigungu.geojson %d B (%d feats, tol %g)" % (b1[0], b1[2], b1[1], b2[0], b2[2], b2[1]))
    print("validation OK: manifest totals == sum(sigungu) == sido+nocoord files == csv rows")


if __name__ == "__main__":
    main()
