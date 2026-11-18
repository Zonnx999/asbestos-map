#!/usr/bin/env python3
"""Geocode address-only rows: VWorld (env VWORLD_KEY) -> Kakao (env KAKAO_KEY) -> none.

 - reads data/processed/buildings.csv, takes rows without coordinates, builds one query per unique address
   (common.geocode_query: modern sido + canonical 2018 sgg name + road/lot core), skips cached ones;
 - cache: data/cache/geocode.json  {query: {"lat","lng","p"} | {"p":"miss"} | {"p":"reject","lat","lng","why"}}
   written every 200 queries and on exit, so it is resumable (Ctrl-C safe);
 - sanity check: a point must lie in its sgg bbox (sido bbox if sgg unknown) or it is stored as "reject";
 - NO key set -> prints a notice and exits 0.  Nominatim is deliberately not used (no bulk use per OSM policy).
build_web.py merges the cache into the output (and re-applies the bbox check).
Usage: geocode.py [--limit N] [--rate REQ_PER_SEC] [--retry-misses] [--dry-run]
Env:   VWORLD_KEY, VWORLD_DOMAIN (optional, if the key is bound to a domain), KAKAO_KEY
"""
import argparse, csv, json, os, sys, time, urllib.error, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PROC, CACHE, Geo, Region, geocode_query, write_json

CACHE_FILE = os.path.join(CACHE, "geocode.json")
UA = "asbestos-map-geocoder/0.1"


class Disabled(Exception):
    pass


def http_json(url, headers=None, tries=3):
    last = None
    for k in range(tries):
        try:
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


def vworld(q, key, domain):
    for typ in ("road", "parcel"):
        p = dict(service="address", request="getcoord", version="2.0", crs="epsg:4326", address=q, refine="true",
                 simple="false", format="json", type=typ, key=key)
        if domain:
            p["domain"] = domain
        r = http_json("https://api.vworld.kr/req/address?" + urllib.parse.urlencode(p))["response"]
        if r.get("status") == "OK":
            pt = r["result"]["point"]
            return float(pt["y"]), float(pt["x"])
        if r.get("status") == "ERROR":
            msg = json.dumps(r.get("error", ""), ensure_ascii=False)
            if "KEY" in msg.upper() or "LIMIT" in msg.upper() or "OVER" in msg.upper():
                raise Disabled("VWorld: " + msg[:120])
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
    """unique queries -> sgg (first seen)."""
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
            qs.setdefault(q, (r["sgg"], r["sido"]))
    return qs, nrows


def run(providers, qs, cache, geo, rate, limit, retry_misses):
    todo = [q for q in qs if q not in cache or (retry_misses and cache[q].get("p") == "miss")]
    if limit:
        todo = todo[:limit]
    print("queries: %d unique, %d cached, %d to do" % (len(qs), len(qs) - len(todo), len(todo)), flush=True)
    gap = 1.0 / rate
    stats = {"ok": 0, "miss": 0, "reject": 0}
    t0 = time.time()
    try:
        for n, q in enumerate(todo, 1):
            hit = None
            for name, fn in list(providers):
                try:
                    hit = fn(q)
                except Disabled as e:
                    print("provider %s disabled: %s" % (name, e), flush=True)
                    providers.remove((name, fn))
                    continue
                except RuntimeError as e:
                    print("provider %s error on %r: %s" % (name, q, e), flush=True)
                    continue
                if hit:
                    hit = (name,) + hit
                    break
                time.sleep(gap)
            sgg, sido = qs[q]
            if hit is None:
                cache[q] = {"p": "miss"}; stats["miss"] += 1
            elif not geo.in_bbox(hit[1], hit[2], sgg=sgg or None, sido=sido, pad=0.02):
                cache[q] = {"p": "reject", "lat": round(hit[1], 6), "lng": round(hit[2], 6), "why": "outside sgg bbox", "by": hit[0]}
                stats["reject"] += 1
            else:
                cache[q] = {"p": hit[0], "lat": round(hit[1], 6), "lng": round(hit[2], 6)}; stats["ok"] += 1
            time.sleep(gap)
            if n % 200 == 0:
                save_cache(cache)
                print("  %d/%d  %s  (%.0f s)" % (n, len(todo), stats, time.time() - t0), flush=True)
            if not providers:
                print("all providers disabled, stopping (progress saved)")
                break
    except KeyboardInterrupt:
        print("interrupted, saving cache")
    finally:
        save_cache(cache)
    print("done:", stats, "cache size", len(cache))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rate", type=float, default=5.0, help="requests per second per provider call (default 5)")
    ap.add_argument("--retry-misses", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
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
    providers = []
    dom = os.environ.get("VWORLD_DOMAIN", "").strip() or None
    if vk:
        providers.append(("vworld", lambda q: vworld(q, vk, dom)))
    if kk:
        providers.append(("kakao", lambda q: kakao(q, kk)))
    run(providers, qs, cache, geo, a.rate, a.limit, a.retry_misses)
    return 0


if __name__ == "__main__":
    sys.exit(main())
