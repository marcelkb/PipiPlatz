import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

class ErrorContract(unittest.TestCase):
    """HTTP errors keep their status, crashes become a generic 502 without internals (B5/B6)."""

    def setUp(self):
        self.c = app.app.test_client()

    def test_unknown_url_is_a_404_not_a_502(self):
        r = self.c.get("/api/nope")
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.get_json())

    def test_missing_query_parameter_is_a_400_not_a_502(self):
        r = self.c.get("/api/nearby?lon=8")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_wrong_method_keeps_405(self):
        self.assertEqual(self.c.post("/").status_code, 405)   # GET /api/along is a 404: the static route shadows it

    def test_a_crash_is_a_generic_502_and_leaks_nothing(self):
        secret = "C:\\secret\\path\\app.py line 42 token=abc123"
        with mock.patch.object(app, "osrmRoutes", side_effect=RuntimeError(secret)), \
             self.assertLogs(app.app.logger, level="ERROR") as logs:
            r = self.c.post("/api/routes", json={"from": [51.0, 8.0], "to": [50.0, 8.0]})
        self.assertEqual(r.status_code, 502)
        body = r.get_json()
        self.assertIn("error", body)
        self.assertNotIn("secret", body["error"])
        self.assertNotIn("abc123", body["error"])
        self.assertIn("abc123", "\n".join(logs.output))   # the detail goes to the log instead

if __name__ == "__main__":
    unittest.main()
