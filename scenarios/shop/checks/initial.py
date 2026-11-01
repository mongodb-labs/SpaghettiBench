"""Checks introduced by the `initial` phase (initial build)."""
from __future__ import annotations

from typing import Any, Callable

import httpx

from harness.suite import (Suite, expect, first_mismatches, get, get_id, id_set, listing, mk, ok, rejected, same_set,
                           status, superset_matching)
from scenarios.shop.checks.helpers import (burst, by_product, expect_amount, fields_match, fresh_shop, items_of,
                                           line_problem, order_body, place, product, seed_orders, seed_status,
                                           seed_stock, stock_is, uid, wins)
from scenarios.shop.common import money_eq, parse_time


def v0(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v0")
    st = s.state
    sh, other = fresh_shop(c, "lisbon-ceramics"), fresh_shop(c, "oslo-prints")

    def products():
        st["pp"] = {
            "mug": product(sh, "MUG-1", "Speckled mug", "mugs", 24.5, 40),
            "plate": product(sh, "PLATE-1", "Dinner plate", "plates", 18, 10),
            "vase": product(sh, "VASE-1", "Bud vase", "vases", 89.99, 0),
            "cup": product(sh, "CUP-1", "Espresso cup", "mugs", 19.99, 25),
        }
        st["p"] = {k: mk(c, "/products", body, f"product {k}") for k, body in st["pp"].items()}
        st["foreign"] = mk(c, "/products", product(other, "OSLO-1", "Print", "prints", 30, 5), "other shop's product")
    s.check("create_products", products)

    def read_back():
        for k, body in s.need("pp").items():
            bad = fields_match(body, get(c, f"/products/{s.need('p')[k]}"))
            assert not bad, f"product {s.need('p')[k]}: differs in {bad}"
    s.check("product_read_back", read_back)

    def base() -> dict:
        return product(sh, f"REJ-{uid()}", "Rejected", "mugs", 10, 1)
    invalid = {
        "missing_sku": lambda: {k: v for k, v in base().items() if k != "sku"},
        "empty_name": lambda: {**base(), "name": ""},
        "missing_category": lambda: {k: v for k, v in base().items() if k != "category"},
        "negative_price": lambda: {**base(), "price": -1},
        "price_with_three_decimals": lambda: {**base(), "price": 10.005},
        "string_price": lambda: {**base(), "price": "10.50"},
        "fractional_stock": lambda: {**base(), "stock": 2.5},
        "negative_stock": lambda: {**base(), "stock": -1},
    }
    for name, body in invalid.items():
        s.check(f"rejects_product_{name}", lambda body=body, name=name: rejected(c.post("/products", json=body()), name))
    s.check("rejects_duplicate_sku", lambda: status(
        c.post("/products", json=product(sh, "MUG-1", "Another mug", "mugs", 5, 1)), 409, "duplicate sku"))
    s.check("same_sku_in_other_shop", lambda: mk(c, "/products", product(other, "MUG-1", "Oslo mug", "mugs", 12, 3),
                                                 "sku another shop also uses"))

    def not_stored():
        same_set(listing(c, "/products", {"shopId": sh}), set(s.need("p").values()), "products after rejections")
    s.check("invalid_products_not_stored", not_stored)

    def product_filter(params: dict, keys: list[str]) -> Callable[[], None]:
        def fn():
            p = s.need("p")
            same_set(listing(c, "/products", {"shopId": sh, **params}), {p[k] for k in keys}, str(params))
        return fn
    s.check("filter_category", product_filter({"category": "mugs"}, ["mug", "cup"]))
    s.check("filter_sku", product_filter({"sku": "PLATE-1"}, ["plate"]))
    s.check("filter_in_stock", product_filter({"inStock": "true"}, ["mug", "plate", "cup"]))
    s.check("filter_and", product_filter({"category": "vases", "inStock": "true"}, []))
    def own_shop():
        items = listing(c, "/products", {"shopId": other})
        assert s.need("foreign") in id_set(items), "other shop's product missing"
        assert not id_set(items) & set(s.need("p").values()), "another shop's products listed"
        assert all(i.get("shopId") == other for i in items), "products of another shop listed"
    s.check("products_only_own_shop", own_shop)
    s.check("limit_param", lambda: expect(len(listing(c, "/products", {"shopId": sh}, limit=2)) == 2,
                                          lambda: "limit=2 did not return 2 products"))

    def patch():
        pid = s.need("p")["plate"]
        ok(c.patch(f"/products/{pid}", json={"name": "Dinner plate – oat", "stock": 12}), "PATCH product")
        st["pp"]["plate"] = {**s.need("pp")["plate"], "name": "Dinner plate – oat", "stock": 12}
        bad = fields_match(st["pp"]["plate"], get(c, f"/products/{pid}"))
        assert not bad, f"differs in {bad}"
    s.check("patch_product", patch)

    def patch_sku():
        pid = s.need("p")["plate"]
        rejected(c.patch(f"/products/{pid}", json={"sku": "PLATE-9"}), "PATCH sku")
        assert get(c, f"/products/{pid}").get("sku") == "PLATE-1", "sku changed"
    s.check("patch_rejects_sku_change", patch_sku)

    def patch_invalid():
        pid = s.need("p")["plate"]
        rejected(c.patch(f"/products/{pid}", json={"price": -5}), "PATCH negative price")
        expect_amount(get(c, f"/products/{pid}").get("price"), 18, "price after rejected PATCH")
    s.check("patch_rejects_invalid", patch_invalid)

    def line(k: str, qty: int, price: float | None = None, name: str | None = None) -> dict:
        body = s.need("pp")[k]
        unit = body["price"] if price is None else price
        return {"productId": s.need("p")[k], "sku": body["sku"], "name": name or body["name"], "unitPrice": unit,
                "quantity": qty, "lineTotal": round(unit * qty, 2)}

    def orders():
        p = s.need("p")
        st["o1"] = get_id(place(c, order_body(sh, "Ines.Moreau@Example.com", [by_product(p["mug"], 2), by_product(p["plate"])])))
        st["o2"] = get_id(place(c, order_body(sh, "tom@example.com", [by_product(p["cup"], 3)])))
        st["o_foreign"] = get_id(place(c, order_body(other, "kari@example.no", [by_product(s.need("foreign"))])))
    s.check("create_orders", orders)

    def order_read_back():
        got = get(c, f"/orders/{s.need('o1')}")
        assert got.get("status") == "placed", f"status {got.get('status')!r}"
        assert str(got.get("customerEmail")).lower() == "ines.moreau@example.com", f"customerEmail {got.get('customerEmail')!r}"
        assert parse_time(got.get("createdAt")), f"createdAt {got.get('createdAt')!r} is not a timestamp"
        problem = line_problem([line("mug", 2), line("plate", 1)], got)
        assert not problem, problem
        expect_amount(got.get("total"), 67, "total")
    s.check("order_read_back", order_read_back)

    def rounding():
        got = get(c, f"/orders/{s.need('o2')}")
        expect_amount(items_of(got)[0].get("lineTotal"), 59.97, "lineTotal of 3 × 19.99")
        expect_amount(got.get("total"), 59.97, "total")
    s.check("order_amounts_to_the_cent", rounding)

    def stock_taken():
        p = s.need("p")
        stock_is(c, p["mug"], 38)
        stock_is(c, p["plate"], 11)
        stock_is(c, p["cup"], 22)
    s.check("order_takes_stock", stock_taken)

    def base_order() -> dict:
        return order_body(sh, "rex@example.com", [by_product(s.need("p")["mug"])])
    order_invalid = {
        "no_items": lambda: {**base_order(), "items": []},
        "zero_quantity": lambda: {**base_order(), "items": [by_product(s.need("p")["mug"], 0)]},
        "fractional_quantity": lambda: {**base_order(), "items": [by_product(s.need("p")["mug"], 1.5)]},  # type: ignore[arg-type]
        "product_of_other_shop": lambda: {**base_order(), "items": [by_product(s.need("foreign"))]},
        "same_product_twice": lambda: {**base_order(), "items": [by_product(s.need("p")["mug"]), by_product(s.need("p")["mug"])]},
        "invalid_email": lambda: {**base_order(), "customerEmail": "rex.example.com"},
        "missing_email": lambda: {k: v for k, v in base_order().items() if k != "customerEmail"},
    }
    for name, body in order_invalid.items():
        s.check(f"rejects_order_{name}", lambda body=body, name=name: rejected(c.post("/orders", json=body()), name))
    s.check("rejects_order_unknown_product", lambda: rejected(c.post("/orders", json={
        **base_order(), "items": [by_product("ffffffffffffffffffffffff")]}), "unknown product", allow_404=True))

    def rejections_store_nothing():
        same_set(listing(c, "/orders", {"shopId": sh}), {s.need("o1"), s.need("o2")}, "orders after rejections")
        stock_is(c, s.need("p")["mug"], 38)
    s.check("order_rejections_store_nothing", rejections_store_nothing)

    s.check("insufficient_stock_409", lambda: status(c.post("/orders", json=order_body(
        sh, "rex@example.com", [by_product(s.need("p")["vase"])])), 409, "product out of stock"))

    def all_or_nothing():
        p = s.need("p")
        status(c.post("/orders", json=order_body(sh, "rex@example.com", [by_product(p["mug"]), by_product(p["plate"], 100)])),
               409, "one item without enough stock")
        stock_is(c, p["mug"], 38, " (the item that had stock)")
        same_set(listing(c, "/orders", {"shopId": sh}), {s.need("o1"), s.need("o2")}, "orders")
    s.check("insufficient_stock_takes_nothing", all_or_nothing)

    def frozen():
        ok(c.patch(f"/products/{s.need('p')['mug']}", json={"price": 26, "name": "Speckled mug (large)"}), "PATCH product")
        got = get(c, f"/orders/{s.need('o1')}")
        problem = line_problem([line("mug", 2), line("plate", 1)], got)
        assert not problem, f"order {s.need('o1')} after the product changed: {problem}"
        expect_amount(got.get("total"), 67, "total")
    s.check("order_keeps_price_and_name_at_order_time", frozen)

    def current_price():
        st["o3"] = get_id(place(c, order_body(sh, "ines.moreau@example.com", [by_product(s.need("p")["mug"])])))
        problem = line_problem([line("mug", 1, 26, "Speckled mug (large)")], get(c, f"/orders/{st['o3']}"))
        assert not problem, problem
    s.check("new_order_uses_current_price", current_price)

    def order_filter(params: Callable[[], dict], keys: list[str]) -> Callable[[], None]:
        def fn():
            same_set(listing(c, "/orders", {"shopId": sh, **params()}), {s.need(k) for k in keys}, str(params()))
        return fn
    s.check("filter_orders_by_email_case_insensitive",
            order_filter(lambda: {"customerEmail": "INES.MOREAU@example.com"}, ["o1", "o3"]))
    s.check("filter_orders_by_product", order_filter(lambda: {"productId": s.need("p")["plate"]}, ["o1"]))
    s.check("filter_orders_by_status", order_filter(lambda: {"status": "placed"}, ["o1", "o2", "o3"]))
    s.check("orders_only_own_shop", lambda: same_set(listing(c, "/orders", {"shopId": other}), {s.need("o_foreign")},
                                                     "other shop's orders"))

    def cancel():
        got = ok(c.post(f"/orders/{s.need('o2')}/cancel"), "cancel")
        assert got.get("status") == "cancelled", f"status {got.get('status')!r} in the response"
        assert get(c, f"/orders/{s.need('o2')}").get("status") == "cancelled", "order not cancelled"
        stock_is(c, s.need("p")["cup"], 25, " after cancelling")
    s.check("cancel_returns_stock", cancel)

    def cancel_twice():
        status(c.post(f"/orders/{s.need('o2')}/cancel"), 409, "cancelling a cancelled order")
        stock_is(c, s.need("p")["cup"], 25, " after cancelling twice")
    s.check("cancel_twice_409", cancel_twice)
    s.check("filter_orders_cancelled", order_filter(lambda: {"status": "cancelled"}, ["o2"]))

    def concurrent():
        pid = mk(c, "/products", product(sh, "DROP-1", "Limited vase", "vases", 120, 5), "drop product")
        won = wins(burst(12, lambda i: c.post("/orders", json=order_body(sh, f"drop{i}@example.com", [by_product(pid)]))))
        assert len(won) == 5, f"{len(won)} of 12 orders for 5 in stock succeeded"
        stock_is(c, pid, 0)
        assert len(listing(c, "/orders", {"shopId": sh, "productId": pid})) == 5, "not 5 orders stored"
    s.check("concurrent_orders_never_oversell", concurrent)

    def concurrent_multi():
        a = mk(c, "/products", product(sh, "DROP-A", "Drop plate", "plates", 40, 4), "drop product A")
        b = mk(c, "/products", product(sh, "DROP-B", "Drop bowl", "bowls", 30, 4), "drop product B")
        bodies = [order_body(sh, f"multi{i}@example.com", [by_product(a, 1 + i % 2), by_product(b)]) for i in range(10)]
        rs = burst(10, lambda i: c.post("/orders", json=bodies[i]))
        won = wins(rs)
        took = [bodies[i] for i, r in enumerate(rs) if r.status_code in (200, 201)]
        assert won, "no order succeeded"
        took_a = sum(x["items"][0]["quantity"] for x in took)
        assert took_a <= 4 and len(took) <= 4, f"orders took {took_a} of A and {len(took)} of B, with 4 of each in stock"
        stock_is(c, a, 4 - took_a, " (A)")
        stock_is(c, b, 4 - len(took), " (B)")
    s.check("concurrent_multi_item_orders_consistent", concurrent_multi)

    if seed:
        def seeded_products():
            first_mismatches(seed["products"], lambda x: get(c, f"/products/{x['id']}"),
                             lambda x, got: fields_match(x["payload"], got))
        s.check("seeded_products_intact", seeded_products)

        def seeded_stock():
            want = seed_stock(c, seed, st)
            first_mismatches(seed["products"], lambda x: get(c, f"/products/{x['id']}"),
                             lambda x, got: got.get("stock") != want[x["id"]]
                             and f"stock {got.get('stock')!r}, expected {want[x['id']]}")
        s.check("seeded_stock_matches_orders", seeded_stock)

        def seeded_orders():
            got = seed_orders(c, seed, st)

            def problem(o: dict, g: dict) -> Any:
                if g.get("status") not in seed_status(o):
                    return f"status {g.get('status')!r}"
                if str(g.get("customerEmail")).lower() != o["payload"]["customerEmail"].lower():
                    return f"customerEmail {g.get('customerEmail')!r}"
                return line_problem(o["lines"], g) or (
                    not money_eq(g.get("total"), o["total"]) and f"total {g.get('total')!r}, expected {o['total']}")
            first_mismatches(seed["orders"], lambda o: got[o["id"]], problem)
        s.check("seeded_orders_intact", seeded_orders)

        def seeded_filter_email():
            emails = sorted({(o["shop"], o["payload"]["customerEmail"].lower()) for o in seed["orders"]})[::7]
            for shop, addr in emails:
                expected = {o["id"] for o in seed["orders"]
                            if o["shop"] == shop and o["payload"]["customerEmail"].lower() == addr}
                items = listing(c, "/orders", {"shopId": shop, "customerEmail": addr.upper()})
                superset_matching(items, expected, lambda i: str(i.get("customerEmail")).lower() == addr,
                                  f"customerEmail={addr.upper()}")
        s.check("seeded_filter_orders_by_email", seeded_filter_email)

        def seeded_filter_product():
            for p in seed["products"][::9]:
                expected = {o["id"] for o in seed["orders"] if any(x["productId"] == p["id"] for x in o["lines"])}
                items = listing(c, "/orders", {"shopId": p["shop"], "productId": p["id"]})
                superset_matching(items, expected, lambda i: any(x.get("productId") == p["id"] for x in items_of(i)),
                                  f"productId={p['id']}")
        s.check("seeded_filter_orders_by_product", seeded_filter_product)

        def seeded_filter_category():
            for shop, category in (("clay-and-kiln", "mugs"), ("northloom", "tops"), ("voltbox", "cables")):
                expected = {p["id"] for p in seed["products"] if p["shop"] == shop and p["payload"]["category"] == category}
                superset_matching(listing(c, "/products", {"shopId": shop, "category": category}), expected,
                                  lambda i: i.get("category") == category, f"{shop} category={category}")
        s.check("seeded_filter_products_by_category", seeded_filter_category)

    return s.results
