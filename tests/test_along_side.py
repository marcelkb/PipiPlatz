import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

# a motorway going south (lat 51 -> 50) at lon 8.0; driving south, east is on the left
LINE = [[8.0, 51.0 - i / 1000] for i in range(1001)]

def fakeRoute(ref="A 3"):
    r = {"geometry": {"coordinates": LINE}, "distance": 1000, "duration": 3600,
         "legs": [{"steps": [{"ref": ref, "geometry": {"coordinates": LINE}}]}]}
    return {"routes": [r]}

def poi(pid, lat, lon, name=""):
    return {"type": "way", "id": pid, "center": {"lat": lat, "lon": lon},
            "tags": {"highway": "services", "name": name}}

def along(els, ref="A 3", detour=5000):
    """Run /api/along with Overpass and OSRM mocked.

    detour: metres OSRM needs for start -> stop -> destination (the direction check). A dict maps
    the stop's longitude to its metres; None forbids the check, which also fails the test if the
    code asks for one."""
    def get(url, **kw):
        class R:
            def json(self):
                path = url.split("?")[0]
                if path.count(";") + 1 > 2:
                    lon = round(float(path.split(";")[1].split(",")[0]), 3)  # OSRM is lon,lat
                    m = detour if isinstance(detour, (int, float)) else detour.get(lon)
                    if m is None:
                        raise AssertionError(f"no direction check expected for lon {lon}: {url}")
                    return {"routes": [{"distance": m, "duration": 600}]}
                return fakeRoute(ref)
        return R()

    with mock.patch.object(app.requests, "get", get), \
         mock.patch.object(app, "fetchAll", lambda q, w, deadline=None: (els, [], 0, 0)), \
         mock.patch.object(app, "shopEvidence", lambda p, limit=25: []):
        r = app.app.test_client().post("/api/along", json={"from": [51.0, 8.0], "to": [50.0, 8.0]})
    assert r.status_code == 200, r.get_json()
    return {p["id"]: p for p in r.get_json()["pois"]}

class AlongSide(unittest.TestCase):
    """The cross product against the driving direction, within a 150 m band."""

    def test_opposite_carriageway_is_flagged(self):
        # east of a southbound lane is left (the other carriageway), west is ours
        got = along([poi(1, 50.5, 8.001, "Ohligser Heide Ost"),
                     poi(2, 50.5, 7.999, "Ohligser Heide West")],
                    detour={8.001: 12000, 7.999: 400})
        self.assertTrue(got["way1"]["opp"])
        self.assertFalse(got["way2"].get("opp"))

    def test_distance_is_the_distance_to_the_line(self):
        got = along([poi(1, 50.5, 8.001, "Ost")])
        self.assertLess(abs(got["way1"]["off"] - 71), 10)     # ~71 m at this latitude, not 700
        self.assertLess(abs(got["way1"]["along"] - 55600), 600)

    def test_place_beside_the_motorway_is_not_flagged(self):
        # 350 m off the line, 5.2 km by road: reachable over an exit, so it must stay visible
        got = along([poi(1, 50.5, 8.005, "Waldparkplatz")], detour=5200)
        self.assertFalse(got["way1"].get("opp"))

    def test_left_of_a_normal_road_is_not_flagged(self):
        got = along([poi(1, 50.5, 8.001, "Ost")], ref="B 8", detour=None)
        self.assertFalse(got["way1"].get("opp"))

class AlongDetour(unittest.TestCase):
    """A stop across an interchange is right of the line but kilometres away by road (Toresberg)."""

    def test_long_detour_through_the_stop_marks_it(self):
        # 500 m right of the line and the side test is happy, but OSRM needs 13 km to pass it
        got = along([poi(1, 50.504, 8.007, "Toresberg")], detour=13870)
        self.assertTrue(got["way1"]["opp"])

    def test_short_detour_leaves_it_alone(self):
        got = along([poi(1, 50.504, 8.007, "Autohof")], detour=5200)  # ~2 km in and out
        self.assertFalse(got["way1"].get("opp"))

class AlongDetourFails(unittest.TestCase):
    """One failing direction check must not take the whole search down (review finding B1)."""

    def run_along(self, boom):
        from concurrent.futures import ThreadPoolExecutor
        spies = []

        class Spy(ThreadPoolExecutor):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.shutdowns = 0
                spies.append(self)
            def shutdown(self, *a, **kw):
                self.shutdowns += 1
                super().shutdown(*a, **kw)

        def get(url, **kw):
            class R:
                def json(self):
                    path = url.split("?")[0]
                    if path.count(";") + 1 > 2:
                        lon = round(float(path.split(";")[1].split(",")[0]), 3)
                        if lon == 8.002:
                            raise boom
                        return {"routes": [{"distance": 13870, "duration": 600}]}
                    return fakeRoute("A 3")
            return R()

        els = [poi(1, 50.5, 8.002, "Kaputt"), poi(2, 50.504, 8.007, "Toresberg")]
        with mock.patch.object(app.requests, "get", get), \
             mock.patch("concurrent.futures.ThreadPoolExecutor", Spy), \
             mock.patch.object(app, "fetchAll", lambda q, w, deadline=None: (els, [], 0, 0)), \
             mock.patch.object(app, "shopEvidence", lambda p, limit=25: []):
            r = app.app.test_client().post("/api/along", json={"from": [51.0, 8.0], "to": [50.0, 8.0]})
        return r, spies

    def test_a_failing_check_drops_only_that_stop(self):
        r, _ = self.run_along(ConnectionError("osrm down"))
        self.assertEqual(r.status_code, 200, r.get_json())
        got = {p["id"]: p for p in r.get_json()["pois"]}
        self.assertEqual(set(got), {"way1", "way2"})
        self.assertTrue(got["way2"]["opp"])          # the healthy check still counted

    def test_the_executor_is_shut_down_when_a_check_fails(self):
        _, spies = self.run_along(ConnectionError("osrm down"))
        self.assertEqual(len(spies), 1)
        self.assertGreaterEqual(spies[0].shutdowns, 1)

class AlongNoNames(unittest.TestCase):
    def test_a_plain_wc_node_is_checked_too(self):
        # the wrong-side test is about the carriageway, not the place type
        el = {"type": "node", "id": 7, "lat": 50.504, "lon": 8.007, "tags": {"amenity": "toilets"}}
        got = along([el], detour=13870)
        self.assertTrue(got["node7"]["opp"])

    def test_town_wc_nodes_do_not_use_up_the_checks(self):
        # 30 public toilets sit closer to the line and pushed the rest area out of the list
        # (Toresberg, 2026-11-08: 56 WC nodes, the Rastplatz at 728 m was never verified)
        els = [{"type": "node", "id": i, "lat": 50.5 + i * 0.001, "lon": 8.002,
                "tags": {"amenity": "toilets"}} for i in range(30)]
        els.append(poi(9, 50.504, 8.007, "Toresberg"))
        got = along(els, detour={8.002: 300, 8.007: 13870})
        self.assertFalse(got["node0"].get("opp"))
        self.assertTrue(got["way9"]["opp"])

if __name__ == "__main__":
    unittest.main()
