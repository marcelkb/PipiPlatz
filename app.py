"""PipiPlatz - nearest toilet stops (Rastplatz, Raststaette, McDonald's, BK, supermarkets) in Germany.
Free data only: OpenStreetMap (Overpass), OSRM routing, Nominatim geocoding."""
import math, time, json, sqlite3, hashlib, os, re
from urllib.parse import urlparse, parse_qs, unquote, unquote_plus, urljoin
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
            "https://lz4.overpass-api.de/api/interpreter",
            "https://z.overpass-api.de/api/interpreter",
            "https://overpass.openstreetmap.ru/api/interpreter",
            "https://overpass.osm.jp/api/interpreter",
            "https://overpass.nchc.org.tw/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
# Only add servers that cover the whole world: a regional one (e.g. overpass.osm.ch, Switzerland only)
# would answer 200 with an empty result for Germany and be taken as valid.
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
ALONG_CUT = 35  # s a route search waits before answering with what is ready; the rest finishes in the background

def allQueries(a, limit, merge=False):
    """[(is_evidence, query)] for one search area `a` (an Overpass area filter like '(around:...)').
    merge=True puts all POI groups into one query: fewer requests for long route searches."""
    qs = []
    groups = [[x for g in GROUPS for x in g]] if merge else GROUPS
    for g in groups:
        body = "".join(x.replace("{a}", a) for x in g)
        qs.append((False, f"[out:json][timeout:25];({body});out center tags {limit};"))
    for src, targets, r in EVIDENCE:
        sets = "".join(f"{t}(around.s:{r});" for t in targets)
        qs.append((True, f"[out:json][timeout:25];{src.replace('{a}', a)}->.s;({sets});out center tags {limit};"))
    return qs

def fetchAll(queries, workers, deadline=None):
    """Run queries in parallel -> (poi elements, evidence elements, failed, pending).
    With `deadline`, gives up waiting after that many seconds and returns what is ready; the rest
    keeps running in the background and its answers land in the cache, so the next call gets them
    without sending a second request."""
    from concurrent.futures import ThreadPoolExecutor, wait
    def one(k, q):
        try:
            return overpass(q[1])
        except RuntimeError:
            return None
        finally:
            INFLIGHT.discard(k)
    keys = [overpassKey(q[1]) for q in queries]
    ex = ThreadPoolExecutor(workers)
    fs = {}
    for k, q in zip(keys, queries):
        if k not in fs and k not in INFLIGHT:  # never two copies of one query at the same time
            INFLIGHT.add(k)
            fs[k] = ex.submit(one, k, q)
    wait(fs.values(), timeout=deadline)
    ex.shutdown(wait=False)  # the rest keeps running and fills the cache
    got = {k: f.result() for k, f in fs.items() if f.done()}
    res = [got.get(k, BUSY) for k in keys]
    pending = sum(r is BUSY for r in res)
    if not pending and all(r is None for r in res):
        raise RuntimeError("Alle Overpass-Server nicht erreichbar")
    ok = lambda r: r is not None and r is not BUSY
    pois = [e for (ev, _q), r in zip(queries, res) if ok(r) and not ev for e in r]
    evid = [e for (ev, _q), r in zip(queries, res) if ok(r) and ev for e in r]
    return pois, evid, sum(r is None for r in res), pending

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
    """cafe / bakery / restaurant element -> (lat, lon, label, strong, eatery) or None.
    strong = has seats, so likely a WC. eatery = restaurant / fast food, which only counts for big stores."""
    t = e.get("tags", {})
    lat = e.get("lat") or e.get("center", {}).get("lat")
    lon = e.get("lon") or e.get("center", {}).get("lon")
    if lat is None: return None
    if t.get("amenity") == "cafe":
        return lat, lon, "Café", True, False
    if t.get("amenity") in ("restaurant", "fast_food"):
        return lat, lon, "Restaurant", True, True
    if t.get("shop") == "bakery":
        if t.get("indoor_seating") == "yes":
            return lat, lon, "Bäcker mit Sitzplätzen", True, False
        return lat, lon, "Bäcker", False, False
    return None

def enrich(pois, evid):
    """Set wc / hint on shops without a confirmed WC from cafes and bakeries next to them."""
    ev = [x for x in map(evidenceOf, evid) if x]
    for p in pois:
        r = EVID_RADIUS.get(p["kind"])
        if not r or p["wc"] is True: continue
        big = p["kind"] in ("moebel", "bau")
        near = [x for x in ev if hav((p["lat"], p["lon"]), (x[0], x[1])) <= r and (big or not x[4])]
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

INFLIGHT = set()  # cache keys of queries that are running right now (foreground or background)
BUSY = object()  # fetchAll marker: this query has not answered yet
DEAD = {}  # cache key -> time of the last failed attempt, so a poll does not start the same query again
DEAD_FOR = 120

def overpassKey(query):
    return hashlib.sha1(query.encode()).hexdigest()

def overpass(query):
    k = overpassKey(query)
    with _db() as c:
        row = c.execute("select t,v from c where k=?", (k,)).fetchone()
    if row and time.time() - row[0] < TTL:
        return json.loads(row[1])
    if time.time() - DEAD.get(k, 0) < DEAD_FOR:
        if row:
            return json.loads(row[1])
        raise RuntimeError("Overpass: vor Kurzem fehlgeschlagen")
    try:
        data = overpass_live(query)
    except RuntimeError:
        DEAD[k] = time.time()
        if row:
            return json.loads(row[1])
        raise
    DEAD.pop(k, None)
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
        els, evid, failed, _pending = run_groups(f"around:{radius + pad},{glat:.2f},{glon:.2f}")
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

GMAPS_HOSTS = ("google.com", "google.de", "goo.gl", "g.co")  # only these are ever fetched
COORD = re.compile(r"^(-?\d{1,3}(?:\.\d+)?),\s*(-?\d{1,3}(?:\.\d+)?)$")

def hostOk(url):
    u = urlparse(url)
    h = (u.hostname or "").lower()
    return u.scheme == "https" and any(h == x or h.endswith("." + x) for x in GMAPS_HOSTS)

def gmapsResolve(url):
    """Follow a short Google Maps link (maps.app.goo.gl/...) to the full /maps/dir/ URL.
    Refuses every host that is not Google, also in redirects (no fetching of arbitrary URLs)."""
    seen = set()
    for _ in range(6):
        if not hostOk(url):
            raise ValueError("Das ist kein Google-Maps-Link")
        u = urlparse(url)
        if (u.hostname or "").startswith("consent."):  # consent wall: the real URL is in ?continue=
            nxt = parse_qs(u.query).get("continue", [""])[0]
            if not nxt: raise ValueError("Google verlangt eine Zustimmung, bitte den langen Link einfügen")
            url = nxt
            continue
        # a link that already carries the route needs no request; fetching it only adds a consent round trip
        qs = parse_qs(u.query, keep_blank_values=True)
        if "/maps/dir" in u.path or qs.get("api") == ["1"] or qs.get("saddr") or qs.get("daddr"):
            return url
        if url in seen:  # bounced back (consent page above): nothing new to follow
            return url
        seen.add(url)
        r = requests.get(url, headers=UA, timeout=10, allow_redirects=False)
        loc = r.headers.get("Location")
        if not (300 <= r.status_code < 400 and loc):
            return url
        url = urljoin(url, loc)
    raise ValueError("Zu viele Weiterleitungen")

def stopOf(text):
    text = text.strip()
    m = COORD.match(text)
    return {"name": text, "lat": float(m[1]) if m else None, "lon": float(m[2]) if m else None}

def gmapsStops(url):
    """Full Google Maps directions URL -> [{name, lat, lon}] (lat / lon None when only a name is known)."""
    u = urlparse(url)
    q = parse_qs(u.query)
    if q.get("api") == ["1"] and q.get("destination"):
        raw = q.get("origin", []) + (q.get("waypoints", [""])[0].split("|") if q.get("waypoints") else []) + q["destination"]
    elif "/dir/" in u.path:
        raw = []
        for seg in u.path.split("/dir/", 1)[1].split("/"):
            if seg.startswith("@") or seg.startswith("data="): break
            if seg.strip(): raw.append(unquote_plus(seg))
    elif q.get("saddr") or q.get("daddr"):
        # the form Google's "share route" copies on the phone: /maps?saddr=51.5,6.9&daddr=Address&dirflg=d
        # (parse_qs already decoded %xx and +), several stops may be chained with " to "
        raw = q.get("saddr", []) + [x for d in q.get("daddr", []) for x in d.split(" to ")]
    else:
        raw = []
    stops = [stopOf(x) for x in raw if x.strip()]
    if len(stops) < 2:
        raise ValueError("Kein Google-Maps-Routenlink: Start und Ziel fehlen")
    # a route link also carries exact coordinates per stop; use them only if they match one to one
    pairs = re.findall(r"!2m2!1d(-?\d+\.?\d*)!2d(-?\d+\.?\d*)", unquote(url))
    if len(pairs) == len(stops):
        for st, (lon, lat) in zip(stops, pairs):
            if st["lat"] is None: st["lat"], st["lon"] = float(lat), float(lon)
    return stops

@app.post("/api/gmaps")
def gmaps():
    """Google Maps route link -> ordered stops with coordinates (start, via..., destination)."""
    try:
        url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
        stops = gmapsStops(gmapsResolve(url))
        for st in stops:
            if st["lat"] is None:
                r = requests.get(NOMINATIM, params={"q": st["name"], "format": "json", "limit": 1,
                                 "countrycodes": "de,at,nl,be,lu,fr,ch,cz,pl,dk"}, headers=UA, timeout=20).json()
                if not r: raise ValueError(f"Ort nicht gefunden: {st['name']}")
                st["lat"], st["lon"] = float(r[0]["lat"]), float(r[0]["lon"])
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(points=stops)

def resample(coords, step=1500):
    """coords: [(lat,lon)] -> points every ~step metres plus cumulative distance."""
    out, cum, acc, total = [coords[0]], [0.0], 0.0, 0.0
    for a, b in zip(coords, coords[1:]):
        d = hav(a, b); acc += d; total += d
        if acc >= step:
            out.append(b); cum.append(total); acc = 0
    return out, cum, total

def osrmRoutes(stops, alternatives=False, steps=False):
    """OSRM driving routes through the stops [[lat, lon], ...]; the first one is the fastest."""
    coords = ";".join(f"{x[1]},{x[0]}" for x in stops)
    alt = "&alternatives=3" if alternatives else ""
    st = "&steps=true" if steps else ("&steps=false" if alternatives else "")
    r = requests.get(f"{OSRM}{coords}?overview=full&geometries=geojson{alt}{st}", headers=UA, timeout=30).json()
    return r.get("routes") or []

def mainRoads(route, n=3):
    """The roads (A 3, B 42 ...) a route spends most of its distance on, longest first."""
    dist = {}
    for leg in route.get("legs", []):
        for st in leg.get("steps", []):
            ref = (st.get("ref") or "").split(";")[0].strip()
            if ref: dist[ref] = dist.get(ref, 0) + st.get("distance", 0)
    return sorted(dist, key=dist.get, reverse=True)[:n]

@app.post("/api/routes")
def routes():
    """Route options between start and destination (through via), to choose one before searching."""
    j = request.get_json()
    stops = [j["from"]] + (j.get("via") or []) + [j["to"]]
    rs = osrmRoutes(stops, alternatives=True, steps=True)
    if not rs:
        return jsonify(error="Keine Route gefunden"), 400
    return jsonify(routes=[{"total": round(r["distance"]), "duration": round(r["duration"]),
                            "roads": mainRoads(r),
                            "route": [(c[1], c[0]) for c in r["geometry"]["coordinates"][::max(1, len(r["geometry"]["coordinates"]) // 1500)]]}
                           for r in rs])

@app.post("/api/along")
def along():
    j = request.get_json()
    o, d = j["from"], j["to"]
    radius = int(j.get("radius", 2000))
    ahead_km = float(j.get("ahead_km", 150))
    via = j.get("via") or []  # stops between start and destination, e.g. from a Google Maps route
    alt = max(0, int(j.get("alt") or 0))  # index of the route option picked in /api/routes
    rs = osrmRoutes([o] + via + [d], alternatives=alt > 0)
    if not rs:
        return jsonify(error="Keine Route gefunden"), 400
    route = rs[min(alt, len(rs) - 1)]
    coords = [(c[1], c[0]) for c in route["geometry"]["coordinates"]]
    pts, cum, total = resample(coords)
    via_along = [round(cum[min(range(len(pts)), key=lambda i: hav(pts[i], tuple(v)))]) for v in via]
    keep = [i for i, c in enumerate(cum) if c <= ahead_km * 1000] or [0]
    spts, scum = [pts[i] for i in keep], [cum[i] for i in keep]
    # Overpass: chunk the polyline so queries stay small
    CH = 26
    jobs = []
    for s in range(0, len(spts), CH - 1):
        chunk = spts[s:s + CH]
        if len(chunk) < 2: chunk = chunk * 2
        line = ",".join(f"{la:.5f},{lo:.5f}" for la, lo in chunk)
        jobs += allQueries(f"(around:{radius},{line})", 1500, merge=True)
    try:
        els, evid, failed, pending = fetchAll(jobs, WORKERS, deadline=ALONG_CUT)
    except RuntimeError:
        return jsonify(error="Overpass-Server nicht erreichbar, bitte später erneut"), 502
    pois = dedupe([p for p in map(to_poi, els) if p])
    # while the rest is still running, do not wait for this second query as well
    enrich(pois, evid if pending else evid + shopEvidence(pois))
    for p in pois:
        best = min(range(len(spts)), key=lambda i: hav(spts[i], (p["lat"], p["lon"])))
        p["off"] = round(hav(spts[best], (p["lat"], p["lon"])))
        p["along"] = round(scum[best])
    pois.sort(key=lambda p: p["along"])
    return jsonify(route=coords[::max(1, len(coords)//1500)], total=round(total),
                   duration=round(route["duration"]), pois=pois, partial=failed, pending=pending,
                   via_along=via_along)

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
