"""Checks introduced by the `order_numbers` phase (improvement: order numbers)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, get, get_id, listing, mk, ok, rejected, same_set, status
from scenarios.shop.checks.helpers import burst, by_product, fresh_shop, num, order_body, place, product, wins
from scenarios.shop.common import parse_time


def i1(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("i1")
    st = s.state
    sh, other = fresh_shop(c, "cork-candles"), fresh_shop(c, "galway-glass")

    def numbers_of(ids: list[str]) -> list[int | None]:
        return [num(get(c, f"/orders/{oid}").get("number")) for oid in ids]

    def fixtures():
        st["p"] = mk(c, "/products", product(sh, "CANDLE-1", "Beeswax candle", "candles", 14, 100), "product")
        st["o"] = [get_id(place(c, order_body(sh, f"c{i}@example.ie", [by_product(st["p"])]))) for i in range(3)]
    s.check("create_orders", fixtures)

    def sequential():
        got = numbers_of(s.need("o"))
        assert got == [1001, 1002, 1003], f"numbers {got}, expected [1001, 1002, 1003]"
    s.check("numbers_start_at_1001", sequential)

    def everywhere():
        listed = {get_id(o): num(o.get("number")) for o in listing(c, "/orders", {"shopId": sh})}
        assert [listed.get(oid) for oid in s.need("o")] == [1001, 1002, 1003], f"numbers in the list {listed}"
        body = place(c, order_body(sh, "c3@example.ie", [by_product(s.need("p"))]))
        assert num(body.get("number")) == 1004, f"number {body.get('number')!r} in the POST response, expected 1004"
        st["o"].append(get_id(body))
    s.check("number_in_every_response", everywhere)

    def rejected_orders():
        status(c.post("/orders", json=order_body(sh, "c@example.ie", [by_product(s.need("p"), 1000)])), 409, "too many")
        rejected(c.post("/orders", json=order_body(sh, "not-an-email", [by_product(s.need("p"))])), "invalid email")
        oid = get_id(place(c, order_body(sh, "c5@example.ie", [by_product(s.need("p"))])))
        st["o"].append(oid)
        assert numbers_of([oid]) == [1005], f"number {numbers_of([oid])[0]} after two rejected orders, expected 1005"
    s.check("rejected_orders_take_no_number", rejected_orders)

    def cancelled():
        o2 = s.need("o")[1]
        ok(c.post(f"/orders/{o2}/cancel"), "cancel")
        assert numbers_of([o2]) == [1002], "cancelled order lost its number"
        oid = get_id(place(c, order_body(sh, "c6@example.ie", [by_product(s.need("p"))])))
        st["o"].append(oid)
        assert numbers_of([oid]) == [1006], f"number {numbers_of([oid])[0]}, expected 1006"
    s.check("cancelled_order_keeps_number", cancelled)

    s.check("filter_by_number", lambda: same_set(listing(c, "/orders", {"shopId": sh, "number": 1003}),
                                                 {s.need("o")[2]}, "number=1003"))

    def per_shop():
        pid = mk(c, "/products", product(other, "GLASS-1", "Tumbler", "glassware", 22, 10), "product")
        body = place(c, order_body(other, "g@example.ie", [by_product(pid)]))
        assert num(body.get("number")) == 1001, f"first order of another shop has number {body.get('number')!r}"
    s.check("numbers_per_shop", per_shop)

    def concurrent():
        pid = mk(c, "/products", product(sh, "CANDLE-DROP", "Drop candle", "candles", 30, 6), "drop product")
        before = max(num(o.get("number")) or 0 for o in listing(c, "/orders", {"shopId": sh}))
        won = wins(burst(10, lambda i: c.post("/orders", json=order_body(sh, f"d{i}@example.ie", [by_product(pid)]))))
        got = sorted(num(o.get("number")) or 0 for o in won)
        assert len(won) == 6, f"{len(won)} of 10 orders for 6 in stock succeeded"
        assert got == list(range(before + 1, before + 7)), f"numbers {got} after {before}"
    s.check("concurrent_orders_numbered_without_gaps", concurrent)

    if seed:
        def seeded_numbers():
            for shop in sorted({o["shop"] for o in seed["orders"]}):
                orders = listing(c, "/orders", {"shopId": shop})
                ranked = sorted(orders, key=lambda o: num(o.get("number")) or 0)
                got = [num(o.get("number")) for o in ranked]
                assert got == list(range(1001, 1001 + len(orders))), f"{shop}: numbers {got[:4]}…{got[-2:]}, expected 1001–{1000 + len(orders)}"
                for a, b in zip(ranked, ranked[1:]):
                    ta, tb = parse_time(a.get("createdAt")), parse_time(b.get("createdAt"))
                    assert ta and tb and ta <= tb, f"{shop}: order {a.get('number')} was placed after {b.get('number')}"
        s.check("seeded_orders_numbered_by_time", seeded_numbers)

        def seeded_filter():
            for o in seed["orders"][::25]:
                n = get(c, f"/orders/{o['id']}").get("number")
                same_set(listing(c, "/orders", {"shopId": o["shop"], "number": n}), {o["id"]}, f"{o['shop']} number={n}")
        s.check("seeded_filter_by_number", seeded_filter)

    return s.results
