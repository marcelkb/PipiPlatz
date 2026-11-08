# PipiPlatz
Finds the nearest toilet stops in Germany (Rastplatz/Raststätte, public WC, McDonald's, Burger King, Edeka/Rewe/Kaufland…, Tankstellen) – also **along a route**, on any road, not only Autobahn.

Data: OpenStreetMap via Overpass (free), OSRM routing, Nominatim search. Map: Leaflet/OSM.
Google Maps: "Los" buttons open Google Maps navigation via the POI; optional embedded Google view with a free Maps Embed API key (⚙️).

## Run
    pip install -r requirements.txt
    python app.py        # http://localhost:5000

## On the phone (installable PWA)
Geolocation + "Add to Home Screen" need **HTTPS**. Easiest free options:
- Quick test: `cloudflared tunnel --url http://localhost:5000` (gives an https URL)
- Permanent: deploy to Render/Fly.io/PythonAnywhere free tier (`gunicorn app:app`).
iPhone: Safari → Share → Zum Home-Bildschirm. Android: Chrome → Install app.
