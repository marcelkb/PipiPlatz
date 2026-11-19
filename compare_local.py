"""Check data/pois.jsonl against live Overpass: the same search box through both sources and the
same pipeline (to_poi, enrich, chargers), then the stop ids and WC states are compared.

Run after build_pois.py: python compare_local.py [radius_m]
Live has no element cap here and shopEvidence no store cap, so both sides see everything; what
remains is OSM edits between the extract and now, and what the two methods compute differently."""
import sys, time
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
    box = ([(lat, lon)], radius)
    t = time.time()
    a = pipeline(*live(box))
    tl = time.time() - t
    t = time.time()
    hit = app.localBox(*app.boxOf(*box))
    if hit is None:
        print(f"⚠️ {name}: not covered by the local data")
        return False
    b = pipeline(*hit)
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
    print(f"{'✅' if same else '❌'} {name}: live {len(a)} stops ({tl:.1f} s), local {len(b)} ({tb * 1000:.0f} ms)")
    for label, ids, src in (("only live", onlyLive, a), ("only local", onlyLocal, b)):
        for k in ids:
            print(f"   {label}: {k} {src[k]['kind']} {src[k]['name']!r} {src[k]['sub']!r}")
    for k in wc:
        print(f"   wc differs: {k} {a[k]['name']!r} live={a[k]['wc']}/{a[k].get('hint', '')!r} "
              f"local={b[k]['wc']}/{b[k].get('hint', '')!r}")
    return same

if __name__ == "__main__":
    radius = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    ok = sum(compare(n, la, lo, radius) for n, la, lo in PLACES)
    print(f"\n{ok}/{len(PLACES)} places identical")
