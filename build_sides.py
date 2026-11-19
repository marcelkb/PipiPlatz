"""Which carriageway each rest area / WC near the Autobahn is reachable from, worked out once with OSRM.

The route search hides a stop that is only reachable from the other carriageway (app.accessDetour:
drive through the stop instead of straight on; >4 km extra = across the median). That costs one OSRM
call per stop and search. This script asks OSRM once per stop and carriageway direction and stores
the answer in data/sides.jsonl, together with the stretch it was measured on (A 1.5 km before the
stop, B 1.5 km after it, along that carriageway). The app uses a stored answer only when the route
drives that same stretch, otherwise it asks OSRM as before.

The carriageways come from the Geofabrik extract (data/germany-latest.osm.pbf, see build_pois.py):
highway=motorway ways chained by their shared nodes, so the walk to A and B follows the real road
through junctions. Run after build_pois.py:

    python -u build_sides.py            # first run ~4 h (13k OSRM calls, 1 per second), resumable
    python -u build_sides.py --ids f    # only the stop ids listed in file f (one per line)

A later run only asks for stops that are new or moved; the rows of the others are kept."""
import json, math, os, pickle, sys, time
import app
import build_pois

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "sides.jsonl")
GRAPH_CACHE = os.path.join(HERE, "data", "motorway-graph.cache")  # git-ignored, rebuilt when the .pbf changes
BACK_M = 1500  # m before and after the stop, the same stretch app.accessDetour measures on
NEAR_M = 1200  # m: the route search checks stops up to this far off the route
CELL = 0.01  # degrees: segment grid for the nearest-carriageway lookup
PAUSE = 1.0  # s between OSRM calls: the public server allows one request per second

def bearing(a, b):
    return math.degrees(math.atan2((b[1] - a[1]) * math.cos(math.radians(a[0])), b[0] - a[0])) % 360

def turn(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)

def readGraph():
    """{way id: (node ids, points)} of every highway=motorway way, in driving direction."""
    if os.path.exists(GRAPH_CACHE) and os.path.getmtime(GRAPH_CACHE) >= os.path.getmtime(build_pois.PBF):
        with open(GRAPH_CACHE, "rb") as f:
            return pickle.load(f)
    import osmium
    ways = {}
    t = time.time()
    fp = (osmium.FileProcessor(build_pois.PBF, osmium.osm.NODE | osmium.osm.WAY).with_locations("flex_mem")
          .with_filter(osmium.filter.TagFilter(("highway", "motorway"))))
    for obj in fp:
        if not isinstance(obj, osmium.osm.Way):
            continue
        nodes = [n for n in obj.nodes if n.location.valid()]
        if len(nodes) < 2:
            continue
        ids, pts = [n.ref for n in nodes], [(n.lat, n.lon) for n in nodes]
        if obj.tags.get("oneway") == "-1":
            ids.reverse(); pts.reverse()
        ways[obj.id] = (ids, pts)
    print(f"🛣️ {len(ways)} motorway ways read in {time.time() - t:.0f} s", flush=True)
    with open(GRAPH_CACHE, "wb") as f:
        pickle.dump(ways, f)
    return ways

class Carriageways:
    def __init__(self, ways):
        self.ways = ways
        self.starts, self.ends, self.grid = {}, {}, {}
        for wid, (ids, pts) in ways.items():
            self.starts.setdefault(ids[0], []).append(wid)
            self.ends.setdefault(ids[-1], []).append(wid)
            for k, ((a, b), (c, d)) in enumerate(zip(pts, pts[1:])):
                for i in range(math.floor(min(a, c) / CELL), math.floor(max(a, c) / CELL) + 1):
                    for j in range(math.floor(min(b, d) / CELL), math.floor(max(b, d) / CELL) + 1):
                        self.grid.setdefault((i, j), []).append((wid, k))

    def nearest(self, pt, within):
        """[(distance m, way, segment index, t along it, bearing)] of the segments within `within`."""
        lat, lon = pt
        kx, ky = 111320 * math.cos(math.radians(lat)), 111320
        s, w, n, e = app.boxOf([pt], within)
        seen, out = set(), []
        for i in range(math.floor(s / CELL), math.floor(n / CELL) + 1):
            for j in range(math.floor(w / CELL), math.floor(e / CELL) + 1):
                for wid, k in self.grid.get((i, j), ()):
                    if (wid, k) in seen:
                        continue
                    seen.add((wid, k))
                    (a, b), (c, d) = self.ways[wid][1][k], self.ways[wid][1][k + 1]
                    ax, ay = (b - lon) * kx, (a - lat) * ky
                    dx, dy = (d - lon) * kx - ax, (c - lat) * ky - ay
                    L2 = dx * dx + dy * dy
                    if not L2:
                        continue
                    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
                    dist = math.hypot(ax + t * dx, ay + t * dy)
                    if dist <= within:
                        out.append((dist, wid, k, t, bearing((a, b), (c, d))))
        return sorted(out)

    def walk(self, wid, k, t, metres, forward):
        """The point `metres` along the carriageway from (way, segment, t), following the next way at
        each end (the one that turns least). Returns (point, metres actually walked)."""
        pts = self.ways[wid][1]
        a, b = pts[k], pts[k + 1]
        here = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        done, hops = 0.0, 0
        nxt = k + 1 if forward else k  # index of the next point to walk to
        while True:
            pts = self.ways[wid][1]
            while 0 <= nxt < len(pts):
                d = app.hav(here, pts[nxt])
                if done + d >= metres:
                    f = (metres - done) / d if d else 0
                    return (here[0] + (pts[nxt][0] - here[0]) * f, here[1] + (pts[nxt][1] - here[1]) * f), metres
                done += d
                here = pts[nxt]
                nxt += 1 if forward else -1
            hops += 1
            ids = self.ways[wid][0]
            heading = bearing(pts[-2], pts[-1]) if forward else bearing(pts[0], pts[1])
            options = self.starts.get(ids[-1], []) if forward else self.ends.get(ids[0], [])
            options = [o for o in options if o != wid]
            if not options or hops > 50:
                return here, done  # the motorway ends here
            def bend(o):
                p = self.ways[o][1]
                return turn(heading, bearing(p[0], p[1]) if forward else bearing(p[-2], p[-1]))
            wid = min(options, key=bend)
            nxt = 1 if forward else len(self.ways[wid][1]) - 2

def stops():
    """The stops the route search may check: rest areas, Raststaetten, WCs within NEAR_M of a motorway."""
    out = []
    with open(app.LOCAL_FILE, encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if e.get("ev"):
                continue
            p = app.to_poi(e)
            if p and p["kind"] in app.MWY_FACILITY:
                out.append(p)
    return out

def directions(cw, pt):
    """The nearest carriageway and the nearest one running the other way: [(way, k, t)]."""
    segs = cw.nearest(pt, NEAR_M)
    if not segs:
        return []
    first = segs[0]
    out = [first]
    opp = next((s for s in segs if turn(s[4], first[4]) > 135), None)
    if opp:
        out.append(opp)
    return [(s[1], s[2], s[3]) for s in out]

def measure(cw, pt, wid, k, t):
    """[A lat, A lon, B lat, B lon, metres A->B along the carriageway, detour] or None if OSRM failed.
    [] where the motorway ends within BACK_M: a route coming from further back may leave there and
    reach the stop cheaply, which only the live check over the route itself sees."""
    A, da = cw.walk(wid, k, t, BACK_M, False)
    B, db = cw.walk(wid, k, t, BACK_M, True)
    if min(da, db) < BACK_M:
        return []
    try:
        rs = app.osrmRoutes([A, pt, B])
    except Exception as e:  # noqa: BLE001 - a failed call is asked again on the next run
        print(f"   ⚠️ OSRM failed: {e}", flush=True)
        return None
    if not rs:
        return None
    direct = da + db
    return [round(A[0], 6), round(A[1], 6), round(B[0], 6), round(B[1], 6), round(direct), round(rs[0]["distance"] - direct)]

def key(p):
    return p["id"], round(p["lat"], 5), round(p["lon"], 5)

def main():
    argv = sys.argv[1:]
    only = None
    if "--ids" in argv:
        with open(argv[argv.index("--ids") + 1], encoding="utf-8") as f:
            only = {x.strip() for x in f if x.strip()}
    done = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                done[(r["id"], r["at"][0], r["at"][1])] = line
    cw = Carriageways(readGraph())
    todo = [p for p in stops() if (only is None or p["id"] in only)]
    wanted = {key(p) for p in todo}
    ask = [p for p in todo if key(p) not in done]
    print(f"📍 {len(todo)} stops, {len(todo) - len(ask)} already stored, {len(ask)} to ask", flush=True)
    last = 0.0
    plan = [(p, directions(cw, (p["lat"], p["lon"]))) for p in ask]
    calls = sum(len(ways) for _, ways in plan)  # one OSRM call per direction; stops far off a motorway need none
    with open(OUT, "a", encoding="utf-8", newline="\n") as f:
        for n, (p, ways) in enumerate(plan):
            pt = (p["lat"], p["lon"])
            dirs, failed = [], False
            calls -= len(ways)
            for wid, k, t in ways:
                time.sleep(max(0.0, PAUSE - (time.time() - last)))
                last = time.time()
                m = measure(cw, pt, wid, k, t)
                if m is None:
                    failed = True
                    break
                if m:
                    dirs.append(m)
            if failed:
                continue  # not written: asked again next run
            _, la, lo = key(p)
            line = json.dumps({"id": p["id"], "at": [la, lo], "dirs": dirs}, separators=(",", ":")) + "\n"
            f.write(line)
            f.flush()
            done[key(p)] = line
            if n % 100 == 0:
                left = calls * PAUSE / 60
                print(f"   {n}/{len(ask)} stops, ~{left:.0f} min left", flush=True)
    if only is None:
        # drop rows of stops that are gone or moved, sorted by id so a rebuild diffs cleanly
        keep = sorted((v for k, v in done.items() if k in wanted), key=lambda x: json.loads(x)["id"])
        with open(OUT + ".part", "w", encoding="utf-8", newline="\n") as f:
            f.writelines(keep)
        os.replace(OUT + ".part", OUT)
        print(f"✅ {len(keep)} stops in {os.path.relpath(OUT, HERE)}", flush=True)

if __name__ == "__main__":
    main()
