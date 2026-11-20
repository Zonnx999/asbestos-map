#!/usr/bin/env python3
"""Geocode address-only rows: VWorld (env VWORLD_KEY) -> Kakao (env KAKAO_KEY) -> none.

 - reads data/processed/buildings.csv, takes rows without coordinates, builds one query per unique address
   (common.geocode_query: modern sido + canonical 2018 sgg name + road/lot core), skips cached ones;
 - cache: data/cache/geocode.json  {query: {"lat","lng","p"} | {"p":"miss"} | {"p":"reject","lat","lng","why"}}
   written every 200 queries and on exit, so it is resumable (Ctrl-C safe);
 - sanity check: a point must lie in its sgg bbox (sido bbox if sgg unknown) or it is stored as "reject";
 - NO key set -> prints a notice and exits 0.  Nominatim is deliberately not used (no bulk use per OSM policy).
build_web.py merges the cache into the output (and re-applies the bbox check).
Extra passes (VWorld only; cache stays keyed by the ORIGINAL query so build_web finds it):
 --variants   for queries cached as miss (or never queried): chain of 'modern-sgg' (2026 district names as in the source) ->
              'clean-road' (지하/trailing 0/번길 spacing/sub-number noise) -> 'parcel' (dong + lot from the raw text) ->
              'raw' (source address as written); hit stored as {"p":"vworld","v":<variant>,"r":<refined address>};
              exhausted chains are marked {"p":"miss","c":1} and skipped next time. --retry-misses also retries the original query.
 --places     VWorld place search by building name for rows still unplaced (no address / no road number / address misses);
              key "place:{sgg}:{name}" -> {"p":"vworld-place","lat","lng","t":title,"r":road address}; accepted only inside the
              sgg bbox and when the hit title matches the cleaned name.
Usage: geocode.py [--limit N] [--rate REQ_PER_SEC] [--retry-misses] [--variants] [--places] [--dry-run]
Env:   VWORLD_KEY, VWORLD_DOMAIN (optional, if the key is bound to a domain), KAKAO_KEY
"""
import argparse, concurrent.futures, csv, json, math, os, re, sys, threading, time, urllib.error, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (PROC, CACHE, Geo, Region, geocode_query, write_json, place_key, PLACE_OK, clean_addr, SIDO_MODERN,
                    SIDO_ALIAS, nfc, addr_core)

CACHE_FILE = os.path.join(CACHE, "geocode.json")
UA = "asbestos-map-geocoder/0.1"


class Disabled(Exception):
    pass


class Limiter:
    """Global request pacing: at most `rate` HTTP calls per second across all threads."""
    def __init__(self, rate):
        self.gap, self.lock, self.next = 1.0 / rate, threading.Lock(), 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        if t > now:
            time.sleep(t - now)


LIMITER = Limiter(5.0)
STOP = threading.Event()  # set when a provider is disabled (key/quota): workers drain and exit


def http_json(url, headers=None, tries=3):
    last = None
    for k in range(tries):
        try:
            LIMITER.wait()
            req = urllib.request.Request(url, headers=dict({"User-Agent": UA}, **(headers or {})))
            return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise Disabled("HTTP %d from %s" % (e.code, url.split("?")[0]))
            last = e
            time.sleep(2 * (k + 1) if e.code != 429 else 20)
        except Exception as e:  # network hiccup
            last = e
            time.sleep(2 * (k + 1))
    raise RuntimeError("request failed: %r" % last)


def _vw_check(r):
    if r.get("status") == "ERROR":
        msg = json.dumps(r.get("error", ""), ensure_ascii=False)
        if "KEY" in msg.upper() or "LIMIT" in msg.upper() or "OVER" in msg.upper() or "QUOTA" in msg.upper():
            raise Disabled("VWorld: " + msg[:120])


def vworld_addr(q, key, domain, typ):
    """one VWorld address call -> (lat, lng, refined text) or None."""
    p = dict(service="address", request="getcoord", version="2.0", crs="epsg:4326", address=q, refine="true",
             simple="false", format="json", type=typ, key=key)
    if domain:
        p["domain"] = domain
    r = http_json("https://api.vworld.kr/req/address?" + urllib.parse.urlencode(p))["response"]
    if r.get("status") == "OK":
        pt = r["result"]["point"]
        return float(pt["y"]), float(pt["x"]), (r.get("refined") or {}).get("text", "")
    _vw_check(r)
    return None


def vworld(q, key, domain, types=("road", "parcel")):
    for typ in types:
        h = vworld_addr(q, key, domain, typ)
        if h:
            return h[0], h[1]
    return None


def vworld_place(query, key, domain, bbox=None, size=5):
    """VWorld place search -> [(title, road address, lat, lng)] in relevance order."""
    p = dict(service="search", request="search", version="2.0", crs="EPSG:4326", size=size, page=1, query=query,
             type="place", format="json", errorformat="json", key=key)
    if domain:
        p["domain"] = domain
    if bbox:
        p["bbox"] = "%.5f,%.5f,%.5f,%.5f" % tuple(bbox)
    r = http_json("https://api.vworld.kr/req/search?" + urllib.parse.urlencode(p))["response"]
    if r.get("status") != "OK":
        _vw_check(r)
        return []
    out = []
    for it in (r.get("result") or {}).get("items") or []:
        try:
            out.append((it.get("title", ""), (it.get("address") or {}).get("road", ""), float(it["point"]["y"]), float(it["point"]["x"])))
        except (KeyError, ValueError, TypeError):
            pass
    return out


# ---- query variants for rows whose canonical query missed
_ROADRE = re.compile(r"^(.*?\S*(?:로|길)(?:\d+번?길)?)\s*(\d+(?:-\d+)?)(?=$|[\s,(]|\D)")
_PARCEL_PAREN = re.compile(r"([가-힣]+\d*(?:동|리|가))\s*(산?\d+(?:-\d+)?)(?![\d가])")
_LOTRE = re.compile(r"^(.*?[가-힣]+\d*(?:동|리|가))\s*(산?\d+(?:-\d+)?)(?![\d가])")


def road_variants(rest):
    """rest (region-less address) -> list of cleaned road strings, best guess first."""
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", rest)
    s = s.split(",")[0]
    s = re.sub(r"(로|길)\s*(?:지하|지상)\s*(\d)", r"\1 \2", s)      # 대로지하195 -> 대로 195
    s = re.sub(r"\s(?:지하|지상)\s*(?=\d)", " ", s)                  # '지하 55'
    s = re.sub(r"(로|길)\s+(\d+번?길)", r"\1\2", s)                 # 로 94번길 -> 로94번길
    s = re.sub(r"(\d+)번\s+길", r"\1번길", s)
    s = re.sub(r"\s+", " ", s).strip()
    m = _ROADRE.match(s)
    if not m:
        return []
    road, num = m.group(1), re.sub(r"-0$", "", m.group(2))
    tail = s[m.end():].strip()
    out = [road + " " + num]
    ex = re.match(r"^(\d+)(?=$|\s|[가-힣A-Za-z]|,)", tail)
    if ex and ex.group(1) != "0" and "-" not in num:
        out.append("%s %s-%s" % (road, num, ex.group(1)))
        if road.endswith("로") and len(num) <= 3:                                          # 대방천로29 18 -> 대방천로29길 18
            out.append("%s%s길 %s" % (road, num, ex.group(1)))
    seen = []
    for x in out:
        if x and x not in seen:
            seen.append(x)
    return seen


def region_parts(region, addr, sido, sgg):
    """-> (modern sido name, [sgg token strings: as written in the source, canonical 2018 name], rest) or None."""
    a = clean_addr(addr)
    r = region.resolve(a, pub_sido=SIDO_MODERN.get(sido), pub_sgg=None)
    code = sgg if sgg and sgg in region.canon else r["sgg"]
    sd = SIDO_MODERN.get((code or sido or "")[:2], "")
    t, rt = a.split(), r["rest"].split()
    forms = []
    if rt and t[len(t) - len(rt):] == rt:
        head = t[:len(t) - len(rt)]
        if head and head[0] in SIDO_ALIAS or (head and re.sub(r"(특별시|광역시)$", "", head[0]) in SIDO_ALIAS):
            head = head[1:]
        if head:
            forms.append(" ".join(head))
    canon = region.canon.get(code, "") if code else ""
    if canon and canon not in forms:
        forms.append(canon)
    return sd, forms, r["rest"]


def consistent(core, refined):
    """the address core we asked for must reappear in VWorld's refined address (it silently fuzzes: 영종대로 9 -> 영종대로27번길 9)."""
    if not core:
        return True
    ns = lambda x: re.sub(r"\s+|지하|지상", "", x)
    return ns(core) in ns(refined)


def types_for(q):
    """road-looking query -> road lookup only; lot-looking -> parcel only (halves the calls)."""
    return ("road",) if re.search(r"(?:로|길)(?:\d+번?길)?\s*\d", q) else ("parcel",)


def variants(region, rows, q):
    """ordered [(label, query, types)] for one canonical query q, from the raw addresses of its rows."""
    out, seen = [], {q}

    def add(label, query, types=("road",), core=None):
        query = re.sub(r"\s+", " ", query).strip()
        if query and query not in seen and re.search(r"\d", query):
            seen.add(query)
            out.append((label, query, types, core))
    parts = []
    for (addr, sido, sgg) in rows[:3]:
        p = region_parts(region, addr, sido, sgg)
        if p[0]:
            parts.append((p, addr))
    for (sd, forms, rest), addr in parts:        # 1. modern sgg name as written
        core = addr_core(rest)
        if forms and core:
            q2 = " ".join([sd, forms[0], core])
            add("modern-sgg", q2, types_for(q2), core)
    for (sd, forms, rest), addr in parts:        # 2. cleaned road forms
        for f in forms:
            for rv in road_variants(rest):
                add("clean-road", " ".join([sd, f, rv]), ("road",), rv)
    for (sd, forms, rest), addr in parts:        # 3. parcel: lot written in the text
        lots = []
        m = _LOTRE.match(re.sub(r"\([^)]*\)", " ", rest).strip())
        if m:
            lots.append(m.group(1) + " " + m.group(2))
        for pm in _PARCEL_PAREN.finditer(" ".join(re.findall(r"\(([^)]*)\)", rest))):
            lots.append(pm.group(1) + " " + pm.group(2))
        for f in forms:
            for lot in lots:
                add("parcel", " ".join([sd, f, lot]), ("parcel",), lot)
    for (sd, forms, rest), addr in parts:        # 4. source address as written
        a = clean_addr(addr)
        nop = re.sub(r"\([^)]*\)", " ", a)
        add("raw", sd + " " + (nop.split(None, 1)[1] if len(nop.split()) > 1 else nop), ("road", "parcel"), (rest and re.sub(r"\([^)]*\)", " ", rest)))
    return out


# ---- place search helpers
GENERIC = {"공영주차장", "주차장", "경로당", "도서관", "보건소", "어린이집", "주민센터", "복지관", "청사", "사무소", "센터", "문화센터",
           "체육관", "회관", "마을회관", "학교", "유치원", "시설", "건물", "공원", "화장실", "창고", "관리동", "관리사무소",
           "동사무소", "행정복지센터", "본관", "별관", "신관", "구립", "시립", "군립", "도립", "공공", "제1", "제2"}
GENERIC_HEAD = {"하수종말처리장", "정수장", "변전소", "물재생센터", "배수지", "펌프장", "빗물펌프장", "가압장", "하수처리장", "정수사업소"}
_SUFFIX_TOK = re.compile(r"^(본관|별관|신관|구관|본동|부속동?|창고|부속건물|[A-Za-z]동|제?\d+동|\d+호|\d+층|\d+실|지하\d*층?|동|호|실)$")


def norm_name(s):
    return re.sub(r"[^0-9a-z가-힣]", "", nfc(s or "").lower())


def clean_name(n):
    n = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", nfc(n or ""))
    n = re.sub(r"[,/·]", " ", n)
    toks = [t for t in n.split() if not _SUFFIX_TOK.match(t)]
    return " ".join(toks)


def name_score(name, title):
    """0 = no match, 2 = partial (>=2/3 tokens), 3 = all tokens / contained, 4 = equal."""
    cn = clean_name(name)
    nn, tn = norm_name(cn), norm_name(title)
    if len(nn) < 3 or not tn:
        return 0
    if nn == tn:
        return 4
    toks = [norm_name(t) for t in cn.split()]
    toks = [t for t in toks if len(t) >= 2]
    nd = set(re.findall(r"\d+", nn))
    if nd and not nd <= set(re.findall(r"\d+", tn)):
        return 0
    if (len(nn) >= 4 and nn in tn) or (len(tn) >= 4 and tn in nn):
        return 3
    if not toks:
        return 0
    have = [t for t in toks if t in tn]
    if len(have) == len(toks):
        return 3 if any(t not in GENERIC for t in toks) else 0
    if len(have) >= math.ceil(len(toks) * 2 / 3) and any(t not in GENERIC and len(t) >= 2 for t in have):
        return 2
    return 0


_BAD_TITLE = re.compile(r"입구|정류장|버스|방면|사거리|삼거리|교차로|출구|앞$")


def extra_ok(name, title, sgg_name):
    """single-token names (실내체육관, 골프연습장): the title may add nothing but the district's own name."""
    cn = clean_name(name)
    if len(cn.split()) != 1:
        return True
    nn, tn = norm_name(cn), norm_name(title)
    if nn == tn or nn not in tn:
        return True
    extra = tn.replace(nn, "", 1)
    sn = norm_name(sgg_name)
    return bool(extra) and extra in sn or extra in ("시립", "구립", "군립", "시립")


def generic_names(rows_iter, min_sgg=3):
    """cleaned building names that occur in >= min_sgg different districts: too generic for a name-only match when an address exists."""
    seen = {}
    for name, sgg in rows_iter:
        seen.setdefault(norm_name(clean_name(name)), set()).add(sgg)
    return {n for n, v in seen.items() if len(v) >= min_sgg}


def head_title_ok(head, title):
    """sub-building -> facility fallback: the title must be (almost) just the facility name, not a longer/different one."""
    nh, tn = norm_name(head), norm_name(title)
    if nh.startswith(("한국", "대한")) or nh in GENERIC_HEAD:   # nationwide organisations / generic facility types: many branches
        return False
    return nh == tn or (nh in tn and len(tn) <= len(nh) + 3)


def place_lookup(geo, name, sgg, sido, key, domain, sgg_name=""):
    """-> (lat, lng, title, road, how) of the best accepted hit, or None."""
    cn = clean_name(name)
    if len(norm_name(cn)) < 3:
        return None
    b = (geo.sgg_bbox.get(sgg) if sgg else None) or geo.sido_bbox.get(sido)
    if b is None:
        return None
    pad = 0.01
    bbox = (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)
    attempts = [(cn, name, "")]
    toks = cn.split()
    head = toks[0] if toks else ""
    if len(toks) >= 2 and len(norm_name(head)) >= 4 and norm_name(head) not in GENERIC:
        attempts.append((head, head, "head"))   # sub-building of a named facility -> the facility's point
    for base, ref, how in attempts:
        for query in dict.fromkeys([base, (sgg_name + " " + base).strip()]):
            best = None
            for rank, (title, road, lat, lng) in enumerate(vworld_place(query, key, domain, bbox)):
                if not geo.in_bbox(lat, lng, sgg=sgg or None, sido=sido, pad=pad):
                    continue
                if _BAD_TITLE.search(title) and not _BAD_TITLE.search(name):
                    continue
                sc = name_score(ref, title)
                if sc < (3 if how else 2) or (how and not head_title_ok(ref, title)) or not extra_ok(ref, title, sgg_name):
                    continue
                k = (sc, 1 if road else 0, -rank)
                if best is None or k > best[0]:
                    best = (k, (lat, lng, title, road, how))
            if best:
                return best[1]
    return None


def kakao(q, key, _domain=None):
    url = "https://dapi.kakao.com/v2/local/search/address.json?" + urllib.parse.urlencode({"query": q, "size": 1})
    d = http_json(url, headers={"Authorization": "KakaoAK " + key})
    docs = d.get("documents") or []
    if docs:
        return float(docs[0]["y"]), float(docs[0]["x"])
    return None


def load_cache():
    return json.load(open(CACHE_FILE, encoding="utf-8")) if os.path.exists(CACHE_FILE) else {}


def save_cache(c):
    write_json(CACHE_FILE, c)


def collect(geo, region):
    """unique queries -> (sgg, sido, [(addr, sido, sgg) of the rows behind it])."""
    path = os.path.join(PROC, "buildings.csv")
    if not os.path.exists(path):
        sys.exit("data/processed/buildings.csv missing: run normalize.py first")
    qs = {}
    nrows = 0
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if r["lat"] or not r["addr"]:
            continue
        nrows += 1
        q = geocode_query(region, geo, r["addr"], r["sido"], r["sgg"])
        if q:
            e = qs.setdefault(q, (r["sgg"], r["sido"], []))
            e[2].append((r["addr"], r["sido"], r["sgg"]))
    return qs, nrows


def place_rows(geo, region, cache):
    """rows still without any usable point -> {place key: (name, sgg, sido, addr)} (first row per key)."""
    out = {}
    allrows = list(csv.DictReader(open(os.path.join(PROC, "buildings.csv"), encoding="utf-8")))
    generic = generic_names((r["name"], r["sgg"]) for r in allrows)
    for r in allrows:
        if r["lat"] or not r["name"].strip():
            continue
        if r["addr"] and norm_name(clean_name(r["name"])) in generic:
            continue  # 실내체육관 / 경로당 ...: a name search would hit some other town's building
        q = geocode_query(region, geo, r["addr"], r["sido"], r["sgg"]) if r["addr"] else ""
        h = cache.get(q) if q else None
        if h and h.get("p") in ("vworld", "kakao") and h.get("lat") is not None and \
                geo.in_bbox(h["lat"], h["lng"], sgg=r["sgg"] or None, sido=r["sido"], pad=0.02):
            continue
        if q and q not in cache:
            continue  # canonical query not tried yet: run the address passes first
        out.setdefault(place_key(r["sgg"], r["name"]), (r["name"], r["sgg"], r["sido"], r["addr"]))
    return out


def run_pool(todo, work, cache, workers, label):
    """work(item) -> (cache key, entry | None); stops cleanly when a provider is disabled."""
    stats = {"ok": 0, "miss": 0, "reject": 0}
    t0 = time.time()
    done = 0
    try:
        with concurrent.futures.ThreadPoolExecutor(workers) as ex:
            for i in range(0, len(todo), 200):
                if STOP.is_set():
                    break
                futs = [ex.submit(work, it) for it in todo[i:i + 200]]
                for f in futs:
                    try:
                        res = f.result()
                    except Disabled as e:
                        if not STOP.is_set():
                            print("provider disabled: %s" % e, flush=True)
                        STOP.set()
                        continue
                    except Exception as e:  # keep going; the key stays uncached and is retried next run
                        print("error: %s" % str(e)[:120], flush=True)
                        continue
                    if not res:
                        continue
                    k, entry = res
                    cache[k] = entry
                    stats["miss" if entry["p"] == "miss" else "reject" if entry["p"] == "reject" else "ok"] += 1
                done += len(futs)
                save_cache(cache)
                print("  %s %d/%d  %s  (%.0f s)" % (label, done, len(todo), stats, time.time() - t0), flush=True)
    except KeyboardInterrupt:
        STOP.set()
        print("interrupted, saving cache")
    finally:
        save_cache(cache)
    left = len(todo) - done
    print("%s done:" % label, stats, "cache size", len(cache), ("| STOPPED early, ~%d items left" % left) if STOP.is_set() else "")


def accept(geo, lat, lng, sgg, sido):
    return geo.in_bbox(lat, lng, sgg=sgg or None, sido=sido, pad=0.02)


def run_variants(region, geo, qs, cache, key, dom, limit, retry_orig, workers):
    todo = [q for q in qs if q not in cache or (cache[q].get("p") == "miss" and not cache[q].get("c"))]
    if limit:
        todo = todo[:limit]
    print("variant chain: %d queries to do" % len(todo), flush=True)

    def work(q):
        if STOP.is_set():
            return None
        sgg, sido, rows = qs[q]
        chain = []
        if retry_orig or q not in cache:
            chain.append(("orig", q, types_for(q), None))
        chain += variants(region, rows, q)
        rej = None
        for label, vq, types, core in chain:
            for typ in types:
                h = vworld_addr(vq, key, dom, typ)
                if not h or not consistent(core, h[2]):
                    continue
                if accept(geo, h[0], h[1], sgg, sido):
                    return q, {"p": "vworld", "lat": round(h[0], 6), "lng": round(h[1], 6), "v": label, "r": h[2]}
                rej = rej or {"p": "reject", "lat": round(h[0], 6), "lng": round(h[1], 6), "why": "outside sgg bbox", "by": "vworld", "v": label}
        return q, (rej if rej and not (q in cache and cache[q].get("p") == "reject") else {"p": "miss", "c": 1})
    run_pool(todo, work, cache, workers, "variants")


def run_places(region, geo, cache, key, dom, limit, workers):
    rows = place_rows(geo, region, cache)
    todo = [k for k in rows if k not in cache]
    if limit:
        todo = todo[:limit]
    print("place search: %d keys (%d cached), %d to do" % (len(rows), len(rows) - len(todo), len(todo)), flush=True)

    def work(k):
        if STOP.is_set():
            return None
        name, sgg, sido, addr = rows[k]
        sn = geo.sgg_name.get(sgg, "") if sgg else ""
        h = place_lookup(geo, name, sgg, sido, key, dom, sn)
        if not h:
            return k, {"p": "miss"}
        e = {"p": "vworld-place", "lat": round(h[0], 6), "lng": round(h[1], 6), "t": h[2], "r": h[3]}
        if h[4]:
            e["m"] = h[4]
        return k, e
    run_pool(todo, work, cache, workers, "places")


def run(providers, qs, cache, geo, rate, limit, retry_misses):
    todo = [q for q in qs if q not in cache or (retry_misses and cache[q].get("p") == "miss")]
    if limit:
        todo = todo[:limit]
    print("queries: %d unique, %d cached, %d to do" % (len(qs), len(qs) - len(todo), len(todo)), flush=True)

    def work(q):
        if STOP.is_set():
            return None
        sgg, sido, _rows = qs[q]
        hit = None
        for name, fn in list(providers):
            try:
                hit = fn(q)
            except Disabled:
                providers.remove((name, fn))
                if not providers:
                    raise
                continue
            except RuntimeError as e:
                print("provider %s error on %r: %s" % (name, q, e), flush=True)
                return None
            if hit:
                hit = (name,) + hit
                break
        if hit is None:
            return q, {"p": "miss"}
        if not accept(geo, hit[1], hit[2], sgg, sido):
            return q, {"p": "reject", "lat": round(hit[1], 6), "lng": round(hit[2], 6), "why": "outside sgg bbox", "by": hit[0]}
        return q, {"p": hit[0], "lat": round(hit[1], 6), "lng": round(hit[2], 6)}
    run_pool(todo, work, cache, 4, "geocode")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rate", type=float, default=5.0, help="max HTTP requests per second, all threads together (default 5)")
    ap.add_argument("--retry-misses", action="store_true")
    ap.add_argument("--variants", action="store_true", help="variant chain for missed queries (VWorld only)")
    ap.add_argument("--places", action="store_true", help="VWorld place search by building name for still-unplaced rows")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    LIMITER.gap = 1.0 / a.rate
    vk, kk = os.environ.get("VWORLD_KEY", "").strip(), os.environ.get("KAKAO_KEY", "").strip()
    if not vk and not kk and not a.dry_run:
        print("No VWORLD_KEY / KAKAO_KEY set: geocoding skipped (rows without coordinates stay in nocoord/*.json).\n"
              "Free keys: VWorld https://www.vworld.kr (primary), Kakao Developers REST key (fallback).\n"
              "Run e.g.:  VWORLD_KEY=xxxx make geocode")
        return 0
    geo = Geo()
    region = Region(geo)
    qs, nrows = collect(geo, region)
    cache = load_cache()
    print("rows without coordinates: %d -> %d unique geocodable queries" % (nrows, len(qs)))
    if a.dry_run:
        todo = [q for q in qs if q not in cache]
        print("dry run: %d queries would be sent (%.1f min at %.1f req/s, worst case 2-3 calls each)" % (len(todo), len(todo) / a.rate / 60, a.rate))
        return 0
    dom = os.environ.get("VWORLD_DOMAIN", "").strip() or None
    if a.variants or a.places:
        if not vk:
            sys.exit("--variants/--places need VWORLD_KEY")
        if a.variants:
            run_variants(region, geo, qs, cache, vk, dom, a.limit, a.retry_misses, a.workers)
        if a.places and not STOP.is_set():
            run_places(region, geo, cache, vk, dom, a.limit, a.workers)
        return 0
    providers = []
    if vk:
        providers.append(("vworld", lambda q: vworld(q, vk, dom)))
    if kk:
        providers.append(("kakao", lambda q: kakao(q, kk)))
    run(providers, qs, cache, geo, a.rate, a.limit, a.retry_misses)
    return 0


if __name__ == "__main__":
    sys.exit(main())
