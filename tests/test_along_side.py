import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

# a motorway going south (lat 51 -> 50) at lon 8.0; driving south, east is on the left
STEP = [[8.0, 51.0], [8.0, 50.5], [8.0, 50.0]]

def fakeRoute(ref="A 3"):
    line = [[8.0, 51.0 - i / 1000] for i in range(1001)]  # dense, so the 1.5 km samples are coarse
    r = {"geometry": {"coordinates": line}, "duration": 3600,
         "legs": [{"steps": [{"ref": ref, "geometry": {"coordinates": line}}]}]}
    return {"routes": [r]}

def poi(pid, lat, lon, name=""):
    return {"type": "way", "id": pid, "center": {"lat": lat, "lon": lon},
            "tags": {"highway": "services", "name": name}}

class AlongSide(unittest.TestCase):
    def run_along(self, els, ref="A 3"):
        class R:
            def json(self): return fakeRoute(ref)
        with mock.patch.object(app.requests, "get", lambda u, **kw: R()), \
             mock.patch.object(app, "fetchAll", lambda q, w, deadline=None: (els, [], 0, 0)), \
             mock.patch.object(app, "shopEvidence", lambda p, limit=25: []):
            r = app.app.test_client().post("/api/along", json={"from": [51.0, 8.0], "to": [50.0, 8.0]})
        self.assertEqual(r.status_code, 200)
        return {p["id"]: p for p in r.get_json()["pois"]}

    def test_opposite_carriageway_is_flagged(self):
        got = self.run_along([poi(1, 50.5, 8.001, "Ohligser Heide Ost"),
                              poi(2, 50.5, 7.999, "Ohligser Heide West")])
        self.assertTrue(got["way1"]["opp"])   # east of a southbound lane = left
        self.assertFalse(got["way2"].get("opp"))

    def test_distance_is_the_distance_to_the_line(self):
        got = self.run_along([poi(1, 50.5, 8.001, "Ost")])
        self.assertLess(abs(got["way1"]["off"] - 71), 10)     # ~71 m at this latitude, not 700
        self.assertLess(abs(got["way1"]["along"] - 55600), 600)

    def test_place_beside_the_motorway_is_not_flagged(self):
        # 350 m off the line: reachable over an exit, so it must stay visible
        got = self.run_along([poi(1, 50.5, 8.005, "Waldparkplatz")])
        self.assertFalse(got["way1"].get("opp"))

    def test_left_of_a_normal_road_is_not_flagged(self):
        got = self.run_along([poi(1, 50.5, 8.001, "Ost")], ref="B 8")
        self.assertFalse(got["way1"].get("opp"))

if __name__ == "__main__":
    unittest.main()
