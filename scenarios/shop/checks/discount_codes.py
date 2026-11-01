"""Checks introduced by the `discount_codes` phase (4: discount codes)."""
from __future__ import annotations

from typing import Any

import httpx

from harness.suite import Suite, as_list, first_mismatches, get, get_id, listing, mk, ok, rejected, same_set, status
from scenarios.shop.checks.helpers import (burst, by_product, expect_amount, fields_match, fresh_shop, iso, items_of,
                                           num, order_body, place, product, seed_orders, stock_is, wins)
from scenarios.shop.common import money_eq


def v4(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v4")
    st = s.state
    sh = fresh_shop(c, "faro-surf")
    path = f"/shops/{sh}/discounts"
    codes = {"WINTER10": {"type": "percent", "value": 10},
             "FIVE": {"type": "fixed", "value": 5, "minSubtotal": 30},
             "ONCE": {"type": "percent", "value": 15, "oncePerCustomer": True},
             "LIM2": {"type": "fixed", "value": 2, "maxUses": 2},
             "CAP": {"type": "fixed", "value": 100},
             "FUTURE": {"type": "percent", "value": 20, "startsAt": iso(365)},
             "PAST": {"type": "percent", "value": 20, "startsAt": iso(-30), "endsAt": iso(-1)},
             "OFF": {"type": "percent", "value": 5},
             "DROP3": {"type": "fixed", "value": 1, "maxUses": 3},
             "SOLO": {"type": "percent", "value": 5, "oncePerCustomer": True}}

    def setup():
        st["p"] = {k: mk(c, "/products", product(sh, k, f"Item {k}", "gear", price, 200), f"product {k}")
                   for k, price in (("P40", 40), ("P25", 25), ("P999", 9.99))}
        for code, body in codes.items():
            got = ok(c.post(path, json={"code": code, **body}), f"code {code}")
            assert got.get("active") is True and num(got.get("uses")) == 0, \
                f"{code}: active {got.get('active')!r}, uses {got.get('uses')!r}"
        st["codes"] = True
    s.check("create_codes", setup)

    def read_back():
        s.need("codes")
        got = get(c, f"{path}/winter10")
        bad = fields_match({"code": "WINTER10", "type": "percent", "value": 10, "active": True}, got)
        assert not bad and num(got.get("uses")) == 0, f"differs in {bad or ['uses ' + repr(got.get('uses'))]}"
        listed = {d.get("code") for d in as_list(get(c, path))}
        assert listed == set(codes), f"listed codes {sorted(map(str, listed))}"
    s.check("code_read_back", read_back)

    invalid = {
        "percent_zero": {"code": "Z0", "type": "percent", "value": 0},
        "percent_over_100": {"code": "Z1", "type": "percent", "value": 101},
        "fixed_three_decimals": {"code": "Z2", "type": "fixed", "value": 2.555},
        "invalid_characters": {"code": "WIN TER", "type": "percent", "value": 10},
        "max_uses_zero": {"code": "Z3", "type": "fixed", "value": 1, "maxUses": 0},
        "unknown_type": {"code": "Z4", "type": "bogo", "value": 1},
        "invalid_starts_at": {"code": "Z5", "type": "percent", "value": 10, "startsAt": "tomorrow"},
    }
    for name, body in invalid.items():
        s.check(f"rejects_code_{name}", lambda body=body, name=name: rejected(c.post(path, json=body), name))
    s.check("rejects_duplicate_code_ignoring_case", lambda: status(c.post(path, json={"code": "winter10", "type": "fixed",
                                                                                      "value": 1}), 409, "duplicate code"))

    def deactivate():
        got = ok(c.patch(f"{path}/OFF", json={"active": False}), "PATCH active")
        assert got.get("active") is False and get(c, f"{path}/OFF").get("active") is False, "code still active"
    s.check("deactivate_code", deactivate)

    def p(k: str, qty: int = 1) -> dict:
        return by_product(s.need("p")[k], qty)

    def check_order(oid: str, subtotal: float, code: str | None, amount: float, shares: list[float]) -> None:
        got = get(c, f"/orders/{oid}")
        expect_amount(got.get("subtotal"), subtotal, "subtotal")
        d = got.get("discount")
        if code:
            assert isinstance(d, dict) and d.get("code") == code, f"discount {d!r}, expected code {code}"
            expect_amount(d.get("amount"), amount, "discount amount")
        else:
            assert d is None or (isinstance(d, dict) and money_eq(d.get("amount"), 0)), f"discount {d!r} without a code"
        expect_amount(got.get("total"), round(subtotal - amount, 2), "total")
        got_shares = [x.get("discount") if x.get("discount") is not None else 0 for x in items_of(got)]
        assert len(got_shares) == len(shares) and all(money_eq(g, w) for g, w in zip(got_shares, shares)), \
            f"line discounts {got_shares}, expected {shares}"

    def percent():
        s.need("codes")
        st["o1"] = get_id(place(c, order_body(sh, "ana@example.pt", [p("P40", 2), p("P999")], discountCode="winter10")))
        check_order(st["o1"], 89.99, "WINTER10", 9.00, [8.00, 1.00])
    s.check("percent_code", percent)

    def three_lines():
        s.need("codes")
        st["o3"] = get_id(place(c, order_body(sh, "bo@example.pt", [p("P40"), p("P25"), p("P999")], discountCode="WINTER10")))
        check_order(st["o3"], 74.99, "WINTER10", 7.50, [4.00, 2.50, 1.00])
    s.check("line_shares_add_up", three_lines)

    def fixed():
        s.need("codes")
        st["o2"] = get_id(place(c, order_body(sh, "cy@example.pt", [p("P25", 2)], discountCode="FIVE")))
        check_order(st["o2"], 50, "FIVE", 5, [5])
    s.check("fixed_code", fixed)

    def capped():
        s.need("codes")
        oid = get_id(place(c, order_body(sh, "di@example.pt", [p("P999")], discountCode="CAP")))
        check_order(oid, 9.99, "CAP", 9.99, [9.99])
    s.check("fixed_code_at_most_subtotal", capped)

    def no_code():
        oid = get_id(place(c, order_body(sh, "ed@example.pt", [p("P25"), p("P999")])))
        check_order(oid, 34.99, None, 0, [0, 0])
    s.check("order_without_code", no_code)

    order_invalid = {
        "unknown_code": lambda: order_body(sh, "rex@example.pt", [p("P40")], discountCode="NOPE"),
        "inactive_code": lambda: order_body(sh, "rex@example.pt", [p("P40")], discountCode="OFF"),
        "code_not_started": lambda: order_body(sh, "rex@example.pt", [p("P40")], discountCode="FUTURE"),
        "code_expired": lambda: order_body(sh, "rex@example.pt", [p("P40")], discountCode="PAST"),
        "below_min_subtotal": lambda: order_body(sh, "rex@example.pt", [p("P999")], discountCode="FIVE"),
    }
    for name, body in order_invalid.items():
        s.check(f"rejects_order_{name}", lambda body=body, name=name: rejected(c.post("/orders", json=body()), name))
    def code_rejections():
        same_set(listing(c, "/orders", {"shopId": sh, "customerEmail": "rex@example.pt"}), set(), "rejected orders")
        stock_is(c, s.need("p")["P40"], 197)
    s.check("code_rejections_store_nothing", code_rejections)

    def once():
        s.need("codes")
        st["eva"] = get_id(place(c, order_body(sh, "Eva@Example.com", [p("P40")], discountCode="ONCE")))
        rejected(c.post("/orders", json=order_body(sh, "eva@example.COM", [p("P25")], discountCode="once")),
                 "second use by the same customer")
        place(c, order_body(sh, "max@example.com", [p("P25")], discountCode="ONCE"), "another customer")
    s.check("once_per_customer", once)

    def once_after_cancel():
        ok(c.post(f"/orders/{s.need('eva')}/cancel"), "cancel")
        place(c, order_body(sh, "eva@example.com", [p("P25")], discountCode="ONCE"), "use after cancelling")
    s.check("cancelling_frees_once_per_customer", once_after_cancel)

    def max_uses():
        s.need("codes")
        first = get_id(place(c, order_body(sh, "u1@example.com", [p("P25")], discountCode="LIM2")))
        place(c, order_body(sh, "u2@example.com", [p("P25")], discountCode="LIM2"))
        rejected(c.post("/orders", json=order_body(sh, "u3@example.com", [p("P25")], discountCode="LIM2")), "third use")
        assert num(get(c, f"{path}/LIM2").get("uses")) == 2, "uses not 2"
        ok(c.post(f"/orders/{first}/cancel"), "cancel")
        assert num(get(c, f"{path}/LIM2").get("uses")) == 1, "uses not 1 after cancelling"
        place(c, order_body(sh, "u3@example.com", [p("P25")], discountCode="LIM2"), "use after a cancellation")
    s.check("max_uses", max_uses)

    def uses_count():
        uses = get(c, f"{path}/WINTER10").get("uses")
        assert num(uses) == 2, f"WINTER10 uses {uses!r}, expected 2"
    s.check("uses_count", uses_count)
    s.check("filter_orders_by_code", lambda: same_set(listing(c, "/orders", {"shopId": sh, "discountCode": "winter10"}),
                                                      {s.need("o1"), s.need("o3")}, "discountCode=winter10"))

    def concurrent_max():
        s.need("codes")
        won = wins(burst(10, lambda i: c.post("/orders", json=order_body(sh, f"drop{i}@example.com", [p("P40")],
                                                                         discountCode="DROP3"))),
                   loser=lambda code: 400 <= code < 500)
        assert len(won) == 3, f"{len(won)} of 10 orders used a code with maxUses 3"
        assert num(get(c, f"{path}/DROP3").get("uses")) == 3, "uses not 3"
    s.check("concurrent_orders_respect_max_uses", concurrent_max)

    def concurrent_once():
        s.need("codes")
        won = wins(burst(6, lambda i: c.post("/orders", json=order_body(sh, "solo@example.com", [p("P25")],
                                                                        discountCode="SOLO"))),
                   loser=lambda code: 400 <= code < 500)
        assert len(won) == 1, f"{len(won)} of 6 simultaneous orders by one customer used a once-per-customer code"
    s.check("concurrent_orders_respect_once_per_customer", concurrent_once)

    if seed:
        def seeded_orders():
            got = seed_orders(c, seed, st)

            def problem(o: dict, g: dict) -> Any:
                if not money_eq(g.get("subtotal"), o["total"]):
                    return f"subtotal {g.get('subtotal')!r}, expected {o['total']}"
                d = g.get("discount")
                if d is not None and not (isinstance(d, dict) and money_eq(d.get("amount"), 0)):
                    return f"discount {d!r}"
                return next((f"items[{i}].discount {x.get('discount')!r}" for i, x in enumerate(items_of(g))
                             if x.get("discount") is not None and not money_eq(x.get("discount"), 0)), None)
            first_mismatches(seed["orders"], lambda o: got[o["id"]], problem)
        s.check("seeded_orders_without_discount", seeded_orders)

    return s.results
