# validate_opposite.py - replay real drives over the /api/along pipeline and check the
# 150 m / 4000 m opposite-carriageway pair against OSRM, stop by stop.
#   python validate_opposite.py [port]     (needs a PipiPlatz instance on that port)
import os, sys, json, time, requests
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app

PORT = sys.argv[1] if len(sys.argv) > 1 else "5060"
BASE = f"http://127.0.0.1:{PORT}"
F = app.MWY_FACILITY  # raststaette, rastplatz, wc

# real corridors; each driven both ways where the pair matters (the flag is direction-dependent)
ROUTES = [
    ("A3 Frankfurt-Koeln",  [50.1109, 8.6821], [50.9375, 6.9603]),
    ("A3 Koeln-Frankfurt",  [50.9375, 6.9603], [50.1109, 8.6821]),
    ("A9 Muenchen-Nuernberg", [48.1351, 11.5820], [49.4520, 11.0768]),
    ("A9 Nuernberg-Muenchen", [49.4520, 11.0768], [48.1351, 11.5820]),
    ("A8 Karlsruhe-Stuttgart", [49.0069, 8.4037], [48.7758, 9.1829]),
    ("A5 Frankfurt-Freiburg", [50.1109, 8.6821], [47.9990, 7.8428]),
    ("A7 Hannover-Hamburg", [52.3759, 9.7320], [53.5511, 9.9937]),
]

pool = ThreadPoolExecutor(8)

def drive(name, a, b, via=None):
    r = requests.post(f"{BASE}/api/along", json={"from": a, "to": b, "via": via or [],
                      "alt": 0, "radius": 1000, "ahead_km": 150}, timeout=300)
    r.raise_for_status()
    j = r.json()
    if "error" in j: raise RuntimeError(j["error"])
    return j

def audit(j):
    """Recompute side and detour for every motorway-facility stop on the true route."""
    # same stops the app searched along (via points included); steps: segList needs them for side/road type
    route = app.osrmRoutes([j["from"], *(j.get("via") or []), j["to"]], steps=True)
    if not route: raise RuntimeError("OSRM gave no route")
    coords = [(c[1], c[0]) for c in route[0]["geometry"]["coordinates"]]
    segs, _ = app.segList(route[0])
    pois = [p for p in j["pois"] if p["kind"] in F and p.get("off", 9e9) <= 1200]
    rows = []
    for p in pois:  # first pass: the app's own numbers, recomputed on the full-res steps route
        d, along, left, mway = app.nearestSeg(segs, (p["lat"], p["lon"]))
        rows.append(dict(p=p, name=p["name"][:38], kind=p["kind"], off=p["off"], d=round(d),
                         left=left, mway=mway, along=p.get("along"), got=bool(p.get("opp"))))
    # reconstruct the OPPOSITE_CHECK cut exactly as app.py builds it: motorway stops only
    incut = {id(r["p"]) for r in sorted([r for r in rows if r["mway"]],
            key=lambda r: (r["kind"] == "wc", r["off"]))[:app.OPPOSITE_CHECK]}
    dts = {id(r["p"]): pool.submit(app.accessDetour, coords, (r["p"]["lat"], r["p"]["lon"])) for r in rows}
    problems, edge = [], []
    for r in rows:
        try:
            det = dts[id(r["p"])].result()
            r["det_failed"] = False
        except Exception:  # one OSRM error leaves this stop unverified, like det=None
            det = None
            r["det_failed"] = True
        r["det"] = round(det) if det else None
        r["incut"] = id(r["p"]) in incut
        if det is None or not r["mway"]:  # unverified or not on the motorway: the side test decides
            exp = r["mway"] and r["left"] and r["d"] <= app.OPPOSITE_M
        elif r["incut"]:  # verified: OSRM overrides the side test in both directions
            exp = det > app.OPPOSITE_DETOUR
        else:  # on the motorway but cut off: side test unless a neighbour's verdict transfers
            exp = r["mway"] and r["left"] and r["d"] <= app.OPPOSITE_M
        r["exp"] = exp
        if r["got"] != exp: problems.append(r)
        if (not r["got"]) and r["mway"] and r["left"] and r["d"] > app.OPPOSITE_M \
           and det is not None and det > app.OPPOSITE_DETOUR:
            edge.append(r)  # really across, not flagged, never OSRM-verified: the cap missed it
    rows = [{k: v for k, v in r.items() if k != "p"} for r in rows]
    twins = 0
    for p in [r for r in rows if r["got"]]:
        if any(abs((q["along"] or 0) - (p["along"] or 0)) < 2000 and not q["got"] and q["kind"] == p["kind"]
               for q in rows if q is not p): twins += 1
    return rows, problems, edge, twins

def saveEvidence(out):
    with open(os.environ.get("TEMP", "/tmp") + "/opposite_evidence.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

def main():
    out, allprob = [], []
    for name, a, b, *rest in ROUTES:  # an optional 4th element carries the via points
        via = rest[0] if rest else []
        t0 = time.time()
        try:
            j = drive(name, a, b, via)
        except Exception as e:
            print(f"{name}: FAILED to search ({e})"); continue
        j["from"], j["to"], j["via"] = a, b, via
        try:
            rows, problems, edge, twins = audit(j)
        except Exception as e:
            print(f"{name}: FAILED to audit ({e})"); continue
        flagged = [r for r in rows if r["got"]]
        allprob += [(name, r) for r in problems]
        print(f"\n== {name}  ({time.time()-t0:.0f} s, {len(j['pois'])} stops, "
              f"pending={j.get('pending')}, partial={j.get('partial')})")
        print(f"   motorway stops checked: {len(rows)}, flagged Gegenfahrbahn: {len(flagged)}, "
              f"twin-on-own-side for {twins}/{len(flagged)}")
        for r in flagged:
            print(f"   FLAG {r['name']:<38} off={r['off']:>5} d={r['d']:>4} det={r['det']} incut={r['incut']}")
        if problems:
            for r in problems:
                print(f"   MISMATCH flag={r['got']} expected={r['exp']}  {r['name']} d={r['d']} det={r['det']} incut={r['incut']}")
        else:
            print("   flag consistent with recomputed side+detour on every stop")
        if edge:
            for r in edge:
                print(f"   MISSED-BY-150M {r['name']} d={r['d']} det={r['det']}  <- really across, never checked")
        out.append({"route": name, "rows": rows, "problems": problems, "edge": edge,
                    "pending": j.get("pending"), "total_stops": len(j["pois"])})
        saveEvidence(out)  # after every route, so a crash later keeps what was measured
    print("\n==== VERDICT")
    print(f"routes: {len(out)}/{len(ROUTES)} audited, mismatches: {len(allprob)}, "
          f"missed-by-150m: {sum(len(o['edge']) for o in out)}")
    saveEvidence(out)
    print("evidence: %TEMP%/opposite_evidence.json")

if __name__ == "__main__":
    main()
