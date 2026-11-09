import os, re, sys, gc, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

# A straight 120 km route west -> east at 51.5 N
N = 200
LON0, LON1, LAT = 6.0, 7.7, 51.5
ROUTE = [{"geometry": {"coordinates": [[LON0 + (LON1 - LON0) * i / N, LAT] for i in range(N + 1)]},
          "duration": 7200, "distance": 120000, "legs": []}]
BBOX = re.compile(r"\((\d+\.\d+),(\d+\.\d+),(\d+\.\d+),(\d+\.\d+)\)")

def node(i, lat, lon):
    return {"type": "node", "id": i, "lat": lat, "lon": lon, "tags": {"highway": "rest_area"}}

class AlongBbox(unittest.TestCase):
    """Dense areas timed out on every Overpass server with a 26-point `around:` polyline
    per query, so the first 70 km of a long route came back empty. A bounding box is cheap
    for Overpass; the true distance to the road is measured here afterwards."""

    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")
        mock.patch.object(app, "DB", self.db).start()
        mock.patch.object(app, "osrmRoutes", lambda *a, **k: ROUTE).start()
        self.queries = []
        def live(q):
            self.queries.append(q)
            return [node(1, LAT + 0.005, 6.5),   # 550 m beside the road: inside 2 km
                    node(2, LAT + 0.05, 6.9)]    # 5.5 km beside the road: outside 2 km
        mock.patch.object(app, "overpass_live", live).start()

    def tearDown(self):
        app.INFLIGHT.clear(); app.DEAD.clear()
        mock.patch.stopall(); gc.collect()
        try: os.remove(self.db)
        except OSError: pass

    def search(self, **kw):
        body = {"from": [LAT, LON0], "to": [LAT, LON1], **kw}
        return app.app.test_client().post("/api/along", json=body).get_json()

    def test_no_query_carries_a_polyline(self):
        self.search()
        poi = [q for q in self.queries if "highway=rest_area" in q]
        self.assertTrue(poi)
        for q in poi:
            self.assertNotIn("around:2000,51", q, "a polyline query is back: " + q[:120])
            self.assertTrue(BBOX.search(q), "no bbox in " + q[:120])

    def test_each_bbox_stays_small(self):
        self.search()
        for q in self.queries:
            m = BBOX.search(q)
            if not m: continue
            s, w, n, e = map(float, m.groups())
            km = app.hav((s, w), (n, e)) / 1000
            self.assertLess(km, 45, "bbox diagonal %.0f km would be as slow as the polyline" % km)

    def test_stops_beyond_the_side_distance_are_dropped(self):
        d = self.search(radius=2000)
        ids = {p["id"] for p in d["pois"]}
        self.assertIn("node1", ids)
        self.assertNotIn("node2", ids, "a stop 5.5 km off the road was listed with a 2 km radius")

    def test_a_wider_side_distance_keeps_them(self):
        d = self.search(radius=8000)
        self.assertIn("node2", {p["id"] for p in d["pois"]})

if __name__ == "__main__":
    unittest.main()
