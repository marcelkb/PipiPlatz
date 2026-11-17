import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

def el(i, lat, lon, **tags):
    return {"type": "way", "id": i, "center": {"lat": lat, "lon": lon}, "tags": tags}

# Kreuz Hilden as mapped in OSM: one charging station per operator, none tagged with a WC,
# a public toilet and a cafe with seats next to them
HILDEN = [el(1, 51.1700, 6.9300, amenity="charging_station", name="Hilden Supercharger", brand="Tesla Supercharger", capacity="40"),
          el(2, 51.1702, 6.9304, amenity="charging_station", name="Fastned Kreuz Hilden", brand="Fastned", capacity="22"),
          el(3, 51.1698, 6.9306, amenity="charging_station", name="Schüren Kreuz Hilden", operator="Ihr Bäcker Schüren", capacity="28")]
WC = el(10, 51.1701, 6.9302, amenity="toilets")
CAFE = el(11, 51.1703, 6.9301, amenity="cafe", indoor_seating="yes", name="Seed & Greet")

def run(els, evid):
    pois = app.dedupe([p for p in map(app.to_poi, els) if p])
    app.enrich(pois, evid)
    return app.chargers(pois)

class Ladepark(unittest.TestCase):
    def test_a_charging_station_is_a_ladepark(self):
        p = app.to_poi(HILDEN[0])
        self.assertEqual((p["kind"], p["name"]), ("ladepark", "Hilden Supercharger"))

    def test_hilden_is_one_row_with_its_operators_and_points(self):
        out = run(HILDEN, [WC])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["wc"], "likely")
        self.assertEqual(out[0]["hint"], "Öffentliches WC nebenan")
        self.assertEqual(out[0]["sub"], "Tesla Supercharger, Fastned, Ihr Bäcker Schüren · 90 Ladepunkte")

    def test_an_operator_that_is_also_the_title_is_not_repeated(self):
        out = run([el(4, 51.17, 6.93, amenity="charging_station", operator="Ihr Bäcker Schüren", capacity="14")], [WC])
        self.assertEqual(out[0]["sub"], "14 Ladepunkte")
        self.assertNotIn("_ops", out[0])

    def test_a_cafe_with_seats_next_door_counts(self):
        self.assertEqual(run(HILDEN[:1], [CAFE])[0]["wc"], "likely")

    def test_a_park_without_any_wc_evidence_is_dropped(self):
        self.assertEqual(run(HILDEN, []), [])

    def test_a_tagged_wc_needs_no_evidence(self):
        out = run([el(5, 50.0, 8.0, amenity="charging_station", toilets="yes", capacity="2")], [])
        self.assertIs(out[0]["wc"], True)

    def test_toilets_no_is_dropped(self):
        self.assertIsNone(app.to_poi(el(6, 50.0, 8.0, amenity="charging_station", toilets="no", capacity="20")))

    def test_a_public_toilet_does_not_count_for_a_supermarket(self):
        p = app.to_poi(el(7, 51.1701, 6.9303, shop="supermarket", brand="Edeka", name="Edeka"))
        app.enrich([p], [WC])
        self.assertIsNot(p["wc"], "likely")

    def test_other_kinds_pass_through(self):
        p = app.to_poi(el(8, 50.0, 8.0, amenity="toilets"))
        self.assertEqual(app.chargers([p]), [p])

    def test_the_big_parks_are_queried(self):
        qs = " ".join(q for _, q in app.allQueries("(around:2000,50.11,8.68)", 300))
        self.assertIn('nwr[amenity=charging_station][access!=private][access!=customers]["capacity"', qs)

if __name__ == "__main__":
    unittest.main()
