"""PipiPlatz - nearest toilet stops (Rastplatz, Raststaette, McDonald's, BK, supermarkets) in Germany.
Free data only: OpenStreetMap (Overpass), OSRM routing, Nominatim geocoding."""
import math, time, json, sqlite3, hashlib, os, re
import requests
try:  # use the OS certificate store: Python's bundled CAs fail behind TLS-inspecting antivirus
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="")
UA = {"User-Agent": "PipiPlatz/1.0 (personal use)"}
OVERPASS = ["https://ovp.dresel.systems/api/interpreter",
            "https://overpass.openstreetmap.fr/api/interpreter",
            "https://overpass-api.de/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
OSRM = "https://router.project-osrm.org/route/v1/driving/"
NOMINATIM = "https://nominatim.openstreetmap.org/search"

LIKELY_SUPER = "Edeka|EDEKA|Rewe|REWE|Kaufland|Globus|Marktkauf|Famila|HIT|real|E center|E-Center"
BURGER = "McDonald's|Burger King"
FURNITURE = ("Ostermann|IKEA|XXXL|Höffner|Segmüller|Porta|Möbel Kraft|Rieger|Mömax|Roller|"
             "Sconto|Hardeck|Biller|Zurbrüggen|Mahler")
DIY = "^OBI( |$)|Bauhaus|Hornbach|^toom|hagebau|Hellweg|Globus Bau|Bauvit"
# A name~ regex in the Overpass query takes 15-30 s on the public servers, the same query without it
# under 1 s. So all furniture / DIY shops are fetched and the chain names are matched here.
CHAIN = {"moebel": re.compile(FURNITURE, re.I), "bau": re.compile(DIY, re.I)}

GROUPS = [
    ["nwr[highway=rest_area]{a};", "nwr[highway=services]{a};"],
    ["nwr[amenity=toilets][access!=private][access!=customers]{a};"],
    ["nwr[amenity=parking][toilets=yes]{a};", "nwr[amenity=fuel][toilets=yes]{a};",
     "nwr[amenity=restaurant][toilets=yes]{a};", "nwr[shop=supermarket][toilets=yes]{a};"],
    ['nwr[amenity=fast_food]["brand:wikidata"~"^(Q38076|Q177054)$"]{a};'],
    ['nwr[shop=supermarket]["brand:wikidata"~"^(Q701755|Q16968817|Q685967|Q1541857)$"]{a};'],
    ["nwr[shop=furniture]{a};", "nwr[shop=doityourself]{a};"],
    ["nwr[shop=mall]{a};"],
    ["nwr[railway=station][toilets=yes]{a};",
     'nwr[railway=station]["railway:station_category"~"^[1-3]$"]{a};'],
]

# WC evidence: shops get a WC mostly when a cafe / bakery with seats is next to them.
# (source selector, selectors searched around the source, radius in m)
EVIDENCE = [
    ('nwr[shop=supermarket]["brand:wikidata"~"^(Q701755|Q16968817|Q685967|Q1541857)$"]{a}',
     ["nwr[shop=bakery]", "nwr[amenity=cafe]"], 80),
]
EVID_RADIUS = {"super": 80, "moebel": 150, "bau": 150}

WORKERS = 5  # public Overpass servers handle only a few requests per client at once

def allQueries(a, limit):
    """[(is_evidence, query)] for one search area `a` (an Overpass area filter like '(around:...)')."""
    qs = []
    for g in GROUPS:
        body = "".join(x.replace("{a}", a) for x in g)
        qs.append((False, f"[out:json][timeout:25];({body});out center tags {limit};"))
    for src, targets, r in EVIDENCE:
        sets = "".join(f"{t}(around.s:{r});" for t in targets)
        qs.append((True, f"[out:json][timeout:25];{src.replace('{a}', a)}->.s;({sets});out center tags {limit};"))
    return qs

def fetchAll(queries, workers):
    """Run queries in parallel -> (poi elements, evidence elements, number of failed queries)."""
    from concurrent.futures import ThreadPoolExecutor
    def one(q):
        try:
            return overpass(q[1])
        except RuntimeError:
            return None
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(one, queries))
    if all(r is None for r in res):
        raise RuntimeError("Alle Overpass-Server nicht erreichbar")
    pois = [e for (ev, _), r in zip(queries, res) if r and not ev for e in r]
    evid = [e for (ev, _), r in zip(queries, res) if r and ev for e in r]
    return pois, evid, sum(r is None for r in res)

def run_groups(area, limit=300):
    return fetchAll(allQueries(f"({area})", limit), WORKERS)

def shopEvidence(pois, limit=25):
    """Cafes / restaurants within 150 m of each furniture / DIY chain store: one small query, cached."""
    stores = [p for p in pois if p["kind"] in ("moebel", "bau") and p["wc"] is not True][:limit]
    if not stores:
        return []
    r = EVID_RADIUS["moebel"]
    body = "".join(f'nwr[amenity~"^(cafe|restaurant|fast_food)$"](around:{r},{p["lat"]:.5f},{p["lon"]:.5f});'
                   for p in stores)
    try:
        return overpass(f"[out:json][timeout:25];({body});out center tags 300;")
    except RuntimeError:
        return []

def evidenceOf(e):
    """cafe / bakery element -> (lat, lon, label, strong) or None. strong = has seats, so likely a WC."""
    t = e.get("tags", {})
    lat = e.get("lat") or e.get("center", {}).get("lat")
    lon = e.get("lon") or e.get("center", {}).get("lon")
    if lat is None: return None
    if t.get("amenity") == "cafe":
        return lat, lon, "Café", True
    if t.get("amenity") in ("restaurant", "fast_food"):
        return lat, lon, "Restaurant", True
    if t.get("shop") == "bakery":
        if t.get("indoor_seating") == "yes":
            return lat, lon, "Bäcker mit Sitzplätzen", True
        return lat, lon, "Bäcker", False
    return None

def enrich(pois, evid):
    """Set wc / hint on shops without a confirmed WC from cafes and bakeries next to them."""
    ev = [x for x in map(evidenceOf, evid) if x]
    for p in pois:
        r = EVID_RADIUS.get(p["kind"])
        if not r or p["wc"] is True: continue
        near = [x for x in ev if hav((p["lat"], p["lon"]), (x[0], x[1])) <= r]
        strong = [x for x in near if x[3]]
        if strong:
            p["wc"], p["hint"] = "likely", f"{strong[0][2]} beim Markt"
        elif p["kind"] == "moebel":
            p["wc"], p["hint"] = "likely", "großes Möbelhaus"
        elif near:
            p["wc"], p["hint"] = None, "nur Bäcker-Theke beim Markt"
        else:
            p["wc"], p["hint"] = None, "kein Café/Bäcker erfasst"

def classify(t):
    brand = t.get("brand") or t.get("name") or ""
    wc = t.get("toilets") == "yes" or t.get("amenity") == "toilets"
    if t.get("highway") == "services":
        return "raststaette", "Raststätte", True
    if t.get("highway") == "rest_area":
        return "rastplatz", "Rastplatz", t.get("toilets") != "no"
    if t.get("amenity") == "toilets":
        return "wc", t.get("name") or "Öffentliches WC", True
    if "McDonald" in brand or "Burger King" in brand:
        # Restaurants have a customer WC; only pop-up / takeaway-only stores usually don't
        txt = " ".join(t.get(k, "") for k in ("name", "description", "note"))
        if t.get("takeaway") == "only" or re.search(r"pop.?up|nur drive|drive.?only", txt, re.I):
            return "burger", brand, wc or None
        return "burger", brand, True
    if t.get("railway") == "station":
        # confirmed WC, else only big DB stations (category 1-3) are assumed to have one
        return "bahnhof", t.get("name") or "Bahnhof", True if t.get("toilets") == "yes" else "likely"
    if t.get("shop") == "mall":
        return "mall", t.get("name") or "Einkaufszentrum", True
    if t.get("shop") == "furniture":
        return "moebel", t.get("name") or "Möbelhaus", wc or None
    if t.get("shop") == "doityourself":
        return "bau", t.get("name") or "Baumarkt", wc or None
    if t.get("shop") == "supermarket":
        return "super", brand or "Supermarkt", wc or None
    if t.get("amenity") == "fuel":
        return "tank", brand or "Tankstelle", True
    if t.get("amenity") == "parking":
        return "rastplatz", t.get("name") or "Parkplatz mit WC", True
    return "other", brand or "Restaurant", wc

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.db")
TTL = 7 * 86400
SNAP = 0.02  # degrees, ~2 km north-south

def _db():
    c = sqlite3.connect(DB)
    c.execute("create table if not exists c(k text primary key, t real, v text)")
    return c

def overpass(query):
    k = hashlib.sha1(query.encode()).hexdigest()
    with _db() as c:
        row = c.execute("select t,v from c where k=?", (k,)).fetchone()
    if row and time.time() - row[0] < TTL:
        return json.loads(row[1])
    try:
        data = overpass_live(query)
    except RuntimeError:
        if row:
            return json.loads(row[1])
        raise
    with _db() as c:
        c.execute("insert or replace into c values(?,?,?)", (k, time.time(), json.dumps(data)))
    return data

BAD = {}  # url -> time of last failure; recently failed hosts are tried last
BAD_FOR = 300
HEDGE = 10  # seconds before the next server is tried in parallel with a slow one

def hostOrder():
    now = time.time()
    return sorted(OVERPASS, key=lambda u: now - BAD.get(u, 0) < BAD_FOR)

def tryHost(url, query):
    """One Overpass request -> elements, or raises RuntimeError and marks the host as bad."""
    try:
        r = requests.post(url, data={"data": query}, headers=UA, timeout=(5, 30))
        if not r.ok:
            raise RuntimeError(r.status_code)
        j = r.json()
        remark = j.get("remark", "")
        if "error" in remark or "timed out" in remark or "out of memory" in remark:
            raise RuntimeError(remark)
        BAD.pop(url, None)
        return j.get("elements", [])
    except (requests.RequestException, ValueError, RuntimeError) as e:
        BAD[url] = time.time()
        raise RuntimeError(e)

def overpass_live(query):
    """Hedged: the next server starts after HEDGE s (or at once on failure); first valid answer wins."""
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    last = None
    for attempt in range(2):
        hosts = hostOrder()
        ex = ThreadPoolExecutor(len(hosts))
        pending, i = set(), 0
        try:
            while True:
                if i < len(hosts):
                    pending.add(ex.submit(tryHost, hosts[i], query))
                    i += 1
                done, pending = wait(pending, timeout=HEDGE if i < len(hosts) else None,
                                     return_when=FIRST_COMPLETED)
                for f in done:
                    try:
                        return f.result()
                    except RuntimeError as e:
                        last = e
                if i >= len(hosts) and not pending:
                    break
        finally:
            ex.shutdown(wait=False)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Overpass failed: {last}")

def hav(a, b):
    R = 6371000
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1]); dp = p2 - p1
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(h))

def to_poi(e):
    t = e.get("tags", {})
    lat = e.get("lat") or e.get("center", {}).get("lat")
    lon = e.get("lon") or e.get("center", {}).get("lon")
    if lat is None: return None
    kind, name, wc = classify(t)
    if kind in CHAIN and t.get("toilets") != "yes" and not CHAIN[kind].search(t.get("name", "")):
        return None  # only the big chains, or shops that say they have a WC
    if t.get("toilets") == "no" and kind in ("burger", "super", "moebel", "bau", "mall", "bahnhof"):
        return None  # OSM says explicitly: no WC
    return {"id": f'{e["type"]}{e["id"]}', "lat": lat, "lon": lon, "kind": kind,
            "name": name, "wc": wc, "hours": t.get("opening_hours", ""),
            "fee": t.get("toilets:fee") or t.get("fee") or ""}

def dedupe(pois):
    seen, out = set(), []
    for p in pois:
        k = (p["kind"], round(p["lat"], 4), round(p["lon"], 4))
        if k not in seen:
            seen.add(k); out.append(p)
    return out

@app.errorhandler(Exception)
def err(e):
    return jsonify(error=str(e)[:200]), 502

@app.get("/")
def index():
    return send_from_directory("static", "index.html")

@app.get("/api/nearby")
def nearby():
    lat, lon = float(request.args["lat"]), float(request.args["lon"])
    radius = 3000
    pois = []
    # Query around a ~2 km grid point instead of the exact position, so repeat searches in the
    # same area hit the Overpass cache. The radius grows by the snap offset, nothing is lost.
    glat, glon = round(lat / SNAP) * SNAP, round(lon / SNAP) * SNAP
    pad = math.ceil(hav((lat, lon), (glat, glon)) / 100) * 100
    while radius <= 24000:  # widen until we have some results
        els, evid, failed = run_groups(f"around:{radius + pad},{glat:.2f},{glon:.2f}")
        pois = dedupe([p for p in map(to_poi, els) if p])
        enrich(pois, evid + shopEvidence(pois))
        if len(pois) >= 8: break
        radius *= 2
    for p in pois:
        p["dist"] = round(hav((lat, lon), (p["lat"], p["lon"])))
    pois.sort(key=lambda p: p["dist"])
    return jsonify(pois=pois[:80], radius=radius, partial=failed)

@app.get("/api/geocode")
def geocode():
    r = requests.get(NOMINATIM, params={"q": request.args["q"], "format": "json",
                     "countrycodes": "de,at,nl,be,lu,fr,ch,cz,pl,dk", "limit": 5},
                     headers=UA, timeout=20)
    return jsonify([{"name": x["display_name"], "lat": float(x["lat"]), "lon": float(x["lon"])} for x in r.json()])

def resample(coords, step=1500):
    """coords: [(lat,lon)] -> points every ~step metres plus cumulative distance."""
    out, cum, acc, total = [coords[0]], [0.0], 0.0, 0.0
    for a, b in zip(coords, coords[1:]):
        d = hav(a, b); acc += d; total += d
        if acc >= step:
            out.append(b); cum.append(total); acc = 0
    return out, cum, total

@app.post("/api/along")
def along():
    j = request.get_json()
    o, d = j["from"], j["to"]
    radius = int(j.get("radius", 2000))
    ahead_km = float(j.get("ahead_km", 150))
    url = f"{OSRM}{o[1]},{o[0]};{d[1]},{d[0]}?overview=full&geometries=geojson"
    r = requests.get(url, headers=UA, timeout=30).json()
    if not r.get("routes"):
        return jsonify(error="Keine Route gefunden"), 400
    route = r["routes"][0]
    coords = [(c[1], c[0]) for c in route["geometry"]["coordinates"]]
    pts, cum, total = resample(coords)
    keep = [i for i, c in enumerate(cum) if c <= ahead_km * 1000] or [0]
    spts, scum = [pts[i] for i in keep], [cum[i] for i in keep]
    # Overpass: chunk the polyline so queries stay small
    CH = 26
    jobs = []
    for s in range(0, len(spts), CH - 1):
        chunk = spts[s:s + CH]
        if len(chunk) < 2: chunk = chunk * 2
        line = ",".join(f"{la:.5f},{lo:.5f}" for la, lo in chunk)
        jobs += allQueries(f"(around:{radius},{line})", 400)
    try:
        els, evid, failed = fetchAll(jobs, WORKERS)
    except RuntimeError:
        return jsonify(error="Overpass-Server nicht erreichbar, bitte später erneut"), 502
    pois = dedupe([p for p in map(to_poi, els) if p])
    enrich(pois, evid + shopEvidence(pois))
    for p in pois:
        best = min(range(len(spts)), key=lambda i: hav(spts[i], (p["lat"], p["lon"])))
        p["off"] = round(hav(spts[best], (p["lat"], p["lon"])))
        p["along"] = round(scum[best])
    pois.sort(key=lambda p: p["along"])
    return jsonify(route=coords[::max(1, len(coords)//1500)], total=round(total),
                   duration=round(route["duration"]), pois=pois, partial=failed)

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
