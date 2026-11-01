"""Checks introduced by the `delivery` phase: delivery zones, pickup points and shipping fees."""
from __future__ import annotations

import math

import httpx

from harness.suite import Suite, get, get_id, listing, mk, ok, rejected, same_set, status
from scenarios.shop.checks.helpers import (by_product, expect_amount, fresh_shop, items_of, order_body, place, product,
                                           stock_is)

CENTRE = [[-9.20, 38.69], [-9.10, 38.69], [-9.10, 38.75], [-9.20, 38.75]]
REGION = [[-9.50, 38.60], [-9.00, 38.60], [-9.00, 38.90], [-9.50, 38.90]]
HERE = {"lat": 38.7223, "lng": -9.1393}


def km(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def v11(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v11")
    st = s.state
    sh = fresh_shop(c, "lisbon-delivery")

    def zones():
        for what, body in (("two corners", {"name": "x", "area": CENTRE[:2], "fee": 1}),
                           ("negative fee", {"name": "x", "area": CENTRE, "fee": -1}),
                           ("bad freeFrom", {"name": "x", "area": CENTRE, "fee": 1, "freeFrom": "lots"}),
                           ("lat out of range", {"name": "x", "area": [[0, 95], [1, 1], [2, 2]], "fee": 1})):
            rejected(c.post(f"/shops/{sh}/delivery-zones", json=body), what)
        centre = mk(c, f"/shops/{sh}/delivery-zones", {"name": "Centre", "area": CENTRE, "fee": 4.5, "freeFrom": 60}, "zone")
        region = mk(c, f"/shops/{sh}/delivery-zones", {"name": "Region", "area": REGION + [REGION[0]], "fee": 9}, "zone")
        same_set(listing(c, f"/shops/{sh}/delivery-zones"), {centre, region}, "zones")
        pid = mk(c, "/products", product(sh, "DLV-1", "Tote", "bags", 20, 100), "product")
        ok(c.post(f"/shops/{sh}/discounts", json={"code": "FIVE", "type": "fixed", "value": 5}), "discount")
        st.update(centre=centre, region=region, pid=pid)
    s.check("delivery_zones", zones)

    def pickups():
        points = {"alfama": {"lat": 38.7139, "lng": -9.1335}, "cascais": {"lat": 38.6979, "lng": -9.4215},
                  "north": {"lat": 38.8000, "lng": -9.1300}}
        ids = {k: mk(c, f"/shops/{sh}/pickup-points", {"name": k, "location": loc}, "pickup point")
               for k, loc in points.items()}
        rejected(c.post(f"/shops/{sh}/pickup-points", json={"name": "x", "location": {"lat": 91, "lng": 0}}), "bad location")
        got = listing(c, f"/shops/{sh}/pickup-points", {"near": f"{HERE['lat']},{HERE['lng']}"})
        assert [get_id(x) for x in got] == [ids[k] for k in ("alfama", "north", "cascais")], "nearest first"
        for x, k in zip(got, ("alfama", "north", "cascais")):
            d = x.get("distanceKm")
            assert isinstance(d, (int, float)) and abs(d - round(km(HERE, points[k]), 1)) <= 0.15, f"{k}: distanceKm {d!r}"
        assert len(listing(c, f"/shops/{sh}/pickup-points", {"near": f"{HERE['lat']},{HERE['lng']}"}, limit=1)) == 1, "limit"
        st["pickups"] = ids
    s.check("pickup_points_nearest_first", pickups)

    def fee(qty: int, location: dict, want_fee: float, want_zone: str, **kw) -> dict:
        o = place(c, order_body(sh, "ana@example.pt", [by_product(s.need("pid"), qty)],
                                delivery={"type": "shipping", "location": location}, **kw))
        expect_amount(o.get("shippingFee"), want_fee, f"{qty} x 20 to {location}: shippingFee")
        assert (o.get("delivery") or {}).get("zoneId") == s.need(want_zone), f"zoneId {(o.get('delivery') or {}).get('zoneId')}"
        return o

    def shipping():
        o = fee(1, {"lat": 38.72, "lng": -9.14}, 4.5, "centre")
        expect_amount(o.get("total"), 24.5, "total with the fee")
        fee(3, {"lat": 38.72, "lng": -9.14}, 0, "centre")
        o = fee(3, {"lat": 38.72, "lng": -9.14}, 4.5, "centre", discountCode="FIVE")
        expect_amount(o.get("total"), 59.5, "60 - 5 + 4.5")
        fee(1, {"lat": 38.70, "lng": -9.40}, 9, "region")
        st["shipped"] = get_id(o)
    s.check("shipping_fee_by_zone", shipping)

    def outside():
        pid = s.need("pid")
        before = get(c, f"/products/{pid}").get("stock")
        rejected(c.post("/orders", json=order_body(sh, "ana@example.pt", [by_product(pid)],
                                                   delivery={"type": "shipping", "location": {"lat": 40.0, "lng": -8.0}})),
                 "address outside every zone")
        rejected(c.post("/orders", json=order_body(sh, "ana@example.pt", [by_product(pid)],
                                                   delivery={"type": "drone"})), "unknown delivery type")
        stock_is(c, pid, before, " after rejected orders")
    s.check("rejects_addresses_outside_zones", outside)

    def pickup():
        pid, ids = s.need("pid"), s.need("pickups")
        o = place(c, order_body(sh, "bo@example.pt", [by_product(pid)], delivery={"type": "pickup",
                                                                                 "pickupPointId": ids["north"]}))
        expect_amount(o.get("shippingFee"), 0, "pickup fee")
        expect_amount(o.get("total"), 20, "pickup total")
        same_set(listing(c, "/orders", {"shopId": sh, "pickupPointId": ids["north"]}), {get_id(o)}, "pickupPointId filter")
        other = fresh_shop(c, "lisbon-delivery-other")
        foreign = mk(c, f"/shops/{other}/pickup-points", {"name": "x", "location": HERE}, "other shop's point")
        rejected(c.post("/orders", json=order_body(sh, "bo@example.pt", [by_product(pid)],
                                                   delivery={"type": "pickup", "pickupPointId": foreign})),
                 "another shop's pickup point")
        plain = place(c, order_body(sh, "bo@example.pt", [by_product(pid)]))
        expect_amount(plain.get("shippingFee") or 0, 0, "no delivery: shippingFee")
        expect_amount(plain.get("total"), 20, "no delivery: total")
    s.check("pickup_and_no_delivery", pickup)

    def returns():
        o = get(c, f"/orders/{s.need('shipped')}")
        items = [{"lineId": get_id(x), "quantity": x.get("quantity")} for x in items_of(o)]
        r = ok(c.post(f"/orders/{s.need('shipped')}/returns", json={"items": items}), "full return")
        expect_amount(r.get("refund"), 55, "refund without the shipping fee")
    s.check("returns_never_refund_shipping", returns)

    def deleted_zone():
        status(c.delete(f"/delivery-zones/{s.need('centre')}"), 204, "DELETE zone")
        fee(1, {"lat": 38.72, "lng": -9.14}, 9, "region")
    s.check("deleting_a_zone", deleted_zone)
    return s.results
