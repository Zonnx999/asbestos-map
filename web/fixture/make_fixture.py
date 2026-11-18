#!/usr/bin/env python3
"""Builds the tiny test fixture in web/fixture/ (data contract shape, docs/data-contract.md).
Run: python3 web/fixture/make_fixture.py  (from the project root)
Seoul (11) has points + nocoord; Daegu (22) has nocoord only (sido/22.json is deliberately
missing, to exercise the 404 → empty path)."""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "web", "fixture")
B = os.path.join(ROOT, "data", "boundaries")


def topo_to_geo(topo, obj, keep=lambda p: True, nd=3):
    tr = topo.get("transform")
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for p in arc:
            if tr:
                x += p[0]; y += p[1]
                pts.append([round(x * tr["scale"][0] + tr["translate"][0], nd),
                            round(y * tr["scale"][1] + tr["translate"][1], nd)])
            else:
                pts.append([round(p[0], nd), round(p[1], nd)])
        arcs.append(pts)

    def ring(ids):
        out = []
        for i in ids:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(a if not out else a[1:])
        dedup = []
        for k, pt in enumerate(out):  # drop points closer than ~400 m to the last kept one
            if k in (0, len(out) - 1) or abs(pt[0] - dedup[-1][0]) + abs(pt[1] - dedup[-1][1]) > 0.004:
                dedup.append(pt)
        return dedup if len(dedup) >= 4 else out

    feats = []
    for g in topo["objects"][obj]["geometries"]:
        p = g["properties"]
        if not keep(p):
            continue
        if g["type"] == "Polygon":
            coords = [ring(r) for r in g["arcs"]]
        else:
            coords = [[ring(r) for r in poly] for poly in g["arcs"]]
        feats.append({"type": "Feature", "properties": {"code": p["code"], "name": p["name"]},
                      "geometry": {"type": g["type"], "coordinates": coords}})
    return {"type": "FeatureCollection", "features": feats}


def dump(rel, obj):
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


prov = json.load(open(os.path.join(B, "skorea-provinces-2018-topo-simple.json")))
muni = json.load(open(os.path.join(B, "skorea-municipalities-2018-topo-simple.json")))
dump("boundaries/sido.geojson", topo_to_geo(prov, "skorea_provinces_2018_geo"))
dump("boundaries/sigungu.geojson", topo_to_geo(muni, "skorea_municipalities_2018_geo",
                                               keep=lambda p: p["code"][:2] in ("11", "22")))

PF = ["lat", "lng", "c", "name", "addr", "use", "year", "area", "src", "sgg", "geo"]
NF = ["c", "name", "addr", "use", "year", "src", "sgg"]
pts11 = [
    [37.57296, 126.97935, 2, "광화문초등학교(가상)", "서울 종로구 세종대로 1", "교육연구시설", 1978, 4321.5, "15000001", "11010", "src"],
    [37.57612, 126.98501, 1, "삼청동 주민센터(가상)", "서울 종로구 삼청로 2", "업무시설", 1989, None, "15000001", "11010", "vworld"],
    [37.57010, 126.99210, 0, "종로 상가(가상)", "서울 종로구 종로 3", "판매시설", 2005, 880.0, "15000001", "11010", "src"],
    [37.49790, 127.02760, 2, "강남 오피스(가상)", "서울 강남구 테헤란로 4", "업무시설", 1984, 15230.0, "15000001,15000002", "11230", "kakao"],
    [37.50120, 127.03960, 2, "역삼 병원(가상)", "서울 강남구 역삼로 5", "의료시설", 1981, 9000.0, "15000002", "11230", "src"],
    [37.49950, 127.03110, 1, None, "서울 강남구 강남대로 6", None, None, None, "15000002", "11230", "nominatim"],
    [37.55630, 126.92370, 2, "홍대 상가(가상)", "서울 마포구 와우산로 7", "근린생활시설", 1979, 2100.0, "15000001", "11140", "src"],
    [37.54900, 126.91400, 0, "합정 창고(가상)", "서울 마포구 양화로 8", "창고시설", 1999, 640.0, "15000001", "11140", "src"],
]
nc11 = [
    [2, "종로 경로당(가상)", "서울 종로구 율곡로 9", "노유자시설", 1975, "15000001", "11010"],
    [1, "마포 어린이집(가상)", "서울 마포구 독막로 10", "노유자시설", 1990, "15000001", "11140"],
    [2, "강남 학원(가상)", "서울 강남구 선릉로 11", "교육연구시설", 1983, "15000002", "11230"],
]
nc22 = [
    [2, "달서 초등학교(가상)", "대구 달서구 월배로 12", "교육연구시설", 1980, "3047280", "22070"],
    [2, "달서 도서관(가상)", "대구 달서구 달구벌대로 13", "문화및집회시설", 1987, "3047280", "22070"],
    [0, "달서 체육관(가상)", "대구 달서구 상인로 14", "운동시설", 2003, "3047280", "22070"],
    [1, "남구 주민센터(가상)", "대구 남구 대명로 15", "업무시설", None, "3046092", "22040"],
    [2, "남구 시장(가상)", "대구 남구 중앙대로 16", "판매시설", 1976, "3046092", "22040"],
    [1, "주소 불명 건물(가상)", "대구광역시", None, None, "3046092", None],
]
dump("sido/11.json", {"fields": PF, "rows": pts11})
dump("nocoord/11.json", {"fields": NF, "rows": nc11})
dump("nocoord/22.json", {"fields": NF, "rows": nc22})

names = {"11010": ("종로구", "11"), "11140": ("마포구", "11"), "11230": ("강남구", "11"),
         "22040": ("남구", "22"), "22070": ("달서구", "22"), "22000": ("시·군·구 미상", "22")}
summ = {k: {"name": n, "sido": s, "c2": 0, "c1": 0, "c0": 0, "points": 0, "nocoord": 0} for k, (n, s) in names.items()}
for r in pts11:
    e = summ[r[9]]; e["c%d" % r[2]] += 1; e["points"] += 1
for r in nc11 + nc22:
    e = summ[r[6] or (r[5] and "22000") or "22000"]; e["c%d" % r[0]] += 1; e["nocoord"] += 1
dump("summary/sigungu.json", summ)

sido = []
for code, name, bbox in [("11", "서울특별시", [126.764, 37.413, 127.184, 37.715]),
                         ("22", "대구광역시", [128.349, 35.607, 128.766, 36.017])]:
    e = {"code": code, "name": name, "bbox": bbox, "c2": 0, "c1": 0, "c0": 0, "points": 0, "nocoord": 0}
    for v in summ.values():
        if v["sido"] == code:
            for k in ("c2", "c1", "c0", "points", "nocoord"):
                e[k] += v[k]
    sido.append(e)
tot = {k: sum(s[k] for s in sido) for k in ("c2", "c1", "c0", "points", "nocoord")}
dump("manifest.json", {
    "built_at": "2026-11-18T12:00:00+09:00", "totals": tot,
    "sources": [
        {"id": "15000001", "publisher": "서울특별시(가상)", "title": "서울특별시_석면건축물 현황(가상)", "updated": "2026-10-01", "rows": 7, "url": "https://www.data.go.kr/data/15000001/fileData.do"},
        {"id": "15000002", "publisher": "서울특별시 강남구(가상)", "title": "강남구_석면조사대상 건축물(가상)", "updated": "2026-09-15", "rows": 4, "url": "https://www.data.go.kr/data/15000002/fileData.do"},
        {"id": "3047280", "publisher": "대구광역시 달서구", "title": "대구광역시 달서구_석면건축물 현황", "updated": "2025-12-31", "rows": 3, "url": "https://www.data.go.kr/data/3047280/fileData.do"},
        {"id": "3046092", "publisher": "대구광역시 남구", "title": "대구광역시 남구_석면건축물", "updated": "2025-06-30", "rows": 3, "url": "https://www.data.go.kr/data/3046092/fileData.do"},
    ],
    "sido": sido})
print("fixture written to", OUT, tot)
