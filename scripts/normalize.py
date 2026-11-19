#!/usr/bin/env python3
"""Normalize every building-list source in data/raw/ into data/processed/buildings.csv.

Columns: name, addr, sido, sgg, lat, lng, c, use, year, area, src, updated
  c: 2 = flag says asbestos building, 1 = no flag (survey-target list), 0 = flag says not.
Also writes data/processed/normalize-report.md and data/processed/sources_used.json.

Header mapping is heuristic (ROLE_PATTERNS, matched on the header with whitespace removed) with an explicit
per-source OVERRIDES dict for odd files.  Asbestos class precedence per row:
  explicit yes/no flag column  >  위해성 grade present (=> asbestos, grade only exists for asbestos buildings)
  >  asbestos-material area column (>0 => 2, 0 => 0)  >  1 (not stated).
"""
import csv, json, os, re, sys, collections, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (RAW, PROC, ROOT, SOURCES_JSON, Geo, Region, read_table, nfc, clean_addr, addr_core, name_key,
                    tm_to_wgs84, guess_epsg, TM, SIDO_ALIAS, write_json)

FIELDS = ["name", "addr", "sido", "sgg", "lat", "lng", "c", "use", "year", "area", "src", "updated"]

ROLE_PATTERNS = {
    "name": [r"^건축물명\(상호\)$", r"^건축물명$", r"^건물명$", r"^시설명$", r"^대상시설명$", r"^기관명\(건물명\)$",
             r"^기관\(상호\)명$", r"^기관명$", r"^상호$", r"^명칭$", r"^BDST_NM$"],
    "sub": [r"^동명$", r"^건물동명$", r"^건물해당동명$", r"^건축물_동명$", r"^세부위치$", r"^EMD_NM$"],
    "addr_road": [r"도로명", r"ROAD"],
    "addr_gen": [r"^주소$", r"^주소지$", r"^건물주소$", r"^건축물주소$", r"^소재지$", r"^소재지주소$", r"^세부소재지$",
                 r"^건축물소재지$"],
    "addr_lot": [r"지번", r"LOTNO", r"^대지위치$"],
    "use": [r"소분류", r"^분류$", r"^건축물분류$", r"^용도분류$", r"^건축물구분$", r"^시설구분$", r"^기관구분$", r"^구분$",
            r"^건축물용도$", r"^건물용도$", r"^주용도$", r"^용도$", r"^BDST_TYPE$", r"대분류"],
    "area": [r"연면적", r"전체면적", r"^GFA$"],
    "year": [r"착공신고일", r"건축일자", r"준공", r"사용승인"],
    "flag": [r"석면건축(물)?여부", r"석면함유여부", r"^SBST_INSP_YN$", r"^조사결과$"],
    "risk": [r"위해성(평가|등급)"],
    "asb_area": [r"석면.*면적", r"^SBST_AREA$"],
    "date": [r"데이터기준일", r"기준일자", r"^기준일$", r"^DATA_CRTR_YMD$"],
    "lat": [r"^위도$", r"^LAT$"],
    "lng": [r"^경도$", r"^LOT$", r"^LON$", r"^LNG$"],
    "pub": [r"^INSTT_NM$"],
}

# Seoul-owned facilities outside Seoul (서울대공원 in 과천, 물재생센터 in 고양 ...) often omit the sido: try these prefixes
FALLBACK_SIDO = ["서울특별시", "경기도", "강원특별자치도", "충청남도", "충청북도", "인천광역시"]

# per-source explicit overrides: role -> list of header names (joined with a space) | None disables the role
OVERRIDES = {
    # no building-name column: 관리주체 (owner) + 건축물용도 (actually the building/room name)
    "3047099": dict(name=["관리주체", "건축물용도"], use=["분류"]),
    # no street address, only 시도/시군구/법정동 (+ name)
    "15086967": dict(addr_join=["시도", "시군구", "법정동"]),
    # 정읍: 석면조사여부/검사여부 = survey done, not a result -> no flag
    "15054349": dict(flag=None), "15054350": dict(flag=None),
    # 영양군 비고 = 조사완료 status; 수성구 비고 = facility type
    "3068391": dict(flag=None, use=None), "3077221": dict(flag=None, use=["비고"]),
    # 창원 위해성 평가: blank for some rows (=> class 1), grade otherwise
    "3068544": dict(use=["건축물분류"]),
    # 영도(11 rows) lists 석면면적 only: area > 0 => asbestos
    "3038626": dict(),
    # Seoul plaza OA-15383: 이름 = building, 기관명 = owning agency (name = "기관 이름"); address split over 주소1/주소2;
    # no flag column -> 석면함유면적 (>0 asbestos, 0 not).  (fetch_all.py drops 소유자 and contact columns)
    "15048930": dict(name=["기관명", "이름"], addr_join=["주소1", "주소2"], use=["건물용도"], fallback_sido=FALLBACK_SIDO),
    # Seoul plaza OA-1119: 주소 has no space before the road name ("용산구한남대로 136"; clean_addr repairs it); 건물명 may be empty -> 기관명
    "15072370": dict(name=["기관명", "건물명"], fallback_sido=FALLBACK_SIDO),
    # 강동구 board: 석면여부 = 석면검출 / 미검출; no address column (publisher region only)
    "3074878": dict(flag=["석면여부"], use=None),
}

# in sources.json as building_list, but not a building list: reason is written to the report
NOT_BUILDINGS = {
    "15048932": "Seoul OA-15384 석면시료: 12,857 per-sample lab/risk records (기관명 + 건물명 + 채취장소, no address, "
                "no building id). Downloaded to data/raw but not used: it would only add a flag to buildings that "
                "15048930/15072370 already list (1,302 of its 1,612 building names match those two).",
}

COUNTRY_BBOX = (32.5, 38.9, 124.0, 132.0)  # lat min/max, lng min/max (incl. Dokdo)


def hnorm(h):
    return re.sub(r"\s+", "", nfc(h or ""))


def pick(headers, role, ovr):
    """-> list of column indices for the role (first matching pattern wins; may be several for addr roles)."""
    if role in ovr:
        names = ovr[role]
        if names is None:
            return []
        return [i for n in names for i, h in enumerate(headers) if hnorm(h) == hnorm(n)]
    hs = [hnorm(h) for h in headers]
    out = []
    for pat in ROLE_PATTERNS[role]:
        for i, h in enumerate(hs):
            if re.search(pat, h) and i not in out:
                out.append(i)
    return out


def cell(row, i):
    return nfc(row[i]).strip() if i < len(row) else ""


def first(row, idxs):
    for i in idxs:
        v = cell(row, i)
        if v:
            return v
    return ""


def parse_float(v):
    v = (v or "").replace(",", "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def parse_date(v):
    v = (v or "").strip()
    m = re.match(r"^(\d{2,4})[-./](\d{1,2})[-./](\d{1,2})", v)
    if m:
        y = int(m.group(1))
        y = y + 2000 if y < 100 else y
        return "%04d-%02d-%02d" % (y, int(m.group(2)), int(m.group(3)))
    if re.fullmatch(r"\d{8}", v):
        return "%s-%s-%s" % (v[:4], v[4:6], v[6:])
    if re.fullmatch(r"\d{5}(\.\d+)?", v):  # Excel serial
        d = datetime.date(1899, 12, 30) + datetime.timedelta(days=int(float(v)))
        return d.isoformat()
    return ""


def parse_year(v):
    m = re.search(r"(19\d{2}|20[0-2]\d)", v or "")
    return int(m.group(1)) if m else None


def flag_val(v):
    v = (v or "").strip()
    if not v:
        return None
    if re.search(r"미해당|해당\s*없음|무석면|^[Nn]$|아님|없음|미검출|불검출", v):
        return 0
    if re.search(r"해당|석면건축물|석면함유|^[Yy]$|있음|검출", v):
        return 2
    return None


def class_of(flag, risk, asb_area):
    f = flag_val(flag)
    if f is not None:
        return f, "flag"
    if risk and risk.strip():
        return 2, "risk"
    a = parse_float(asb_area)
    if a is not None:
        return (2 if a > 0 else 0), "area"
    return 1, "none"


def coords(lat_s, lng_s, rep, src):
    """-> (lat, lng) WGS84 or (None, None). Handles swapped values and projected coords."""
    a, b = parse_float(lat_s), parse_float(lng_s)
    if a is None or b is None or (a == 0 and b == 0):
        return None, None
    lat, lng = a, b
    if abs(a) > 1000 or abs(b) > 1000:
        ep = guess_epsg(a, b)
        if ep:
            lat, lng = tm_to_wgs84(a, b, ep)
        elif guess_epsg(b, a):
            lat, lng = tm_to_wgs84(b, a, 5179)
        else:
            rep["coord_unconv"][src] += 1
            return None, None
        rep["coord_projected"][src] += 1
    elif not (COUNTRY_BBOX[0] <= lat <= COUNTRY_BBOX[1]) and COUNTRY_BBOX[0] <= lng <= COUNTRY_BBOX[1] \
            and COUNTRY_BBOX[2] <= lat <= COUNTRY_BBOX[3]:
        lat, lng = lng, lat
        rep["coord_swapped"][src] += 1
    if not (COUNTRY_BBOX[0] <= lat <= COUNTRY_BBOX[1] and COUNTRY_BBOX[2] <= lng <= COUNTRY_BBOX[3]):
        rep["coord_bad"][src] += 1
        return None, None
    return round(lat, 6), round(lng, 6)


def display_name(name, sub):
    nk, sk = name_key(name), name_key(sub)
    if not sub or not sk or sk == nk or sk in nk:
        return name
    if nk and nk in sk:
        return sub
    return "%s %s" % (name, sub)


def process_source(src, path, geo, region, rep):
    sid = src["id"]
    hdr, rows = read_table(path)
    ovr = OVERRIDES.get(sid, {})
    cols = {r: pick(hdr, r, ovr) for r in ROLE_PATTERNS}
    for r in ("addr_join",):
        if r in ovr:
            cols[r] = [i for n in ovr[r] for i, h in enumerate(hdr) if hnorm(h) == hnorm(n)]
    name_cols = cols["name"]
    prim = name_cols[:1] if "name" not in ovr else name_cols
    extra_names = [] if "name" in ovr else name_cols[1:]
    pub_sido, pub_sgg = src.get("sido"), src.get("sigungu")
    pub_sido = None if pub_sido == "전국" else pub_sido
    cnt = rep["src"][sid] = collections.Counter(read=len(rows))
    rep["cols"][sid] = {k: [hdr[i] for i in v] for k, v in cols.items() if v}
    out = []
    for row in rows:
        name = " ".join(dict.fromkeys(v for v in (cell(row, i) for i in prim) if v))
        sub = first(row, cols["sub"] + extra_names)
        if "addr_join" in cols:
            addr = " ".join(v for v in (cell(row, i) for i in cols["addr_join"]) if v)
        else:
            addr = first(row, cols["addr_road"] + cols["addr_gen"] + cols["addr_lot"])
        addr = clean_addr(addr)
        if re.fullmatch(r"[-.\s0]*", addr):
            addr = ""
        if not name and not addr:
            cnt["reject: no name and no address"] += 1
            rep["rejects"].append((sid, "no name and no address", "|".join(row)[:80]))
            continue
        rp, rg = pub_sido, pub_sgg
        if cols["pub"]:
            pv = first(row, cols["pub"]).split()
            rp, rg = (pv[0], " ".join(pv[1:]) or None) if pv else (rp, rg)
        res = region.resolve(addr, pub_sido=rp, pub_sgg=rg)
        if ovr.get("fallback_sido") and addr and not res.get("sgg"):
            a0 = re.sub(r"^서울특별\s", "서울특별시 ", addr)
            a0 = re.sub(r"^서울(?:특별시?)?\s+(?=\S+[시군도]\s)", "", a0)  # "서울특별시 경기도 과천시" (agency + town)
            a0 = re.sub(r"(\S{2,3}시)\1", r"\1", a0)  # "과천시과천시"
            a0 = re.sub(r"(?<=[가-힣]시)(?=[가-힣]+(?:동|읍|면|리)(?:\s|$))", " ", a0)  # "과천시주암동"
            for pre in [""] + ovr["fallback_sido"]:
                cand = (pre + " " + a0).strip()
                r2 = region.resolve(cand, pub_sido=None, pub_sgg=None)
                if r2.get("sgg"):
                    addr, res = cand, r2
                    cnt["sido inferred from address (fallback list)"] += 1
                    break
        # addresses without a region prefix (e.g. "해양로 293") get the publisher's region
        if addr and not res.get("had_sido") and res["sido"] and rp:
            addr = " ".join(x for x in (rp, rg, addr) if x)
            cnt["addr prefixed with publisher region"] += 1
        c, how = class_of(first(row, cols["flag"]), first(row, cols["risk"]), first(row, cols["asb_area"]))
        lat, lng = coords(first(row, cols["lat"]), first(row, cols["lng"]), rep, sid)
        sido, sgg = res["sido"], res["sgg"]
        if lat is not None:
            here = geo.locate(lat, lng)
            if sgg is None and here:
                sgg = here; sido = here[:2]; cnt["sgg from coordinates"] += 1
            elif sgg and not geo.in_bbox(lat, lng, sgg=sgg):
                cnt["coords outside sgg bbox (dropped)"] += 1
                rep["coord_out"][sid] += 1
                lat = lng = None
        use = first(row, cols["use"]) if "use" not in ovr else " ".join(cell(row, i) for i in cols["use"] if cell(row, i))
        use = use.split(">")[-1].strip()
        if re.fullmatch(r"\d+", use):
            use = ""
        area = parse_float(first(row, cols["area"]))
        year = parse_year(first(row, cols["year"])) if cols["year"] else None
        date = parse_date(first(row, cols["date"])) or (src.get("updated") or "")
        if sido is None:
            cnt["reject: sido unresolved"] += 1
            rep["rejects"].append((sid, "sido unresolved", addr[:60]))
            continue
        if sgg is None:
            rep["unmatched"][(sid, res["note"], " ".join((addr.split() + [""])[:3]))] += 1
            cnt["sgg unresolved"] += 1
        cnt["class %d via %s" % (c, how)] += 1
        cnt["kept"] += 1
        if lat is not None:
            cnt["with coords"] += 1
        out.append(dict(name=name or "(명칭 없음)", sub=sub, addr=addr, sido=sido, sgg=sgg or "", lat=lat, lng=lng, c=c,
                        use=use, year=year, area=area if area and area > 0 else None, src=sid, updated=date,
                        akey="%s|%s" % (sgg or sido, addr_core(res["rest"])), nkey=name_key(name), skey=re.sub(r"[^0-9a-z가-힣]", "", nfc(sub).lower())))
    return out


def merge(rows, rep, prio):
    """Dedupe: same address core + same name (cross-source sub-name tolerant, then name containment)."""
    # rows of ONE source are separate records (e.g. several 동 under one name/address): only fully identical rows collapse
    seen, uniq = set(), []
    for r in rows:
        k = (r["src"], r["akey"], r["nkey"], r["skey"], r["area"], r["c"], r["lat"])
        if k in seen:
            continue
        seen.add(k); uniq.append(r)
    rep["dedupe_identical"] = len(rows) - len(uniq)
    rows_in = len(rows)
    rows = uniq
    clusters, index = [], collections.defaultdict(list)
    for r in rows:
        placed = False
        for cl in index[(r["akey"], r["nkey"])]:
            if r["src"] not in cl["srcs"] and (r["skey"] == cl["skey"] or not r["skey"] or not cl["skey"]):
                cl["rows"].append(r); cl["srcs"].add(r["src"]); cl["skey"] = cl["skey"] or r["skey"]
                placed = True
                break
        if not placed:
            cl = dict(rows=[r], srcs={r["src"]}, skey=r["skey"], akey=r["akey"], nkey=r["nkey"])
            clusters.append(cl); index[(r["akey"], r["nkey"])].append(cl)
    rep["dedupe_exact"] = len(rows) - len(clusters)  # cross-source exact (address+name)
    # pass 2: same address, different sources, one name contains the other
    byaddr = collections.defaultdict(list)
    for cl in clusters:
        byaddr[cl["akey"]].append(cl)
    dead = set()
    merged2 = 0
    for ak, group in byaddr.items():
        if len(group) < 2 or not ak.split("|", 1)[1]:
            continue
        for i, a in enumerate(group):
            if id(a) in dead:
                continue
            for b in group[i + 1:]:
                if id(b) in dead or a["srcs"] & b["srcs"]:
                    continue
                x, y = a["nkey"], b["nkey"]
                if min(len(x), len(y)) >= 3 and (x in y or y in x):
                    a["rows"] += b["rows"]; a["srcs"] |= b["srcs"]; dead.add(id(b)); merged2 += 1
    # pass 3: address-less rows (강동 board: name only) join the one same-name cluster of that sgg
    bysn = collections.defaultdict(list)
    for cl in clusters:
        if id(cl) not in dead and cl["akey"].split("|", 1)[1]:
            bysn[(cl["akey"].split("|", 1)[0], cl["nkey"])].append(cl)
    for cl in clusters:
        if id(cl) in dead or cl["akey"].split("|", 1)[1] or len(cl["nkey"]) < 3:
            continue
        cand = [c for c in bysn.get((cl["akey"].split("|", 1)[0], cl["nkey"]), []) if not (c["srcs"] & cl["srcs"])]
        if len(cand) == 1:
            cand[0]["rows"] += cl["rows"]; cand[0]["srcs"] |= cl["srcs"]; dead.add(id(cl)); merged2 += 1
    clusters = [c for c in clusters if id(c) not in dead]
    rep["dedupe_fuzzy"] = merged2
    final = []
    for cl in clusters:
        rs = cl["rows"]
        best = max(rs, key=lambda r: (r["c"] != 1, r["lat"] is not None, r["updated"]))
        cs = {r["c"] for r in rs} - {1}
        if len(cs) > 1:
            rep["conflicts"] += 1
        o = dict(best)
        o["c"] = best["c"]
        for k in ("use", "year", "area"):
            if o[k] in (None, ""):
                o[k] = next((r[k] for r in rs if r[k] not in (None, "")), o[k])
        if o["lat"] is None:
            g = next((r for r in rs if r["lat"] is not None), None)
            if g:
                o["lat"], o["lng"] = g["lat"], g["lng"]
        o["updated"] = max(r["updated"] for r in rs)
        srcs = sorted(cl["srcs"], key=lambda s: prio.get(s, 99))
        o["src"] = ",".join([best["src"]] + [s for s in srcs if s != best["src"]])
        o["name"] = display_name(best["name"], best["sub"])
        final.append(o)
    return final


def main():
    geo = Geo()
    region = Region(geo)
    sources = json.load(open(SOURCES_JSON))
    st_path = os.path.join(RAW, "_fetch_state.json")
    state = json.load(open(st_path)) if os.path.exists(st_path) else {}
    rep = dict(src={}, cols={}, rejects=[], unmatched=collections.Counter(), coord_swapped=collections.Counter(),
               coord_projected=collections.Counter(), coord_unconv=collections.Counter(), coord_bad=collections.Counter(),
               coord_out=collections.Counter(), conflicts=0, skipped=[])
    allrows, used = [], []
    prio = {}
    for s in sources:
        if s["category"] != "building_list":
            continue
        sid = s["id"]
        fn = (state.get(sid) or {}).get("file") or (os.path.basename(s["local_file"]) if s.get("local_file") else None)
        path = os.path.join(RAW, fn) if fn else None
        if sid in NOT_BUILDINGS:
            rep["skipped"].append((sid, s["publisher"], NOT_BUILDINGS[sid]))
            continue
        if (state.get(sid) or {}).get("via") and state[sid].get("updated"):
            s = dict(s, updated=state[sid]["updated"])  # scraped sources: date of the live page
        if not path or not os.path.exists(path):
            rep["skipped"].append((sid, s["publisher"], "no local file: " + (s.get("notes") or "")[:90]))
            continue
        rows = process_source(s, path, geo, region, rep)
        c = rep["src"][sid]
        # explicit-flag sources first in dedupe priority
        prio[sid] = 0 if c["class 2 via flag"] + c["class 0 via flag"] > 0 else 1
        allrows += rows
        used.append(dict(id=sid, publisher=s["publisher"] or "전국석면조사건축물표준데이터 (19 지자체)", title=s["title"],
                         updated=(state.get(sid) or {}).get("updated") or s["updated"], rows=len(rows),
                         url=s["url_page"]))
    prio["15092323"] = 2  # national file is the backbone: lowest priority on ties of source order
    final = merge(allrows, rep, prio)
    final.sort(key=lambda r: (r["sido"], r["sgg"] or "z", r["name"]))
    per_src = collections.Counter()
    for r in final:
        for sid in r["src"].split(","):
            per_src[sid] += 1
    for u in used:
        u["rows_final"] = per_src[u["id"]]
    os.makedirs(PROC, exist_ok=True)
    with open(os.path.join(PROC, "buildings.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for r in final:
            w.writerow([r["name"], r["addr"], r["sido"], r["sgg"], "" if r["lat"] is None else "%.6f" % r["lat"],
                        "" if r["lng"] is None else "%.6f" % r["lng"], r["c"], r["use"], r["year"] or "",
                        "" if r["area"] is None else r["area"], r["src"], r["updated"]])
    write_json(os.path.join(PROC, "sources_used.json"), used, compact=False)
    report(rep, final, allrows, used, geo)
    print("buildings.csv: %d rows (from %d; identical %d, cross-source exact %d, fuzzy %d)" % (len(final), len(allrows), rep["dedupe_identical"], rep["dedupe_exact"], rep["dedupe_fuzzy"]))


def report(rep, final, allrows, used, geo):
    L = ["# normalize report", "", "Generated %s by scripts/normalize.py" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), ""]
    cc = collections.Counter(r["c"] for r in final)
    L += ["## Totals", "", "- rows read (kept, before dedupe): %d" % len(allrows),
          "- duplicates removed: %d identical rows inside one source + %d cross-source exact (address+name) + %d cross-source fuzzy (same address, name containment)" % (rep["dedupe_identical"], rep["dedupe_exact"], rep["dedupe_fuzzy"]),
          "- **final rows: %d**  (c=2: %d, c=1: %d, c=0: %d)" % (len(final), cc[2], cc[1], cc[0]),
          "- with source coordinates: %d" % sum(1 for r in final if r["lat"] is not None),
          "- sgg resolved: %d, sido-only: %d" % (sum(1 for r in final if r["sgg"]), sum(1 for r in final if not r["sgg"])),
          "- class conflicts across sources (a=0 vs 2, newest explicit flag kept): %d" % rep["conflicts"], ""]
    L += ["## Per source", "", "| id | publisher | read | kept | rows after dedupe | c2/c1/c0 | coords | sgg unresolved | notes |", "|---|---|---|---|---|---|---|---|---|"]
    for u in used:
        c = rep["src"][u["id"]]
        cls = [sum(v for k, v in c.items() if k.startswith("class %d" % n)) for n in (2, 1, 0)]
        notes = "; ".join("%s %d" % (k, v) for k, v in c.items() if k.startswith("reject") or k.startswith("coords outside") or k.startswith("addr prefixed") or k.startswith("sgg from"))
        L.append("| %s | %s | %d | %d | %d | %d/%d/%d | %d | %d | %s |" % (
            u["id"], u["publisher"], c["read"], c["kept"], u["rows_final"], cls[0], cls[1], cls[2], c["with coords"], c["sgg unresolved"], notes))
    L += ["", "## Skipped sources (no local file)", ""]
    L += ["- %s %s: %s" % x for x in rep["skipped"]] or ["- none"]
    L += ["", "## Coordinates", "",
          "- swapped lat/lng fixed: %s" % (dict(rep["coord_swapped"]) or "none"),
          "- projected converted (TM->WGS84): %s" % (dict(rep["coord_projected"]) or "none (no source uses projected coords)"),
          "- unconvertible projected: %s; outside Korea: %s; outside their sgg bbox: %s" % (
              dict(rep["coord_unconv"]) or "none", dict(rep["coord_bad"]) or "none", dict(rep["coord_out"]) or "none")]
    L += ["", "## Unresolved sgg (source, reason, first 3 address tokens: count)", ""]
    um = rep["unmatched"].most_common(60)
    L += ["- %s | %s | %s: %d" % (k[0], k[1], k[2], n) for k, n in um] or ["- none"]
    if len(rep["unmatched"]) > 60:
        L.append("- ... %d more distinct" % (len(rep["unmatched"]) - 60))
    L += ["", "## Rejected rows (%d)" % len(rep["rejects"]), ""]
    L += ["- %s | %s | %s" % x for x in rep["rejects"][:40]] or ["- none"]
    L += ["", "## Column mapping used", ""]
    for sid, m in rep["cols"].items():
        L.append("- %s: %s" % (sid, "; ".join("%s=%s" % (k, "/".join(v)) for k, v in m.items())))
    open(os.path.join(PROC, "normalize-report.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
