import contextlib, io, json, os, sys, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import app
import validate_opposite as vo

# a motorway going south at lon 8.0, one rest area beside it, one across the median
LINE = [[8.0, 51.0 - i / 1000] for i in range(1001)]
ROUTE = {"geometry": {"coordinates": LINE}, "distance": 111000, "duration": 3600,
         "legs": [{"steps": [{"ref": "A 3", "distance": 111000, "geometry": {"coordinates": LINE}}]}]}

def stop(pid, lon, kind="rastplatz"):
    return {"id": pid, "lat": 50.5, "lon": lon, "kind": kind, "name": "Stop " + pid, "off": 70,
            "along": 55600, "wc": True}

def answer(via=None, pois=None):
    return {"from": [51.0, 8.0], "to": [50.0, 8.0], "via": via or [],
            "pois": pois or [stop("a", 8.001), stop("b", 7.999)], "pending": 0, "partial": 0}

class AuditUsesTheAppsRoute(unittest.TestCase):
    """The audit has to measure against the road the app searched, via points included (T2)."""

    def test_the_refetched_route_goes_through_the_same_via_points(self):
        seen = []
        def osrm(stops, alternatives=False, steps=False):
            seen.append(stops)
            return [ROUTE]
        with mock.patch.object(app, "osrmRoutes", osrm), \
             mock.patch.object(app, "accessDetour", lambda coords, pt: 300):
            vo.audit(answer(via=[[50.7, 8.0], [50.3, 8.0]]))
        self.assertEqual(seen, [[[51.0, 8.0], [50.7, 8.0], [50.3, 8.0], [50.0, 8.0]]])

    def test_without_via_it_is_still_from_to(self):
        seen = []
        with mock.patch.object(app, "osrmRoutes", lambda s, **k: seen.append(s) or [ROUTE]), \
             mock.patch.object(app, "accessDetour", lambda coords, pt: 300):
            vo.audit(answer())
        self.assertEqual(seen, [[[51.0, 8.0], [50.0, 8.0]]])

    def test_a_corridor_may_carry_via_points_and_drive_sends_them(self):
        sent = {}
        class R:
            def raise_for_status(self): pass
            def json(self): return {"pois": []}
        def post(url, json=None, timeout=None):
            sent.update(json); return R()
        with mock.patch.object(vo.requests, "post", post):
            vo.drive("x", [51.0, 8.0], [50.0, 8.0], [[50.5, 8.0]])
        self.assertEqual(sent["via"], [[50.5, 8.0]])

class AuditSurvivesFailures(unittest.TestCase):
    """One OSRM error must not cost the whole run (T3)."""

    def test_a_failing_detour_check_leaves_that_stop_unverified(self):
        def detour(coords, pt):
            if round(pt[1], 3) == 8.001:
                raise ConnectionError("osrm down")
            return 300
        with mock.patch.object(app, "osrmRoutes", lambda s, **k: [ROUTE]), \
             mock.patch.object(app, "accessDetour", detour):
            rows, problems, edge, twins = vo.audit(answer())
        byName = {r["name"]: r for r in rows}
        self.assertEqual(len(rows), 2)
        self.assertIsNone(byName["Stop a"]["det"])
        self.assertTrue(byName["Stop a"]["det_failed"])
        self.assertEqual(byName["Stop b"]["det"], 300)

    def run_main(self, audit):
        tmp = tempfile.mkdtemp()
        routes = [("first", [51.0, 8.0], [50.0, 8.0]), ("second", [51.0, 8.0], [50.0, 8.0]),
                  ("third", [51.0, 8.0], [50.0, 8.0])]
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"TEMP": tmp}), mock.patch.object(vo, "ROUTES", routes), \
             mock.patch.object(vo, "drive", lambda name, a, b, via=None: answer()), \
             mock.patch.object(vo, "audit", audit), contextlib.redirect_stdout(out):
            vo.main()
        path = os.path.join(tmp, "opposite_evidence.json")
        return out.getvalue(), (json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None)

    def test_a_failing_audit_skips_that_route_and_keeps_the_others(self):
        calls = []
        def audit(j):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("OSRM gave no route")
            return [], [], [], 0
        text, evidence = self.run_main(audit)
        self.assertEqual(len(calls), 3, "the run stopped at the failing route")
        self.assertIn("FAILED to audit", text)
        self.assertEqual([e["route"] for e in evidence], ["first", "third"])

    def test_evidence_is_on_disk_before_the_next_route_starts(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "opposite_evidence.json")
        onDisk = []
        def audit(j):
            onDisk.append(json.load(open(path, encoding="utf-8")) if os.path.exists(path) else [])
            return [], [], [], 0
        routes = [("first", [51.0, 8.0], [50.0, 8.0]), ("second", [51.0, 8.0], [50.0, 8.0])]
        with mock.patch.dict(os.environ, {"TEMP": tmp}), mock.patch.object(vo, "ROUTES", routes), \
             mock.patch.object(vo, "drive", lambda name, a, b, via=None: answer()), \
             mock.patch.object(vo, "audit", audit), contextlib.redirect_stdout(io.StringIO()):
            vo.main()
        self.assertEqual([len(x) for x in onDisk], [0, 1], "nothing was written until the end of the run")

if __name__ == "__main__":
    unittest.main()
