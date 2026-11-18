#!/usr/bin/env python3
"""Inspect data/raw files: encoding, header, row count -> data/search/raw_summary.json"""
import csv, glob, io, json, os, re, sys, zipfile
import xml.etree.ElementTree as ET
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
def xlsx_rows(path, n=None):
    z = zipfile.ZipFile(path)
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            ss.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
    sheet = sorted(x for x in z.namelist() if x.startswith("xl/worksheets/sheet"))[0]
    rows = []
    for r in ET.fromstring(z.read(sheet)).iter("{%s}row" % ns["m"]):
        vals = []
        for c in r.findall("m:c", ns):
            v = c.find("m:v", ns)
            t = c.get("t")
            if v is None:
                is_ = c.find("m:is", ns); vals.append("".join(x.text or "" for x in is_.iter("{%s}t" % ns["m"])) if is_ is not None else "")
            else:
                vals.append(ss[int(v.text)] if t == "s" else v.text)
        rows.append(vals)
    return rows
def sniff(b):
    for enc in ("utf-8-sig", "cp949"):
        try: b.decode(enc); return enc
        except UnicodeDecodeError: pass
    return "unknown"
out = {}
for p in sorted(glob.glob(os.path.join(ROOT, "data", "raw", "*"))):
    name = os.path.basename(p); pk = name.split("_")[0]
    b = open(p, "rb").read()
    d = {"file": name, "bytes": len(b)}
    try:
        if name.endswith(".csv"):
            enc = sniff(b); d["encoding"] = enc
            rows = list(csv.reader(b.decode(enc).splitlines()))
        elif name.endswith(".xlsx"):
            d["encoding"] = "xlsx"; rows = xlsx_rows(p)
        elif name.endswith(".json"):
            d["encoding"] = "utf-8"; j = json.loads(b); d["header"] = list(j[0].keys()); d["rows"] = len(j); out[pk] = d; continue
        else: continue
        # header = first row with >=3 non-empty cells
        hi = next((i for i, r in enumerate(rows) if sum(1 for c in r if c.strip()) >= 3), 0)
        d["header_row_index"] = hi
        d["header"] = [c.strip() for c in rows[hi]]
        d["rows"] = sum(1 for r in rows[hi + 1:] if any(c.strip() for c in r))
        d["sample"] = rows[hi + 1] if len(rows) > hi + 1 else []
    except Exception as e:
        d["error"] = repr(e)
    out[pk] = d
json.dump(out, open(os.path.join(ROOT, "data", "search", "raw_summary.json"), "w"), ensure_ascii=False, indent=1)
for pk, d in out.items(): print(pk, d.get("encoding"), d.get("rows"), d.get("header"))
