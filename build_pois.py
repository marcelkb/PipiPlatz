"""Build data/pois.jsonl, the local copy of every PipiPlatz stop in Germany, from Geofabrik's Germany
extract, so a search answers from disk instead of waiting up to 35 s for the public Overpass servers.

Update by hand, every few weeks or when stops are missing:
    del data\\germany-latest.osm.pbf     (the next run downloads the current one, ~4.9 GB)
    python build_pois.py [--force]
    python compare_local.py               (local vs live Overpass, 10 places)
then commit data/pois.jsonl and data/pois.meta.json. The .pbf and .poly stay out of git.

The selectors are the live search's own (app.GROUPS), evaluated here in Python. WC evidence (cafes,
bakeries, restaurants) is kept when it lies within 150 m of a supermarket, furniture / DIY store or
charging station, the widest radius enrich() looks at. Centres of ways and areas are the middle of
their bounding box, like Overpass `out center`.

Refuses to write when a kind lost more than DROP_MAX of its rows against the last build (a truncated
download must not replace a good file). --force skips that check."""
import json, math, os, re, sys, time
import osmium
import requests
import app

HERE = os.path.dirname(os.path.abspath(__file__))
PBF = os.path.join(HERE, "data", "germany-latest.osm.pbf")
POLY = os.path.join(HERE, "data", "germany.poly")
OUT = os.path.join(HERE, "data", "pois.jsonl")
META = os.path.join(HERE, "data", "pois.meta.json")
SOURCES = {PBF: "https://download.geofabrik.de/europe/germany-latest.osm.pbf",
           POLY: "https://download.geofabrik.de/europe/germany.poly"}
DROP_MAX = 0.10
KEEP = ("highway", "amenity", "toilets", "brand", "name", "description", "note", "takeaway", "railway",
        "shop", "opening_hours", "toilets:fee", "fee", "capacity", "operator", "indoor_seating")
EVID_SRC = ['nwr[shop=supermarket]["brand:wikidata"~"^(Q701755|Q16968817|Q685967|Q1541857)$"]{a};',
            "nwr[shop=furniture]{a};", "nwr[shop=doityourself]{a};",
            "nwr[amenity=charging_station][access!=private][access!=customers]{a};"]
EVID_SEL = ['nwr[amenity~"^(cafe|restaurant|fast_food)$"]{a};', "nwr[shop=bakery]{a};"]
EVID_M = max(app.EVID_RADIUS.values())  # 150 m

COND = re.compile(r'\["?([^"\]=!~]+)"?(=|!=|~)(?:"([^"]*)"|([^\]]*))\]')  # a quoted value may hold ]

def parseSel(sel):
    """'nwr[k=v][k!=v]["k"~"re"]{a};' -> [(key, op, value or compiled regex)]."""
    return [(k, op, re.compile(qv or uv) if op == "~" else (qv or uv)) for k, op, qv, uv in COND.findall(sel)]

def matches(conds, tags):
    """Overpass semantics: = needs the value, != also passes a missing key, ~ is a regex search."""
    for k, op, v in conds:
        t = tags.get(k)
        if op == "=" and t != v: return False
        if op == "!=" and t == v: return False
        if op == "~" and (t is None or not v.search(t)): return False
    return True

# The first condition of every selector is a plain key=value (or a value list for the evidence regex).
# Filtering on those in C keeps the 30 million roads and untagged nodes out of Python.
PREFILTER = [("highway", "rest_area"), ("highway", "services"), ("railway", "station")] + \
    [("amenity", v) for v in ("toilets", "fuel", "parking", "restaurant", "fast_food", "cafe", "charging_station")] + \
    [("shop", v) for v in ("supermarket", "furniture", "doityourself", "mall", "bakery")]

POI_SEL = [parseSel(x) for g in app.GROUPS for x in g]
SRC_SEL = [parseSel(x) for x in EVID_SRC]
EV_SEL = [parseSel(x) for x in EVID_SEL]

def download():
    for path, url in SOURCES.items():
        if os.path.exists(path):
            continue
        print(f"⬇️ downloading {url}", flush=True)
        with requests.get(url, headers=app.UA, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(path + ".part", "wb") as f:
                for block in r.iter_content(1 << 20):
                    f.write(block)
        os.replace(path + ".part", path)

def center(obj):
    """Middle of the bounding box of a node, way or area, like Overpass `out center`."""
    if isinstance(obj, osmium.osm.Node):
        return obj.location.lat, obj.location.lon
    if isinstance(obj, osmium.osm.Way):
        pts = [(n.lat, n.lon) for n in obj.nodes if n.location.valid()]
    else:
        pts = [(n.lat, n.lon) for ring in obj.outer_rings() for n in ring if n.location.valid()]
    if not pts:
        return None
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    return (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2

def scan():
    """One pass over the extract -> (stops, sources, evidence candidates) as compact rows."""
    stops, sources, cands = {}, [], {}
    fp = (osmium.FileProcessor(PBF).with_locations("flex_mem").with_areas()
          .with_filter(osmium.filter.TagFilter(*PREFILTER)))
    t0, seen = time.time(), 0
    for obj in fp:
        seen += 1
        if seen % 500000 == 0:
            print(f"  … {seen} tagged objects, {len(stops)} stops, {time.time() - t0:.0f} s", flush=True)
        if isinstance(obj, osmium.osm.Relation):
            continue  # its geometry arrives as an area
        if isinstance(obj, osmium.osm.Area):
            if obj.from_way():
                continue  # the way itself is handled
            otype, oid = "relation", obj.orig_id()
        else:
            otype, oid = ("node" if isinstance(obj, osmium.osm.Node) else "way"), obj.id
        tags = {t.k: t.v for t in obj.tags}
        poi = any(matches(c, tags) for c in POI_SEL)
        src = any(matches(c, tags) for c in SRC_SEL)
        ev = any(matches(c, tags) for c in EV_SEL)
        if not (poi or src or ev):
            continue
        c = center(obj)
        if c is None:
            continue
        row = {"type": otype, "id": oid, "lat": round(c[0], 7), "lon": round(c[1], 7),
               "tags": {k: v for k, v in tags.items() if k in KEEP}}
        if poi:
            stops[(otype, oid)] = row
        if src:
            sources.append(c)
        if ev and not poi:
            cands[(otype, oid)] = dict(row, ev=1)
    print(f"🔎 scan done: {seen} tagged objects, {len(stops)} stops, {len(sources)} evidence sources, "
          f"{len(cands)} cafes/bakeries/restaurants, {time.time() - t0:.0f} s", flush=True)
    return stops, sources, cands

def nearSources(cands, sources):
    """Only the evidence within EVID_M of a source survives (a grid, not 10^5 x 10^5 distances)."""
    cell = 0.01
    grid = {}
    for lat, lon in sources:
        grid.setdefault((math.floor(lat / cell), math.floor(lon / cell)), []).append((lat, lon))
    keep = {}
    for k, r in cands.items():
        i, j = math.floor(r["lat"] / cell), math.floor(r["lon"] / cell)
        if any(app.hav((r["lat"], r["lon"]), s) <= EVID_M
               for di in (-1, 0, 1) for dj in (-1, 0, 1) for s in grid.get((i + di, j + dj), ())):
            keep[k] = r
    return keep

def readPoly(path):
    """Geofabrik .poly -> [[(lon, lat), ...]] rings (outer rings and holes alike: even-odd rule)."""
    rings, ring = [], None
    with open(path, encoding="utf-8") as f:
        lines = [x.strip() for x in f][1:]
    for x in lines:
        if x == "END":
            if ring is not None:
                rings.append(ring); ring = None
            continue
        parts = x.split()
        if len(parts) == 2:
            ring.append((float(parts[0]), float(parts[1])))
        else:
            ring = []
    return rings

def inside(rings, lon, lat):
    hit = False
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                hit = not hit
    return hit

def coverCells(rings):
    """Grid cells (app.LOCAL_CELL) wholly inside the extract's border. A cell an edge passes through is
    left out, so a search box that reaches across the border goes to Overpass for the foreign side."""
    c = app.LOCAL_CELL
    border = set()
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            n = max(1, int(max(abs(x2 - x1), abs(y2 - y1)) / (c / 10)))
            for s in range(n + 1):
                x, y = x1 + (x2 - x1) * s / n, y1 + (y2 - y1) * s / n
                border.add((math.floor(y / c), math.floor(x / c)))
    lats = [p[1] for r in rings for p in r]
    lons = [p[0] for r in rings for p in r]
    cells = []
    for i in range(math.floor(min(lats) / c), math.floor(max(lats) / c) + 1):
        for j in range(math.floor(min(lons) / c), math.floor(max(lons) / c) + 1):
            if (i, j) not in border and inside(rings, (j + 0.5) * c, (i + 0.5) * c):
                cells.append([i, j])
    return cells

def kindCounts(rows):
    counts = {}
    for r in rows:
        k = "evidence" if r.get("ev") else app.classify(r["tags"])[0]
        counts[k] = counts.get(k, 0) + 1
    return counts

def writeOut(rows, cells):
    counts = kindCounts(rows)
    if os.path.exists(META) and "--force" not in sys.argv:
        with open(META, encoding="utf-8") as f:
            old = json.load(f).get("counts", {})
        lost = {k: (v, counts.get(k, 0)) for k, v in old.items() if counts.get(k, 0) < v * (1 - DROP_MAX)}
        if lost:
            print(f"🛑 refusing to write, kinds dropped more than {DROP_MAX:.0%} (--force to override):\n"
                  + "\n".join(f"{k}: {a} -> {b}" for k, (a, b) in lost.items()))
            sys.exit(1)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    with open(META, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"built": time.strftime("%Y-%m-%d"),
                   "source": time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(PBF))),
                   "rows": len(rows), "counts": dict(sorted(counts.items())), "cell": app.LOCAL_CELL,
                   "cells": cells}, f, separators=(",", ":"))
        f.write("\n")
    print(f"✅ wrote {len(rows)} rows, {os.path.getsize(OUT) / 1e6:.1f} MB, {len(cells)} covered cells\n"
          + "\n".join(f"{k}: {v}" for k, v in sorted(counts.items())))

def build():
    download()
    stops, sources, cands = scan()
    evid = nearSources(cands, sources)
    rows = {**evid, **stops}
    return [rows[k] for k in sorted(rows)]

def main():
    rows = build()
    writeOut(rows, coverCells(readPoly(POLY)))

if __name__ == "__main__":
    main()
