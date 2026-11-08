# PipiPlatz

Finds the nearest toilet stops in Germany, either **around you** or **along a route** on any road
(not only Autobahn). It is a small Flask backend plus a single-file Leaflet PWA with a German UI,
built for a phone in a car holder: the answer (what, how far, is there a WC) comes first.

All data is free: OpenStreetMap through Overpass, OSRM for routing, Nominatim for address search.

## Features

- **In der Nähe**: stops around your position, sorted by distance.
- **Entlang der Route**: enter start and destination, get stops along the way with the distance ahead
  and the detour to each one. Radius and "how far ahead" are adjustable.
- **Google-Maps-Link laden**: paste the share link of a route planned in Google Maps (short
  `maps.app.goo.gl` or long `/maps/dir/` links). Start, destination and the stops in between are read
  from it and the route is searched through them. The road line is calculated again with OSRM, so it
  can differ slightly from Google's, especially for alternative routes.
- **Route options:** when OSRM finds alternatives, "Suchen" first lists them with their main roads
  ("über A 3 · A 9"), distance and time, drawn on the map. Pick one, then search along it. OSRM does not
  always return the road you have in mind; if it is missing, add a stop on it.
- **Filters** by place type, with an "Alle wählen / Alle abwählen" button. On by default: Rastplatz,
  WC and Burger.
- **"Los"** opens Google Maps navigation to the stop. An optional embedded Google Maps view works
  with your own Maps Embed API key (stored in the browser only).
- Search overlay with a progress bar and a time estimate learned from your previous searches.
- Installable PWA, light and dark theme.

## How the WC state is decided

OSM rarely tags toilets on shops, so the state is inferred per place type
(`classify`, `to_poi` and `enrich` in `app.py`):

| Place type | Rule |
|---|---|
| Raststätte, public WC | WC vorhanden |
| Rastplatz, Tankstelle, parking with WC | WC vorhanden unless `toilets=no` (Rastplatz) |
| McDonald's, Burger King | WC vorhanden, except pop-up or takeaway-only stores |
| Supermarket | WC wahrscheinlich if a café or seated bakery is within 80 m, otherwise unklar |
| Möbelhaus, Baumarkt (big chains by name) | WC wahrscheinlich if a café or restaurant is within 150 m; a Möbelhaus without one is still "wahrscheinlich", a Baumarkt "unklar" |
| Einkaufszentrum (`shop=mall`) | WC vorhanden |
| Bahnhof | WC vorhanden with `toilets=yes`; big DB stations (category 1-3) are "wahrscheinlich"; other stations are not listed |

An explicit `toilets=no` in OSM removes a Burger, Supermarkt, Möbelhaus, Baumarkt, Einkaufszentrum or
Bahnhof entry. The UI shows three states: "WC vorhanden", "WC wahrscheinlich", "WC unklar".

## Run

```
pip install -r requirements.txt
python app.py        # http://localhost:5000, set PORT to change it
```

`truststore` is optional but recommended on Windows: it lets Python use the system certificate
store, which fixes `CERTIFICATE_VERIFY_FAILED` errors behind antivirus that inspects TLS.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/nearby?lat=&lon=` | Stops around a point; the radius widens from 3 km up to 24 km until enough are found |
| `POST /api/along` | JSON `{from:[lat,lon], to:[lat,lon], via:[[lat,lon],...], radius, ahead_km}` (`via` optional); routes with OSRM and searches along the line |
| `GET /api/geocode?q=` | Nominatim address search |
| `POST /api/routes` | JSON `{from, to, via}`; up to 3 route options with `roads`, `total`, `duration`, `route`; `/api/along` takes `alt` to pick one |
| `POST /api/gmaps` | JSON `{url}`; reads the stops of a Google Maps route link, returns `points` (start, stops, destination) |

## Caching and speed

- Every Overpass query is cached for 7 days in `cache.db` (SQLite), so a repeat search is fast.
- "In der Nähe" queries run around a ~2 km grid point instead of your exact position, so searches
  from nearby spots reuse the same cache entries. The radius is padded by the offset.
- Eleven public Overpass servers are used as fallbacks. If one is slow, the next starts after 10 s in
  parallel and the first valid answer wins; a host that failed recently is tried last.
- Name filters (`name~`) are slow on the public servers, so furniture and DIY chains are matched in Python.
- A cold search takes a few seconds when the servers are healthy (1.9 s measured for Frankfurt on
  2026-11-08) and much longer when they are overloaded.

## On the phone

Geolocation and "Add to Home Screen" need **HTTPS**.

- Quick test: `cloudflared tunnel --url http://localhost:5000` gives an https URL.
- Permanent: deploy somewhere with HTTPS. The `Procfile` runs `gunicorn app:app`.
  iPhone: Safari, Share, "Zum Home-Bildschirm". Android: Chrome, "Install app".

### Hosting notes

- **PythonAnywhere (free):** outbound calls are limited to an allow-list. `overpass-api.de`,
  `overpass.openstreetmap.fr`, `overpass.kumi.systems`, `router.project-osrm.org` and
  `nominatim.openstreetmap.org` are on it (checked 2026-11-08). `ovp.dresel.systems`,
  `overpass.private.coffee` and `maps.mail.ru` are not. The free plan has one web app, one worker and
  a one-month expiry you have to renew.
- **Render (free):** the web service sleeps after 15 minutes idle and its disk is not kept, so the
  cache starts empty after a restart.

## Data and attribution

Map data © OpenStreetMap contributors (ODbL). The public Overpass, OSRM and Nominatim servers have
fair-use limits, so this is meant for personal use. The app sends the User-Agent
`PipiPlatz/1.0 (personal use)`.

## Known limits

- Google links give stops, not the road line. Stops given only by name are looked up in Nominatim and can
  be matched wrongly. Short links are followed from the server, which Google may answer with a
  consent page; then paste the long link.
- OSM coverage decides everything: a missing or wrong tag gives a wrong WC state.
- Chain names for furniture and DIY stores are matched by name and may miss some branches.
- Route searches send two Overpass queries per ~25 route points (one merged POI query, one for
  supermarket evidence). A 150 km route took 25 s cold on 2026-11-08; repeats come from the cache.
