"""Checks introduced by the `territories` phase: locations, nearby search and polygon territories."""
from __future__ import annotations

import math

import httpx

from harness.suite import Suite, get, get_id, listing, mk, ok, rejected, same_set, status
from scenarios.crm.checks.helpers import fresh_agency, num, person

CENTRE = (38.7223, -9.1393)  # Lisbon
AREA = [[-9.20, 38.69], [-9.10, 38.69], [-9.10, 38.75], [-9.20, 38.75]]


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def loc(p: tuple[float, float] | None) -> dict | None:
    return {"lat": p[0], "lng": p[1]} if p else None


def v11(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v11")
    st = s.state
    ag = fresh_agency(c, "lisbon-venues")
    places = {  # key: (own location, company location); all far from the radius and area edges
        "alfama": ((38.7139, -9.1335), None),       # ~1.1 km, inside
        "belem": ((38.6970, -9.1960), None),        # ~5.7 km, inside
        "cascais": ((38.6979, -9.4215), None),      # ~24.6 km, outside
        "office": (None, (38.7369, -9.1427)),       # ~1.6 km via its company, inside
        "nowhere": (None, None),
        "north": ((38.8000, -9.1300), None),        # ~8.7 km, outside the area (north)
    }

    def fixtures():
        ids = {}
        for k, (own, company) in places.items():
            body = person(ag, k.title(), "Place", **({"location": loc(own)} if own else {}))
            if company:
                body["companyId"] = mk(c, "/companies", {"agencyId": ag, "name": f"{k} HQ", "location": loc(company)}, "co")
            ids[k] = mk(c, "/contacts", body, k)
        got = get(c, f"/contacts/{ids['alfama']}").get("location") or {}
        assert (got.get("lat"), got.get("lng")) == places["alfama"][0], f"location {got}"
        assert get(c, f"/contacts/{ids['office']}").get("location") is None, "a contact's own location must stay its own"
        st["ids"] = ids
    s.check("create_with_locations", fixtures)

    def validation():
        for bad in ({"lat": 91, "lng": 0}, {"lat": 0, "lng": -181}, {"lat": "38", "lng": 9}, {"lat": 1}):
            rejected(c.post("/contacts", json=person(ag, "Bad", "Location", location=bad)), f"location {bad}")
            rejected(c.post("/companies", json={"agencyId": ag, "name": "Bad", "location": bad}), f"company location {bad}")
    s.check("rejects_invalid_locations", validation)

    def nearby(radius: float, keys: list[str], limit: int = 500, **params) -> list[dict]:
        ids = s.need("ids")
        got = listing(c, "/contacts", {"agencyId": ag, "near": f"{CENTRE[0]},{CENTRE[1]}", "radiusKm": radius, **params},
                      limit=limit)
        assert [get_id(x) for x in got] == [ids[k] for k in keys], \
            f"near, {radius} km: {[get_id(x) for x in got]}, expected {[ids[k] for k in keys]}"
        return got

    def near():
        got = nearby(10, ["alfama", "office", "belem", "north"])
        for x, k in zip(got, ["alfama", "office", "belem", "north"]):
            own, company = places[k]
            num(x.get("distanceKm"), round(km(CENTRE, own or company), 1), f"{k}: distanceKm", 0.15)
        nearby(30, ["alfama", "office", "belem", "north", "cascais"])
        nearby(3, ["alfama", "office"])
        nearby(10, ["alfama"], limit=1)
        nearby(10, ["office"], companyId=get(c, f"/contacts/{s.need('ids')['office']}").get("companyId"))
    s.check("nearby_nearest_first", near)

    def follows_company():
        ids = s.need("ids")
        company = get(c, f"/contacts/{ids['office']}").get("companyId")
        ok(c.patch(f"/companies/{company}", json={"location": loc((38.6979, -9.4215))}), "move company")
        nearby(10, ["alfama", "belem", "north"])
        ok(c.patch(f"/contacts/{ids['office']}", json={"location": loc((38.7200, -9.1400))}), "own location")
        nearby(3, ["office", "alfama"])
        ok(c.patch(f"/contacts/{ids['office']}", json={"location": None}), "clear own location")
        nearby(3, ["alfama"])
    s.check("nearby_follows_company_and_own_location", follows_company)

    def territories():
        ids = s.need("ids")
        t = ok(c.post(f"/agencies/{ag}/territories", json={"name": "Lisbon centre", "area": AREA}), "territory")
        tid = get_id(t)
        same_set(listing(c, "/contacts", {"agencyId": ag, "territory": tid}), {ids["alfama"], ids["belem"]}, "in territory")
        closed = ok(c.post(f"/agencies/{ag}/territories", json={"name": "Closed", "area": AREA + [AREA[0]]}), "closed ring")
        same_set(listing(c, "/contacts", {"agencyId": ag, "territory": get_id(closed)}), {ids["alfama"], ids["belem"]},
                 "closed ring")
        assert tid in {get_id(x) for x in listing(c, f"/agencies/{ag}/territories")}, "territory not listed"
        status(c.delete(f"/territories/{get_id(closed)}"), 204, "DELETE territory")
        assert get_id(closed) not in {get_id(x) for x in listing(c, f"/agencies/{ag}/territories")}, "deleted territory"
        st["territory"] = tid
    s.check("contacts_in_territory", territories)

    def bad_territories():
        for what, area in (("two corners", AREA[:2]), ("lat out of range", [[0, 95], [1, 1], [2, 2]]),
                           ("not pairs", [[1, 2, 3], [1, 1], [2, 2]]), ("no area", None)):
            rejected(c.post(f"/agencies/{ag}/territories", json={"name": "Bad", "area": area}), what)
    s.check("rejects_invalid_territories", bad_territories)

    def summary():
        ids, tid = s.need("ids"), s.need("territory")
        mk(c, "/deals", {"agencyId": ag, "contactId": ids["alfama"], "title": "Venue", "value": 1200, "stage": "lead"}, "deal")
        mk(c, "/deals", {"agencyId": ag, "contactId": ids["belem"], "title": "Gala", "value": 800, "stage": "won"}, "deal")
        mk(c, "/deals", {"agencyId": ag, "contactId": ids["cascais"], "title": "Far", "value": 5000, "stage": "lead"}, "deal")
        got = get(c, f"/territories/{tid}/summary")
        assert got.get("contacts") == 2, f"summary contacts {got.get('contacts')!r}, expected 2"
        num(got.get("openPipeline"), 1200, "summary openPipeline")
        ok(c.patch(f"/contacts/{ids['cascais']}", json={"location": loc((38.7100, -9.1500))}), "move into the area")
        got = get(c, f"/territories/{tid}/summary")
        assert got.get("contacts") == 3, f"after a move: contacts {got.get('contacts')!r}, expected 3"
        num(got.get("openPipeline"), 6200, "after a move: openPipeline")
    s.check("territory_summary", summary)
    return s.results
