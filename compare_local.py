"""Check data/pois.jsonl against live Overpass: the same search box through both sources and the
same pipeline (to_poi, enrich, chargers), then the stop ids and WC states are compared.

Run after build_pois.py: python compare_local.py [--random N] [radius_m]
(--random N: N places spread over Germany, the same ones on every run, instead of the fixed 10;
 --motorway N: the Autobahn check at N points instead of the stops)
Live has no element cap here and shopEvidence no store cap, so both sides see everything; what
remains is OSM edits between the extract and now, and what the two methods compute differently."""
import math, sys, time
import app

PLACES = [("Frankfurt Hbf", 50.1069, 8.6625), ("Essen Mitte", 51.4556, 7.0116),
          ("Duisburg Hbf", 51.4297, 6.7757), ("Köln Dom", 50.9413, 6.9583),
          ("Kreuz Hilden", 51.1647, 6.9350), ("Berlin Mitte", 52.5200, 13.4050),
          ("München Zentrum", 48.1370, 11.5750), ("Hamburg Hbf", 53.5528, 10.0067),
          ("A 7 Kassel", 51.2700, 9.5300), ("A 2 Garbsen", 52.4200, 9.5800)]

def pipeline(els, evid):
    pois = app.dedupe([p for p in map(app.to_poi, els) if p])
    app.enrich(pois, evid)
    return {p["id"]: p for p in app.chargers(pois)}

def live(box):
    qs = app.allQueries(app.bboxOf(*box), 20000)
    els, evid, failed, _ = app.fetchAll(qs, app.WORKERS)
    if failed:
        raise RuntimeError(f"{failed} live queries failed")
    pois = app.dedupe([p for p in map(app.to_poi, els) if p])
    return els, evid + app.shopEvidence(pois, limit=500)

def compare(name, lat, lon, radius):
    """True / False = identical or not; None = near the border, where the app asks Overpass anyway."""
    box = ([(lat, lon)], radius)
    if app.localBox(*app.boxOf(*box)) is None:
        print(f"🌍 {name}: reaches across the border, the app asks Overpass here (not compared)")
        return None
    t = time.time()
    a = pipeline(*live(box))
    tl = time.time() - t
    t = time.time()
    b = pipeline(*app.localBox(*app.boxOf(*box)))
    tb = time.time() - t
    s, w, n, e = app.boxOf(*box)
    # Overpass returns a way when any of its nodes is in the box; the local lookup goes by its centre.
    # A way whose centre lies outside the box is that edge effect, not missing data.
    edge = sorted(k for k in a.keys() - b.keys() if not (s <= a[k]["lat"] <= n and w <= a[k]["lon"] <= e))
    for k in edge:
        print(f"   edge (centre outside the box, not counted): {k} {a[k]['kind']} {a[k]['name']!r}")
        a.pop(k)
    onlyLive, onlyLocal = sorted(a.keys() - b.keys()), sorted(b.keys() - a.keys())
    wc = sorted(k for k in a.keys() & b.keys() if (a[k]["wc"], a[k]["hint"] if "hint" in a[k] else "")
                != (b[k]["wc"], b[k]["hint"] if "hint" in b[k] else ""))
    same = not (onlyLive or onlyLocal or wc)
    print(f"{'✅' if same else '❌'} {name}: live {len(a)} stops ({tl:.1f} s), local {len(b)} ({tb * 1000:.0f} ms)", flush=True)
    for label, ids, src in (("only live", onlyLive, a), ("only local", onlyLocal, b)):
        for k in ids:
            print(f"   {label}: {k} {src[k]['kind']} {src[k]['name']!r} {src[k]['sub']!r}")
    for k in wc:
        print(f"   wc differs: {k} {a[k]['name']!r} live={a[k]['wc']}/{a[k].get('hint', '')!r} "
              f"local={b[k]['wc']}/{b[k].get('hint', '')!r}")
    return same

def randomPlaces(n, seed=1):
    """n places, the same ones on every run: 60% at a random stop of the file (so cities and the
    Autobahn get their real share), 40% anywhere in the covered cells (the countryside)."""
    import random
    rnd = random.Random(seed)
    cover, grid = app.localData()
    stops = [(la, lo) for rows in grid.values() for la, lo, line in rows if '"ev":1' not in line]
    stops.sort()
    cells = sorted(cover)
    out = []
    for k in range(n):
        if k % 5 < 3:
            la, lo = rnd.choice(stops)
        else:
            i, j = rnd.choice(cells)
            la, lo = (i + rnd.random()) * app.LOCAL_CELL, (j + rnd.random()) * app.LOCAL_CELL
        out.append((f"#{k + 1} {la:.4f},{lo:.4f}", round(la, 4), round(lo, 4)))
    return out

def motorwayCheck(n, seed=1):
    """app.onMotorway from the local lines against the live Overpass query, at n points the same on
    every run: 40% up to 20 m from an Autobahn line, 30% 20-45 m (around the 30 m limit), 30% 45-300 m."""
    import json, random
    rnd = random.Random(seed)
    with open(app.MWY_FILE, encoding="utf-8") as f:
        ways = [json.loads(x)["pts"] for x in f]
    same = skipped = 0
    for k in range(n):
        pts = rnd.choice(ways)
        i = rnd.randrange(len(pts) - 1)
        (a, b), (c, d), t = pts[i], pts[i + 1], rnd.random()
        lat, lon = a + (c - a) * t, b + (d - b) * t
        off = rnd.uniform(*((0, 20), (20, 45), (45, 300))[0 if k % 10 < 4 else 1 if k % 10 < 7 else 2])
        ang = rnd.uniform(0, 2 * math.pi)
        lat = round(lat + off * math.sin(ang) / 111320, 4)  # the live query rounds to 4 decimals too
        lon = round(lon + off * math.cos(ang) / (111320 * math.cos(math.radians(lat))), 4)
        local = app.localOnMotorway(lat, lon, app.MWY_CHECK_M)
        if local is None:
            print(f"⏭️ #{k + 1} {lat},{lon}: outside the local data")
            skipped += 1
            continue
        q = (f'[out:json][timeout:10];way(around:{app.MWY_CHECK_M},{lat:.4f},{lon:.4f})'
             f'[highway~"^(motorway|motorway_link)$"];out ids 1;')
        try:
            live = bool(app.overpass(q))
        except RuntimeError as e:
            print(f"⏭️ #{k + 1} {lat},{lon}: skipped, {e}")
            skipped += 1
            continue
        same += live == local
        if live != local:
            print(f"❌ #{k + 1} {lat},{lon} ({off:.0f} m from the line it was placed by): live={live} local={local}")
    print(f"\nAutobahn check: {same}/{n - skipped} points agree, {skipped} skipped")

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--motorway" in argv:
        motorwayCheck(int(argv[argv.index("--motorway") + 1]))
        sys.exit()
    places = PLACES
    if "--random" in argv:
        i = argv.index("--random")
        places = randomPlaces(int(argv[i + 1]))
        del argv[i:i + 2]
    radius = int(argv[0]) if argv else 3000
    ok = border = skipped = 0
    for n, la, lo in places:
        try:
            r = compare(n, la, lo, radius)
        except RuntimeError as e:  # Overpass failed: no verdict for this place
            print(f"⏭️ {n}: skipped, {e}")
            skipped += 1
            continue
        if r is None:
            border += 1
        else:
            ok += r
    print(f"\n{ok}/{len(places) - border - skipped} places identical, {border} at the border (live in the app), "
          f"{skipped} skipped (Overpass failed)", flush=True)
