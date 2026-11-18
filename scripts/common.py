"""Shared helpers for the asbestos-map pipeline (Python 3.9 stdlib only).

 - paths
 - table readers (CSV in cp949/utf-8, XLSX via zipfile, JSON)
 - boundaries: TopoJSON decode (+ topology-preserving simplification), bbox, point-in-polygon
 - address handling: sido / sgg resolver with alias table for 2026 renames, address cores
 - TM -> WGS84 (EPSG:5174 / 5179 / 5186)
"""
import csv, io, json, math, os, re, unicodedata, zipfile
import xml.etree.ElementTree as ET

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RAW = os.path.join(ROOT, "data", "raw")
PROC = os.path.join(ROOT, "data", "processed")
CACHE = os.path.join(ROOT, "data", "cache")
BOUND = os.path.join(ROOT, "data", "boundaries")
WEBDATA = os.path.join(ROOT, "web", "data")
SOURCES_JSON = os.path.join(ROOT, "data", "sources.json")
TOPO_SGG = os.path.join(BOUND, "skorea-municipalities-2018-topo-simple.json")
TOPO_SIDO = os.path.join(BOUND, "skorea-provinces-2018-topo-simple.json")


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


# --------------------------------------------------------------------------- readers
def decode_bytes(b):
    if b[:3] == b"\xef\xbb\xbf":
        return b[3:].decode("utf-8")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp949", "replace")


def read_csv(path):
    text = decode_bytes(open(path, "rb").read())
    rows = list(csv.reader(io.StringIO(text, newline=None)))  # newline=None: CR-only files ok
    rows = [r for r in rows if any(c.strip() for c in r)]
    return rows[0], rows[1:]


def _col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    z = zipfile.ZipFile(path)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            ss.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])
                              if True))  # rich text + plain; phonetic <rPh><t> is rare in these files
    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for r in sheet.find("m:sheetData", ns).findall("m:row", ns):
        cells = {}
        for c in r.findall("m:c", ns):
            v = c.find("m:v", ns)
            t = c.get("t")
            if t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % ns["m"]))
            elif v is None or v.text is None:
                val = ""
            elif t == "s":
                val = ss[int(v.text)]
            else:
                val = v.text
                if re.fullmatch(r"-?\d+\.0", val):
                    val = val[:-2]
            cells[_col(c.get("r"))] = val
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    return rows[0], rows[1:]


def read_table(path):
    """-> (header list, rows as list of lists)."""
    ext = path.rsplit(".", 1)[-1].lower()
    if ext == "xlsx":
        return read_xlsx(path)
    if ext == "json":
        data = json.load(open(path, encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("data") or data.get("records") or []
        hdr = list(data[0].keys())
        return hdr, [[str(r.get(h, "") if r.get(h) is not None else "") for h in hdr] for r in data]
    return read_csv(path)


# --------------------------------------------------------------------------- geometry
def _dp(pts, eps):
    """Douglas-Peucker on a list of (x, y); endpoints kept. Iterative."""
    n = len(pts)
    if n < 3:
        return pts
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    e2 = eps * eps
    while stack:
        i, j = stack.pop()
        x1, y1 = pts[i]; x2, y2 = pts[j]
        dx, dy = x2 - x1, y2 - y1
        L = dx * dx + dy * dy
        md, mk = -1.0, -1
        for k in range(i + 1, j):
            px, py = pts[k]
            if L == 0:
                d = (px - x1) ** 2 + (py - y1) ** 2
            else:
                t = ((px - x1) * dx + (py - y1) * dy) / L
                t = max(0.0, min(1.0, t))
                d = (px - x1 - t * dx) ** 2 + (py - y1 - t * dy) ** 2
            if d > md:
                md, mk = d, k
        if md > e2 and mk > 0:
            keep[mk] = True
            stack.append((i, mk)); stack.append((mk, j))
    return [p for p, k in zip(pts, keep) if k]


def load_topo(path, simplify_deg=0.0, ndigits=5):
    """TopoJSON -> list of dict(properties, type, coordinates) in WGS84 (GeoJSON geometry coords).
    simplify_deg: DP tolerance in degrees applied per ARC (topology preserving, shared borders stay shared)."""
    t = json.load(open(path, encoding="utf-8"))
    sx, sy = t["transform"]["scale"]; tx, ty = t["transform"]["translate"]
    arcs = []
    for arc in t["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx; y += dy
            pts.append((x * sx + tx, y * sy + ty))
        if simplify_deg > 0:
            pts = _dp(pts, simplify_deg)
        arcs.append([(round(a, ndigits), round(b, ndigits)) for a, b in pts])

    def ring(idx):
        out = []
        for a in idx:
            seg = arcs[a] if a >= 0 else arcs[~a][::-1]
            out.extend(seg if not out else seg[1:])
        return [list(p) for p in out]

    def poly(rings):
        rs = [ring(r) for r in rings]
        rs = [r for r in rs if len(r) >= 4]          # drop degenerate rings (tiny islands collapsed by quantisation)
        return rs if rs and len(rs[0]) >= 4 and rings and len(ring(rings[0])) >= 4 else None

    feats = []
    obj = list(t["objects"].values())[0]
    for g in obj["geometries"]:
        if g["type"] == "Polygon":
            pl = [poly(g["arcs"])]
        elif g["type"] == "MultiPolygon":
            pl = [poly(p) for p in g["arcs"]]
        else:
            continue
        pl = [p for p in pl if p]
        if not pl:
            continue
        if g["type"] == "Polygon" or len(pl) == 1 and g["type"] == "Polygon":
            feats.append(dict(properties=g["properties"], type="Polygon", coordinates=pl[0]))
        else:
            feats.append(dict(properties=g["properties"], type="MultiPolygon", coordinates=pl))
    return feats


def polygons(feat):
    return [feat["coordinates"]] if feat["type"] == "Polygon" else feat["coordinates"]


def feat_bbox(feat):
    xs, ys = [], []
    for poly in polygons(feat):
        for p in poly[0]:
            xs.append(p[0]); ys.append(p[1])
    return [min(xs), min(ys), max(xs), max(ys)]


def _in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]; xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def in_feat(x, y, feat, bbox=None):
    b = bbox or feat_bbox(feat)
    if not (b[0] <= x <= b[2] and b[1] <= y <= b[3]):
        return False
    for poly in polygons(feat):
        if _in_ring(x, y, poly[0]) and not any(_in_ring(x, y, h) for h in poly[1:]):
            return True
    return False


CURRENT_SIDO_NAME = {"32": "강원특별자치도", "35": "전북특별자치도"}


class Geo:
    """Boundary index: bbox per sgg / sido, point -> sgg lookup."""

    def __init__(self):
        self.sgg = load_topo(TOPO_SGG)
        self.sido = load_topo(TOPO_SIDO)
        self.sgg_bbox = {f["properties"]["code"]: feat_bbox(f) for f in self.sgg}
        self.sido_bbox = {f["properties"]["code"]: feat_bbox(f) for f in self.sido}
        self.sgg_name = {f["properties"]["code"]: f["properties"]["name"] for f in self.sgg}
        for f in self.sido:  # 2018 boundary names -> current official names
            f["properties"]["name"] = CURRENT_SIDO_NAME.get(f["properties"]["code"], f["properties"]["name"])
        self.sido_name = {f["properties"]["code"]: f["properties"]["name"] for f in self.sido}
        self._by_sido = {}
        for f in self.sgg:
            self._by_sido.setdefault(f["properties"]["code"][:2], []).append(f)

    def in_bbox(self, lat, lng, sgg=None, sido=None, pad=0.01):
        b = None
        if sgg and sgg in self.sgg_bbox:
            b = self.sgg_bbox[sgg]
        elif sido and sido in self.sido_bbox:
            b = self.sido_bbox[sido]
        if b is None:
            return True
        return b[0] - pad <= lng <= b[2] + pad and b[1] - pad <= lat <= b[3] + pad

    def locate(self, lat, lng):
        """-> sgg code containing the point (exact polygon test) or None."""
        for code, b in self.sgg_bbox.items():
            if b[0] <= lng <= b[2] and b[1] <= lat <= b[3]:
                f = next(x for x in self._by_sido[code[:2]] if x["properties"]["code"] == code)
                if in_feat(lng, lat, f, b):
                    return code
        return None


# --------------------------------------------------------------------------- TM -> WGS84
_GRS80 = (6378137.0, 1 / 298.257222101)
_BESSEL = (6377397.155, 1 / 299.1528128)
_WGS84 = (6378137.0, 1 / 298.257223563)
TM = {  # epsg: (ellipsoid, lat0, lon0, k0, FE, FN, towgs84 or None)
    5179: (_GRS80, 38.0, 127.5, 0.9996, 1000000.0, 2000000.0, None),
    5186: (_GRS80, 38.0, 127.0, 1.0, 200000.0, 600000.0, None),
    5174: (_BESSEL, 38.0, 127.0028902777778, 1.0, 200000.0, 500000.0,
           (-115.80, 474.99, 674.11, 1.16, -2.31, -1.63, 6.43)),
}


def _meridian(phi, a, e2):
    return a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
                - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
                + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
                - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))


def _geoc(lat, lon, h, a, f):
    e2 = 2 * f - f * f
    N = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return ((N + h) * math.cos(lat) * math.cos(lon), (N + h) * math.cos(lat) * math.sin(lon),
            (N * (1 - e2) + h) * math.sin(lat))


def _geod(X, Y, Z, a, f):
    e2 = 2 * f - f * f
    p = math.hypot(X, Y)
    lat = math.atan2(Z, p * (1 - e2))
    for _ in range(8):
        N = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        lat = math.atan2(Z + e2 * N * math.sin(lat), p)
    return lat, math.atan2(Y, X)


def tm_to_wgs84(x, y, epsg):
    """Projected (x=easting, y=northing) -> (lat, lng) WGS84."""
    (a, f), lat0, lon0, k0, fe, fn, tow = TM[epsg]
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    M = _meridian(math.radians(lat0), a, e2) + (y - fn) / k0
    mu = M / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    p1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
          + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    C1 = ep2 * math.cos(p1) ** 2; T1 = math.tan(p1) ** 2
    N1 = a / math.sqrt(1 - e2 * math.sin(p1) ** 2)
    R1 = a * (1 - e2) / (1 - e2 * math.sin(p1) ** 2) ** 1.5
    D = (x - fe) / (N1 * k0)
    lat = p1 - (N1 * math.tan(p1) / R1) * (D ** 2 / 2 - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * ep2) * D ** 4 / 24
                                          + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * ep2 - 3 * C1 ** 2) * D ** 6 / 720)
    lon = math.radians(lon0) + (D - (1 + 2 * T1 + C1) * D ** 3 / 6
                                + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * ep2 + 24 * T1 ** 2) * D ** 5 / 120) / math.cos(p1)
    if tow:  # Helmert (position vector) to WGS84
        dx, dy, dz, rx, ry, rz, ds = tow
        X, Y, Z = _geoc(lat, lon, 0.0, a, f)
        s = 1 + ds * 1e-6
        r = [v / 206264.806247 for v in (rx, ry, rz)]
        X2 = dx + s * (X - r[2] * Y + r[1] * Z)
        Y2 = dy + s * (r[2] * X + Y - r[0] * Z)
        Z2 = dz + s * (-r[1] * X + r[0] * Y + Z)
        lat, lon = _geod(X2, Y2, Z2, *_WGS84)
    return math.degrees(lat), math.degrees(lon)


def guess_epsg(x, y):
    """Only 5179 is unambiguous by magnitude; others need an explicit override."""
    if 700000 < x < 1500000 and 1300000 < y < 2200000:
        return 5179
    return None


# --------------------------------------------------------------------------- address / region
SIDO_ALIAS = {
    "서울특별시": "11", "서울시": "11", "서울": "11",
    "부산광역시": "21", "부산시": "21", "부산": "21",
    "대구광역시": "22", "대구시": "22", "대구": "22",
    "인천광역시": "23", "인천시": "23", "인천": "23",
    "광주광역시": "24", "광주": "24",
    "대전광역시": "25", "대전시": "25", "대전": "25",
    "울산광역시": "26", "울산시": "26", "울산": "26",
    "세종특별자치시": "29", "세종시": "29", "세종": "29",
    "경기도": "31", "경기": "31",
    "강원도": "32", "강원특별자치도": "32", "강원": "32",
    "충청북도": "33", "충북": "33",
    "충청남도": "34", "충남": "34",
    "전라북도": "35", "전북특별자치도": "35", "전북": "35",
    "전라남도": "36", "전남": "36",
    "경상북도": "37", "경북": "37",
    "경상남도": "38", "경남": "38",
    "제주특별자치도": "39", "제주도": "39", "제주": "39",
    "전남광주통합특별시": "24|36",  # 2026 merger: resolved per district name
}
SIDO_MODERN = {"11": "서울특별시", "21": "부산광역시", "22": "대구광역시", "23": "인천광역시", "24": "광주광역시",
               "25": "대전광역시", "26": "울산광역시", "29": "세종특별자치시", "31": "경기도", "32": "강원특별자치도",
               "33": "충청북도", "34": "충청남도", "35": "전북특별자치도", "36": "전라남도", "37": "경상북도",
               "38": "경상남도", "39": "제주특별자치도"}
# 2026 renamed / split districts -> 2018 boundary code (documented alias table)
SGG_ALIAS = {
    "23": {"영종구": "23010", "제물포구": "23010", "서해구": "23080", "검단구": "23080",
           "미추홀구": "23030"},  # 남구 -> 미추홀구 (2018, after the boundary vintage)
    "22": {"군위군": "37310"},
    "23x": {},  # 군위 moved 경북 -> 대구 in 2023; the 2018 boundary still has it under 경북 (37)
}
# 제물포구 = 구 중구 + 구 동구: these 법정동 belong to the old 동구 polygon (23020)
INCHEON_DONGGU_DONG = {"만석동", "화수동", "송현동", "화평동", "창영동", "금창동", "송림동", "금곡동"}
ZIP_RE = re.compile(r"^\s*[\[\(]\d{5}[\]\)]\s*")


class Region:
    """Resolve (sido, sgg) from address text. Index built from the 2018 boundary names."""

    def __init__(self, geo):
        self.plain = {}      # sido -> {name: code}   (names w/o 시+구 split)
        self.city = {}       # sido -> {city: {gu: code}}
        self.canon = {}      # code -> canonical query name ("수원시 팔달구")
        for code, name in geo.sgg_name.items():
            sd = code[:2]
            m = re.match(r"^(.+?시)(.+구)$", name)
            if m and code[-1] != "0":
                self.city.setdefault(sd, {}).setdefault(m.group(1), {})[m.group(2)] = code
                self.canon[code] = "%s %s" % (m.group(1), m.group(2))
            else:
                self.plain.setdefault(sd, {})[name] = code
                self.canon[code] = name
        self.plain.setdefault("29", {})["세종특별자치시"] = "29010"
        self.canon["29010"] = ""
        self.sgg_name = geo.sgg_name

    def _names(self, sidos):
        names = set()
        for sd in sidos:
            names |= set(self.plain.get(sd, {})) | set(SGG_ALIAS.get(sd, {}))
            for city, gus in self.city.get(sd, {}).items():
                names.add(city); names |= set(gus)
        return sorted(names, key=len, reverse=True)

    def _split_glued(self, sidos, toks):
        """'유성구신봉동' -> '유성구', '신봉동' (also the 2nd token for '창원시 마산회원구율림교30')."""
        names = self._names(sidos)
        toks = list(toks)
        for k in (0, 1):
            if k < len(toks) and toks[k] not in names:
                for n in names:
                    if len(n) >= 3 and toks[k].startswith(n) and len(toks[k]) > len(n) and (k == 0 or (n.endswith("구") and toks[0] in self.city.get(sidos[0], {}))):
                        toks[k:k + 1] = [n, toks[k][len(n):]]
                        break
        return toks

    def _find(self, sidos, toks):
        """-> (sido, sgg code, tokens consumed) or (sido, None, 0)."""
        t1 = toks[0] if toks else ""
        t2 = toks[1] if len(toks) > 1 else ""
        for sd in sidos:
            if sd == "29":
                return sd, "29010", (1 if t1 in ("세종특별자치시", "세종시", "세종") else 0)
        for sd in sidos:
            al = SGG_ALIAS.get(sd, {})
            if t1 in al:
                code = al[t1]
                return sd, code, 1
        for sd in sidos:
            if t1 in self.plain.get(sd, {}):
                return sd, self.plain[sd][t1], 1
            c = self.city.get(sd, {}).get(t1)
            if c is not None:
                if t2 in c:
                    return sd, c[t2], 2
                return sd, None, 1  # city split into 구 but the address has none
            # glued "수원시팔달구"
            for city, gus in self.city.get(sd, {}).items():
                for gu, code in gus.items():
                    if t1 == city + gu:
                        return sd, code, 1
        return sidos[0], None, 0

    def resolve(self, addr, pub_sido=None, pub_sgg=None):
        """-> dict(sido, sgg, rest, note). sgg None when unresolved. `rest` = address after region tokens."""
        a = ZIP_RE.sub("", nfc(addr))
        a = a.replace("\t", " ")
        toks = a.split()
        sido = None
        i = 0
        if toks and toks[0] in SIDO_ALIAS:
            sido = SIDO_ALIAS[toks[0]]
            i = 1
        elif toks and re.sub(r"(특별시|광역시)$", "", toks[0]) in ("서울", "부산", "대구", "인천", "대전", "울산", "광주"):
            sido = SIDO_ALIAS[re.sub(r"(특별시|광역시)$", "", toks[0])]
            i = 1
        had_sido = sido is not None
        if sido is None and pub_sido:
            sido = SIDO_ALIAS.get(pub_sido)
        if sido is None:
            return dict(sido=None, sgg=None, rest=a, note="no sido")
        sidos = sido.split("|")
        toks = toks[:i] + self._split_glued(sidos, toks[i:])
        sd, code, used = self._find(sidos, toks[i:])
        rest_toks = toks[i + used:]
        if code is None and pub_sgg:
            # publisher fallback (e.g. "영도구"); skip if the publisher district has 구 sub-split
            sd2, code2, used2 = self._find(sidos, [pub_sgg])
            if code2 and used2:
                sd, code = sd2, code2
        # 제물포구 = old 중구 + old 동구
        if code == "23010" and toks[i:i + 1] == ["제물포구"]:
            dongs = re.findall(r"([가-힣]+동)\d*가?", " ".join(rest_toks))
            if any(d in INCHEON_DONGGU_DONG for d in dongs):
                code = "23020"
        if code:
            sd = code[:2]
        note = "" if code else ("city without gu" if used else "sgg not found")
        return dict(sido=sd, sgg=code, rest=" ".join(rest_toks), note=note, had_sido=had_sido)


def clean_addr(addr):
    a = ZIP_RE.sub("", nfc(addr or ""))
    a = re.sub(r"[\t\r\n]+", " ", a)
    return re.sub(r"\s+", " ", a).strip()


_ROAD = re.compile(r"^(.*?\S*(?:로|길)(?:\d+번?길)?)\s*(\d+(?:-\d+)?)(?=$|[\s,(]|\D)")
_LOT = re.compile(r"^(.*?\S*(?:동|리|가|읍|면))\s+(산?\d+(?:-\d+)?)(?=$|\s|,|\()")


def addr_core(rest):
    """Region-less address -> normalized core for dedupe / geocode ('대청로 136')."""
    s = re.sub(r"\([^)]*\)", " ", rest or "")
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = s.split(",")[0]
    s = re.sub(r"\s+", " ", s).strip()
    m = _ROAD.match(s)
    if m:
        num = re.sub(r"-0$", "", m.group(2))
        return (m.group(1) + " " + num).replace("  ", " ")
    m = _LOT.match(s)
    if m:
        return m.group(1) + " " + re.sub(r"-0$", "", m.group(2))
    return s


def geocode_query(region, geo, addr, sido, sgg):
    """Query string for geocoders: modern sido name + canonical (2018) sgg name + address core."""
    r = region.resolve(addr, pub_sido=SIDO_MODERN.get(sido), pub_sgg=None)
    rest = r["rest"]
    core = addr_core(rest)
    if not core or not re.search(r"\d", core):
        return ""
    if r["sgg"] and sgg and r["sgg"] != sgg and sgg in region.canon:
        pass
    code = sgg if sgg and sgg in region.canon else r["sgg"]
    parts = [SIDO_MODERN.get((code or sido or "")[:2], ""), region.canon.get(code, "") if code else "", core]
    return " ".join(p for p in parts if p)


def name_key(s):
    s = nfc(s or "").lower()
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", s)
    s = re.sub(r"㈜|\(주\)|주식회사|[^0-9a-z가-힣]", "", s)
    return s


def write_json(path, obj, compact=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
