"""Checks introduced by the `returns` phase (5: returns)."""
from __future__ import annotations

from typing import Any

import httpx

from harness.suite import (Suite, as_list, created, first_mismatches, get, get_id, listing, mk, ok, rejected, same_set,
                           status)
from scenarios.shop.checks.helpers import (burst, by_product, expect_amount, fresh_shop, items_of, num, order_body,
                                           place, product, seed_orders, stock_is, wins)
from scenarios.shop.common import money_eq


def v5(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v5")
    st = s.state
    sh = fresh_shop(c, "leiria-linen")

    def line_ids(oid: str) -> dict[str, str]:
        return {x.get("sku"): get_id(x) for x in items_of(get(c, f"/orders/{oid}"))}

    def setup():
        st["p"] = {k: mk(c, "/products", product(sh, k, f"Item {k}", "linen", price, 20), f"product {k}")
                   for k, price in (("A", 30), ("B", 12.5), ("C", 10), ("D", 18))}
        st["p"]["P"] = mk(c, "/products", product(sh, "P", "Monogram towel", "linen", 20, 5, personalization=[
            {"key": "initials", "label": "Initials", "type": "text", "required": True}]), "personalised product")
        for code, body in (("TEN", {"type": "percent", "value": 10}), ("ONE", {"type": "fixed", "value": 1})):
            ok(c.post(f"/shops/{sh}/discounts", json={"code": code, **body}), f"code {code}")
        p = st["p"]
        st["o1"] = get_id(place(c, order_body(sh, "a@example.pt", [by_product(p["A"], 3), by_product(p["B"], 2)],
                                              discountCode="TEN")))
        st["o2"] = get_id(place(c, order_body(sh, "b@example.pt", [by_product(p["A"]),
                                                                   by_product(p["P"], personalization={"initials": "LU"})])))
        st["o3"] = get_id(place(c, order_body(sh, "c@example.pt", [by_product(p["C"], 3)], discountCode="ONE")))
        st["o4"] = get_id(place(c, order_body(sh, "d@example.pt", [by_product(p["B"])])))
        ok(c.post(f"/orders/{st['o4']}/cancel"), "cancel")
    s.check("create_orders", setup)

    def ids():
        got = items_of(get(c, f"/orders/{s.need('o1')}"))
        lids = [get_id(x) for x in got]
        assert all(lids) and len(set(lids)) == len(lids), f"line ids {lids}"
        assert lids == [get_id(x) for x in items_of(get(c, f"/orders/{s.need('o1')}"))], "line ids change between reads"
        assert all(x.get("returnedQuantity") in (0, None) for x in got), "returnedQuantity not 0"
        st["l1"], st["l2"], st["l3"] = line_ids(st["o1"]), line_ids(st["o2"]), line_ids(st["o3"])
    s.check("lines_have_ids", ids)

    def ret(oid: str, *items: tuple[str, int], reason: str | None = None) -> httpx.Response:
        body: dict[str, Any] = {"items": [{"lineId": lid, "quantity": q} for lid, q in items]}
        if reason:
            body["reason"] = reason
        return c.post(f"/orders/{oid}/returns", json=body)

    def partial():
        got = created(ret(s.need("o1"), (s.need("l1")["A"], 1), reason="arrived chipped"), "return")
        expect_amount(got.get("refund"), 27, "refund (a third of 90 − 9.00 discount)")
        assert got.get("reason") == "arrived chipped" and got.get("orderId") == s.need("o1"), \
            f"reason {got.get('reason')!r}, orderId {got.get('orderId')!r}"
        items = got.get("items") or []
        assert len(items) == 1 and items[0].get("lineId") == s.need("l1")["A"] and items[0].get("quantity") == 1, f"items {items}"
        expect_amount(items[0].get("refund"), 27, "item refund")
    s.check("return_part_of_a_line", partial)

    def order_after():
        got = get(c, f"/orders/{s.need('o1')}")
        assert got.get("status") == "partially_returned", f"status {got.get('status')!r}"
        expect_amount(got.get("refunded"), 27, "refunded")
        returned = {x.get("sku"): x.get("returnedQuantity") or 0 for x in items_of(got)}
        assert returned == {"A": 1, "B": 0}, f"returnedQuantity {returned}"
        stock_is(c, s.need("p")["A"], 17)
    s.check("order_after_partial_return", order_after)

    def rounding():
        o3, lid = s.need("o3"), s.need("l3")["C"]
        refunds = [created(ret(o3, (lid, 1)), f"return {i + 1}").get("refund") for i in range(3)]
        assert all(money_eq(g, w) for g, w in zip(refunds, [9.67, 9.67, 9.66])), \
            f"refunds {refunds}, expected [9.67, 9.67, 9.66] (29 paid for 3 units)"
        got = get(c, f"/orders/{o3}")
        assert got.get("status") == "returned", f"status {got.get('status')!r}"
        expect_amount(got.get("refunded"), 29, "refunded")
    s.check("last_return_refunds_the_rest", rounding)

    invalid = {
        "zero_quantity": lambda: ret(s.need("o1"), (s.need("l1")["B"], 0)),
        "unknown_line": lambda: ret(s.need("o1"), ("ffffffffffffffffffffffff", 1)),
        "line_of_other_order": lambda: ret(s.need("o1"), (s.need("l2")["A"], 1)),
        "same_line_twice": lambda: ret(s.need("o1"), (s.need("l1")["B"], 1), (s.need("l1")["B"], 1)),
        "personalised_line": lambda: ret(s.need("o2"), (s.need("l2")["P"], 1)),
        "no_items": lambda: ret(s.need("o1")),
    }
    for name, call in invalid.items():
        s.check(f"rejects_return_{name}", lambda call=call, name=name: rejected(call(), name))
    s.check("rejects_returning_more_than_left", lambda: status(ret(s.need("o1"), (s.need("l1")["A"], 3)), 409,
                                                               "3 of a line with 2 left"))
    s.check("rejects_return_of_cancelled_order", lambda: status(ret(s.need("o4"), (line_ids(s.need("o4"))["B"], 1)), 409,
                                                                "return of a cancelled order"))

    def unchanged():
        got = get(c, f"/orders/{s.need('o1')}")
        expect_amount(got.get("refunded"), 27, "refunded after rejected returns")
        assert len(as_list(get(c, f"/orders/{s.need('o1')}/returns"))) == 1, "a rejected return was stored"
        stock_is(c, s.need("p")["A"], 17)
        stock_is(c, s.need("p")["P"], 4)
    s.check("rejected_returns_change_nothing", unchanged)

    def full():
        l1 = s.need("l1")
        got = created(ret(s.need("o1"), (l1["A"], 2), (l1["B"], 2)), "return the rest")
        refunds = {x.get("lineId"): x.get("refund") for x in got.get("items") or []}
        expect_amount(refunds.get(l1["A"]), 54, "refund of A (81 paid − 27 refunded)")
        expect_amount(refunds.get(l1["B"]), 22.5, "refund of B (25 − 2.50 discount)")
        expect_amount(got.get("refund"), 76.5, "refund")
        order = get(c, f"/orders/{s.need('o1')}")
        assert order.get("status") == "returned", f"status {order.get('status')!r}"
        expect_amount(order.get("refunded"), 103.5, "refunded")
        stock_is(c, s.need("p")["B"], 20 - 2 + 2)
    s.check("full_return", full)

    s.check("returned_order_cannot_be_cancelled", lambda: status(c.post(f"/orders/{s.need('o1')}/cancel"), 409,
                                                                 "cancelling a returned order"))

    def partial_not_cancelled():
        created(ret(s.need("o2"), (s.need("l2")["A"], 1)), "return")
        assert get(c, f"/orders/{s.need('o2')}").get("status") == "partially_returned", "status not partially_returned"
        status(c.post(f"/orders/{s.need('o2')}/cancel"), 409, "cancelling a partially returned order")
    s.check("partially_returned_order_cannot_be_cancelled", partial_not_cancelled)

    def listed():
        got = as_list(get(c, f"/orders/{s.need('o1')}/returns"))
        assert [round(float(r.get("refund") or 0), 2) for r in got] == [27.0, 76.5], \
            f"returns {[r.get('refund') for r in got]}, expected refunds [27, 76.5] oldest first"
    s.check("list_returns", listed)

    def code_use():
        uses = get(c, f"/shops/{sh}/discounts/TEN").get("uses")
        assert num(uses) == 1, f"TEN uses {uses!r} after the order was returned, expected 1"
    s.check("return_keeps_code_use", code_use)
    s.check("filter_returned", lambda: same_set(listing(c, "/orders", {"shopId": sh, "status": "returned"}),
                                                {s.need("o1"), s.need("o3")}, "status=returned"))
    s.check("filter_partially_returned", lambda: same_set(listing(c, "/orders", {"shopId": sh, "status": "partially_returned"}),
                                                          {s.need("o2")}, "status=partially_returned"))

    def concurrent():
        oid = get_id(place(c, order_body(sh, "e@example.pt", [by_product(s.need("p")["D"], 2)])))
        lid = line_ids(oid)["D"]
        won = wins(burst(6, lambda i: ret(oid, (lid, 1))))
        assert len(won) == 2, f"{len(won)} of 6 returns of 1 unit succeeded for a line of 2"
        got = get(c, f"/orders/{oid}")
        assert items_of(got)[0].get("returnedQuantity") == 2, f"returnedQuantity {items_of(got)[0].get('returnedQuantity')!r}"
        expect_amount(got.get("refunded"), 36, "refunded")
        stock_is(c, s.need("p")["D"], 20)
    s.check("concurrent_returns_never_exceed_order", concurrent)

    if seed:
        def seeded_ids():
            got = seed_orders(c, seed, st)
            first_mismatches(seed["orders"], lambda o: got[o["id"]], lambda o, g: (
                (len({get_id(x) for x in items_of(g)} - {None}) != len(items_of(g)) and "lines without distinct ids")
                or (o["key"] != "late_return" and any(x.get("returnedQuantity") not in (0, None) for x in items_of(g))
                    and "returnedQuantity not 0")))
        s.check("seeded_lines_have_ids", seeded_ids)

        def seeded_return():
            o = next(x for x in seed["orders"] if x["key"] == "late_return")
            line = items_of(get(c, f"/orders/{o['id']}"))[0]
            if not line.get("returnedQuantity"):
                got = created(ret(o["id"], (get_id(line), 1), reason="changed my mind"), "return of an existing order")
                expect_amount(got.get("refund"), o["lines"][0]["unitPrice"], "refund")
            after = get(c, f"/orders/{o['id']}")
            assert items_of(after)[0].get("returnedQuantity") == 1, f"returnedQuantity {items_of(after)[0].get('returnedQuantity')!r}"
            assert after.get("status") == "partially_returned", f"status {after.get('status')!r}"
            expect_amount(after.get("refunded"), o["lines"][0]["unitPrice"], "refunded")
        s.check("seeded_return_existing_order", seeded_return)

    return s.results
