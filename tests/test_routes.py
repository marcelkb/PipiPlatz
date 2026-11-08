import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

def mkRoute(dx, dist, dur, steps):
    n = 60
    coords = [[8.68 + (11.58 - 8.68) * i / n, 50.11 + (48.14 - 50.11) * i / n + dx] for i in range(n + 1)]
    return {"geometry": {"coordinates": coords}, "distance": dist, "duration": dur,
            "legs": [{"steps": [{"ref": r, "distance": d} for r, d in steps]}]}

ROUTES = {"routes": [
    mkRoute(0, 393000, 14000, [("A 3;E 35", 200000), ("A 9", 150000), ("B 3", 30000), ("", 13000), ("K 1", 500)]),
    mkRoute(0.2, 423000, 15900, [("A 8", 250000), ("A 5", 100000), ("K 807", 60000)]),
]}

class Base(unittest.TestCase):
    def call(self, path, body):
        seen = {}
        class R:
            def json(self): return ROUTES
        def get(url, **kw):
            seen["url"] = url
            return R()
        with mock.patch.object(app.requests, "get", get), \
             mock.patch.object(app, "fetchAll", lambda q, w, deadline=None: ([], [], 0, 0)):
            r = app.app.test_client().post(path, json=body)
        return r, seen["url"]

class Routes(Base):
    body = {"from": [50.11, 8.68], "to": [48.14, 11.58]}

    def test_lists_alternatives_with_main_roads(self):
        r, url = self.call("/api/routes", self.body)
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertIn("alternatives=3", url)
        self.assertIn("steps=true", url)
        self.assertEqual(len(j["routes"]), 2)
        self.assertEqual(j["routes"][0]["roads"], ["A 3", "A 9", "B 3"])  # by distance, first ref of "A 3;E 35"
        self.assertEqual(j["routes"][1]["roads"], ["A 8", "A 5", "K 807"])
        self.assertEqual((j["routes"][1]["total"], j["routes"][1]["duration"]), (423000, 15900))
        self.assertTrue(len(j["routes"][0]["route"]) > 10)

    def test_no_route_is_an_error(self):
        global ROUTES
        saved, ROUTES = ROUTES, {"routes": []}
        try:
            r, _ = self.call("/api/routes", self.body)
        finally:
            ROUTES = saved
        self.assertEqual(r.status_code, 400)

class AlongAlt(Base):
    body = {"from": [50.11, 8.68], "to": [48.14, 11.58]}

    def test_alt_picks_that_route(self):
        r, url = self.call("/api/along", dict(self.body, alt=1))
        self.assertEqual(r.get_json()["duration"], 15900)
        self.assertIn("alternatives=3", url)
        self.assertIn("steps=false", url)

    def test_alt_out_of_range_falls_back_to_last(self):
        r, _ = self.call("/api/along", dict(self.body, alt=7))
        self.assertEqual(r.get_json()["duration"], 15900)

    def test_default_is_first_route_without_alternatives(self):
        r, url = self.call("/api/along", self.body)
        self.assertEqual(r.get_json()["duration"], 14000)
        self.assertNotIn("alternatives", url)

if __name__ == "__main__":
    unittest.main()
