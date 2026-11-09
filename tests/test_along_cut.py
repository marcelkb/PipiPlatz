import os, sys, gc, time, tempfile, threading, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

N = 200
ROUTE = [{"geometry": {"coordinates": [[8.68 + (11.58 - 8.68) * i / N, 50.11 + (48.14 - 50.11) * i / N]
                                    for i in range(N + 1)]},
          "duration": 9000}]

def elem(i):
    t = (i % 40) / 40  # a point on the route: stops further than the side distance are dropped
    return {"type": "node", "id": i, "lat": 50.11 + (48.14 - 50.11) * t, "lon": 8.68 + (11.58 - 8.68) * t,
            "tags": {"highway": "rest_area"}}

class AlongCut(unittest.TestCase):
    """A route search answers with what is ready, the slow queries finish in the background."""

    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.p = mock.patch.object(app, "DB", self.db)
        self.p.start()
        self.calls = []
        self.lock = threading.Lock()
        mock.patch.object(app, "osrmRoutes", lambda *a, **k: ROUTE).start()
        mock.patch.object(app, "ALONG_CUT", 0.4).start()
        mock.patch.object(app, "WORKERS", 2).start()

    def tearDown(self):
        app.INFLIGHT.clear()
        app.DEAD.clear()
        mock.patch.stopall()
        gc.collect()  # the cache connections are only released when collected (Windows file lock)
        try:
            os.remove(self.db)
        except OSError:
            pass

    def live(self, fast, slow_seconds):
        """Queries that start while fewer than `fast` calls were made answer at once, the rest need time."""
        def f(q):
            with self.lock:
                n = len(self.calls)
                self.calls.append(q)
            if n >= fast:
                time.sleep(slow_seconds)
            return [elem(n)]
        return f

    def search(self):
        return app.app.test_client().post("/api/along", json={"from": [50.11, 8.68], "to": [48.14, 11.58]})

    def waitBackground(self, timeout=20):
        t0 = time.time()
        while app.INFLIGHT and time.time() - t0 < timeout:
            time.sleep(0.05)

    def test_first_answer_comes_before_the_slow_queries(self):
        with mock.patch.object(app, "overpass_live", self.live(fast=2, slow_seconds=2)):
            t0 = time.time()
            r = self.search()
            took = time.time() - t0
            d = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertLess(took, 1.5, "the cut did not cut: %.1f s" % took)
        self.assertGreater(d["pending"], 0)
        self.assertGreater(len(d["pois"]), 0, "nothing was delivered")

    def test_background_results_are_complete_and_come_from_the_cache(self):
        with mock.patch.object(app, "overpass_live", self.live(fast=2, slow_seconds=1)):
            first = self.search().get_json()
            self.waitBackground()
            made = len(self.calls)
            t0 = time.time()
            second = self.search().get_json()
            took = time.time() - t0
        self.assertEqual(second["pending"], 0)
        self.assertGreater(len(second["pois"]), len(first["pois"]), "no results were filled in")
        self.assertEqual(len(self.calls), made, "the second call sent Overpass requests again")
        self.assertLess(took, 1.0)

    def test_a_poll_does_not_send_a_query_that_is_already_running(self):
        with mock.patch.object(app, "overpass_live", self.live(fast=0, slow_seconds=2)):
            self.search()                      # everything is slow, all queries stay in flight
            made = len(self.calls)
            r = self.search()                  # a poll while the background work is still running
            self.assertEqual(len(self.calls), made, "the same queries were sent a second time")
        self.assertGreater(r.get_json()["pending"], 0)

    def test_a_fast_search_is_not_cut(self):
        with mock.patch.object(app, "overpass_live", self.live(fast=99, slow_seconds=0)):
            r = self.search().get_json()
        self.assertEqual(r["pending"], 0)
        self.assertGreater(len(r["pois"]), 0)

if __name__ == "__main__":
    unittest.main()
