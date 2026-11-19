import json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import build_pois

def row(rid, lat, lon, ev=False, **tags):
    r = {"type": "node", "id": rid, "lat": lat, "lon": lon, "tags": tags}
    if ev:
        r["ev"] = 1
    return r

# a motorway going south at lon 8.0 (as in test_along_side), stops beside it
LINE = [[8.0, 51.0 - i / 1000] for i in range(1001)]
ROWS = [row(1, 50.5, 8.001, highway="services", name="Testhausen"),
        row(2, 50.6, 8.001, amenity="toilets"),
        row(3, 50.7, 8.3, highway="rest_area"),  # 21 km off the route
        # supermarket at the edge of a nearby box, its cafe 60 m further out
        row(4, 50.52, 8.0, shop="supermarket", brand="REWE", name="REWE"),
        row(5, 50.5205, 8.0, ev=True, amenity="cafe"),
        row(6, 50.518, 8.03, highway="rest_area")]  # 2 km N and 2.1 km E of 50.5/8.0: a box corner

def refuse(q):
    raise AssertionError(f"Overpass asked although the local data covers it: {q[:80]}")

def osrm(url, **kw):
    class R:
        def json(self):
            r = {"geometry": {"coordinates": LINE}, "distance": 111000, "duration": 3600,
                 "legs": [{"steps": [{"ref": "A 5", "geometry": {"coordinates": LINE}, "duration": 3600}]}]}
            if url.split("?")[0].count(";") >= 2:  # the opposite-carriageway check: on the way
                return {"routes": [{"distance": 3000, "duration": 100}]}
            return {"routes": [r]}
    return R()

class LocalDbCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.dir.name, "pois.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in ROWS)
        with open(os.path.join(self.dir.name, "pois.meta.json"), "w", encoding="utf-8") as f:
            # covered: 49.9-51.2 N, 7.5-8.6 E (cells of 0.1 degree)
            json.dump({"cells": [[i, j] for i in range(499, 512) for j in range(75, 86)]}, f)
        env = {k: v for k, v in os.environ.items() if k != "PIPI_NO_LOCAL"}
        for p in (mock.patch.object(app, "LOCAL_FILE", path), mock.patch.object(app, "_local", None),
                  mock.patch.dict(os.environ, env, clear=True), mock.patch.object(app, "overpass_live", refuse)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.dir.cleanup)

class LocalDb(LocalDbCase):
    # ===== Acceptance criteria =====
    def test_route_search_answers_from_the_file_without_overpass(self):
        with mock.patch.object(app.requests, "get", osrm):
            r = app.app.test_client().post("/api/along", json={"from": [51.0, 8.0], "to": [50.0, 8.0]})
        j = r.get_json()
        self.assertEqual(r.status_code, 200, j)
        self.assertEqual({p["id"] for p in j["pois"]}, {"node1", "node2", "node4"})  # node3 is too far off
        self.assertEqual((j["partial"], j["pending"]), (0, 0))

    def test_nearby_search_answers_from_the_file_without_overpass(self):
        r = app.app.test_client().get("/api/nearby?lat=50.5&lon=8.0")
        j = r.get_json()
        self.assertEqual(r.status_code, 200, j)
        self.assertIn("node1", {p["id"] for p in j["pois"]})

    # ===== Core behavior =====
    def test_cafe_just_outside_the_box_still_counts_for_the_supermarket(self):
        els, evid = app.localBox(50.50, 7.99, 50.5202, 8.01)
        self.assertIn(4, [e["id"] for e in els])
        self.assertNotIn(5, [e["id"] for e in els])  # evidence is never a stop
        pois = [p for p in map(app.to_poi, els) if p]
        app.enrich(pois, evid)
        self.assertEqual(next(p for p in pois if p["id"] == "node4")["wc"], "likely")

    def test_box_reaching_outside_the_covered_cells_goes_live(self):
        self.assertIsNone(app.localBox(50.0, 8.4, 50.5, 8.7))  # its east edge is past 8.6
        self.assertIsNotNone(app.localBox(50.0, 8.4, 50.5, 8.55))

    def test_prefilter_lets_every_live_selector_through(self):
        # build_pois filters on tag pairs in C before the selectors run: a new GROUPS entry needs one too
        for conds in build_pois.POI_SEL + build_pois.SRC_SEL:
            k, op, v = conds[0]
            self.assertTrue(op == "=" and (k, v) in build_pois.PREFILTER, conds)

    def test_circle_search_drops_the_box_corners(self):
        self.assertIn(6, [e["id"] for e in app.localBox(*app.boxOf([(50.5, 8.0)], 2300))[0]])
        els, _ = app.localAround(50.5, 8.0, 2300)
        self.assertEqual({e["id"] for e in els}, {1, 4})  # node6 is 2.9 km away diagonally

    def test_builder_refuses_a_build_that_lost_rows(self):
        meta = os.path.join(self.dir.name, "pois.meta.json")
        with open(meta, "w", encoding="utf-8") as f:
            json.dump({"counts": {"raststaette": 100}}, f)
        few = [row(i, 50.0, 8.0, highway="services") for i in range(80)]
        out = os.path.join(self.dir.name, "new.jsonl")
        with mock.patch.object(build_pois, "META", meta), mock.patch.object(build_pois, "OUT", out), \
             mock.patch.object(sys, "argv", ["build_pois.py"]):
            with self.assertRaises(SystemExit) as cm:
                build_pois.writeOut(few, [])
        self.assertEqual(cm.exception.code, 1)
        self.assertFalse(os.path.exists(out))

    # ===== Coverage and null checks =====
    def test_missing_file_means_no_local_data(self):
        with mock.patch.object(app, "LOCAL_FILE", os.path.join(self.dir.name, "none.jsonl")):
            self.assertIsNone(app.localBox(50.0, 8.0, 50.1, 8.1))

    def test_switched_off_by_env(self):
        with mock.patch.dict(os.environ, {"PIPI_NO_LOCAL": "1"}):
            self.assertIsNone(app.localBox(50.0, 8.0, 50.1, 8.1))

if __name__ == "__main__":
    unittest.main()
