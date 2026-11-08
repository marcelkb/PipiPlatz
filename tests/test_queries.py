import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

def fuel(i, tags=None):
    t = {"amenity": "fuel", "name": f"Tankstelle {i}"}
    t.update(tags or {})
    return {"type": "node", "id": i, "lat": 50.11 + i * 0.001, "lon": 8.68, "tags": t}

class FuelQuery(unittest.TestCase):
    """Every petrol station is fetched, not only the ones tagged with a WC."""

    def queries(self):
        return [q for _, q in app.allQueries("(around:2000,50.11,8.68)", 1500)]

    def test_plain_fuel_query_is_sent(self):
        plain = [q for q in self.queries() if "nwr[amenity=fuel](around:" in q and "toilets" not in q]
        self.assertTrue(plain, "no unfiltered amenity=fuel query in GROUPS")

    def test_fuel_without_wc_tag_survives(self):
        p = app.to_poi(fuel(1))
        self.assertIsNotNone(p)
        self.assertEqual(p["kind"], "tank")
        self.assertEqual(p["name"], "Tankstelle 1")

    def test_fuel_with_wc_tag_is_not_duplicated(self):
        a = app.to_poi(fuel(2))
        b = app.to_poi(fuel(2, {"toilets": "yes"}))
        self.assertEqual(len(app.dedupe([a, b])), 1)

if __name__ == "__main__":
    unittest.main()
