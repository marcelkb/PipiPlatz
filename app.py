"""PipiPlatz - nearest toilet stops (Rastplatz, Raststaette, McDonald's, BK, supermarkets) in Germany.
Free data only: OpenStreetMap (Overpass), OSRM routing, Nominatim geocoding."""
import math, time, json, sqlite3, hashlib, os
import requests
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

GROUPS = [
    ["nwr[highway=rest_area]{a};", "nwr[highway=services]{a};"],
    ["nwr[amenity=toilets][access!=private][access!=customers]{a};"],
    ["nwr[amenity=parking][toilets=yes]{a};", "nwr[amenity=fuel][toilets=yes]{a};",
     "nwr[amenity=restaurant][toilets=yes]{a};", "nwr[shop=supermarket][toilets=yes]{a};"],
    ['nwr[amenity=fast_food]["brand:wikidata"~"^(Q38076|Q177054)$"]{a};'],
    ['nwr[shop=supermarket]["brand:wikidata"~"^(Q701755|Q16968817|Q685967|Q1541857)$"]{a};'],
]

def run_groups(area, limit=300):
    a = f"({area})"
    from concurrent.futures import ThreadPoolExecutor
    def one(g):
        body = "".join(x.replace("{a}", a) for x in g)
        try:
            return overpass(f"[out:json][timeout:25];({body});out center tags {limit};")
        except RuntimeError:
            return None
    with ThreadPoolExecutor(len(GROUPS)) as ex:
        res = list(ex.map(one, GROUPS))
    if all(r is None for r in res):
        raise RuntimeError("Alle Overpass-Server nicht erreichbar")
    return [e for r in res if r for e in r], sum(r is None for r in res)

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
        return "burger", brand, wc or None
    if t.get("shop") == "supermarket":
        return "super", brand or "Supermarkt", wc or None
    if t.get("amenity") == "fuel":
        return "tank", brand or "Tankstelle", True
    if t.get("amenity") == "parking":
        return "rastplatz", t.get("name") or "Parkplatz mit WC", True
    return "other", brand or "Restaurant", wc

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.db")
TTL = 7 * 86400

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

def overpass_live(query):
    last = None
    for attempt in range(2):
        for url in OVERPASS:
            try:
                r = requests.post(url, data={"data": query}, headers=UA, timeout=30)
                if r.ok:
                    j = r.json()
                    remark = j.get("remark", "")
                    if "error" in remark or "timed out" in remark or "out of memory" in remark:
                        last = remark
                        continue
                    return j.get("elements", [])
                last = r.status_code
            except (requests.RequestException, ValueError) as e:
                last = e
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
    while radius <= 24000:  # widen until we have some results
        els, failed = run_groups(f"around:{radius},{lat},{lon}")
        pois = dedupe([p for p in map(to_poi, els) if p])
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
    from concurrent.futures import ThreadPoolExecutor
    CH = 26
    jobs = []
    for s in range(0, len(spts), CH - 1):
        chunk = spts[s:s + CH]
        if len(chunk) < 2: chunk = chunk * 2
        line = ",".join(f"{la:.5f},{lo:.5f}" for la, lo in chunk)
        for g in GROUPS:
            body = "".join(x.replace("{a}", f"(around:{radius},{line})") for x in g)
            jobs.append(f"[out:json][timeout:25];({body});out center tags 400;")
    def one(q):
        try:
            return overpass(q)
        except RuntimeError:
            return None
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, jobs))
    failed = sum(r is None for r in res)
    if failed == len(res):
        return jsonify(error="Overpass-Server nicht erreichbar, bitte später erneut"), 502
    pois = [p for r in res if r for p in map(to_poi, r) if p]
    pois = dedupe(pois)
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
