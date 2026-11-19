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

    def test_same_name_twin_23_m_away_is_one_stop(self):
        # OSM sometimes has one petrol station as two nodes with the same name, here 24 m apart
        a = app.to_poi({"type": "node", "id": 11, "lat": 50.11, "lon": 8.68,
                        "tags": {"amenity": "fuel", "brand": "Raiffeisen", "name": "Raiffeisen"}})
        b = app.to_poi({"type": "node", "id": 12, "lat": 50.11007, "lon": 8.68032,
                        "tags": {"amenity": "fuel", "brand": "Raiffeisen", "name": "Raiffeisen"}})
        self.assertEqual([p["id"] for p in app.dedupe([a, b])], ["node11"])

    def test_different_names_or_40_m_apart_stay_two_stops(self):
        a = app.to_poi(fuel(1))
        b = app.to_poi({**fuel(1), "id": 7, "lat": a["lat"] + 0.0002})  # 22 m north, other name below
        b["name"] = "Tankstelle 7"
        c = app.to_poi({**fuel(1), "id": 8, "lat": a["lat"] + 0.00036})  # 40 m north, same name
        self.assertEqual(len(app.dedupe([a, b, c])), 3)

    def test_rest_area_twins_stay_two_stops(self):
        # the mirrored Rastplatz on the other carriageway has the same name: the side test needs both
        r = [app.to_poi({"type": "way", "id": i, "center": {"lat": 50.0 + i * 0.0002, "lon": 8.0},
                         "tags": {"highway": "rest_area", "name": "Rastplatz Testwald"}}) for i in (1, 2)]
        self.assertEqual(len(app.dedupe(r)), 2)

if __name__ == "__main__":
    unittest.main()
