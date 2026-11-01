"""Checks introduced by the `variants` phase (1: variants)."""
from __future__ import annotations

from typing import Any, Callable

import httpx

from harness.suite import (Suite, created, first_mismatches, get, get_id, listing, mk, ok, rejected, same_set, status,
                           strip_nulls, superset_matching)
from scenarios.shop.checks.helpers import (burst, by_product, by_variant, expect_amount, fields_match, fresh_shop,
                                           items_of, line_problem, order_body, place, product, seed_orders, seed_stock,
                                           stock_is, uid, variant_stock, variants_of, wins)


def v1(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v1")
    st = s.state
    sh, other = fresh_shop(c, "bergen-apparel"), fresh_shop(c, "tromso-apparel")
    tee = [{"sku": "TEE-S-BLACK", "options": {"size": "S", "colour": "black"}, "price": 25, "stock": 10},
           {"sku": "TEE-M-BLACK", "options": {"size": "M", "colour": "black"}, "price": 25, "stock": 0},
           {"sku": "TEE-M-WHITE", "options": {"size": "M", "colour": "white"}, "price": 27.5, "stock": 4}]

    def create():
        body = created(c.post("/products", json={"shopId": sh, "name": "Heavy tee", "category": "tops",
                                                  "options": ["size", "colour"], "variants": tee}), "product with variants")
        st["tee"] = get_id(body)
        st["v"] = {sku: get_id(v) for sku, v in variants_of(body).items()}
        cap = created(c.post("/products", json=product(sh, "CAP-1", "Wool cap", "accessories", 30, 6)), "old-shape product")
        st["cap"] = get_id(cap)
        st["v"]["CAP-1"] = get_id((cap.get("variants") or [{}])[0])
        st["sock"] = mk(c, "/products", product(sh, "SOCK-1", "Socks", "accessories", 9.5, 0), "product out of stock")
        foreign = created(c.post("/products", json=product(other, "FJORD-1", "Fjord scarf", "accessories", 45, 5)),
                          "other shop's product")
        st["foreign_variant"] = get_id((foreign.get("variants") or [{}])[0])
    s.check("create_products", create)

    def read_back():
        got = get(c, f"/products/{s.need('tee')}")
        assert got.get("options") == ["size", "colour"], f"options {got.get('options')!r}"
        vs = got.get("variants")
        assert isinstance(vs, list) and len(vs) == 3, f"variants {str(vs)[:200]}"
        for want, v in zip(tee, vs):
            bad = fields_match(want, v)
            assert not bad and get_id(v), f"variant {want['sku']}: {bad or 'no id'}"
    s.check("variants_read_back", read_back)

    def old_fields():
        got = get(c, f"/products/{s.need('tee')}")
        assert got.get("sku") == "TEE-S-BLACK", f"sku {got.get('sku')!r} is not the first variant's"
        expect_amount(got.get("price"), 25, "price (the first variant's)")
        assert got.get("stock") == 14, f"stock {got.get('stock')!r}, expected 14 (all variants)"
    s.check("old_fields_from_variants", old_fields)

    def old_shape():
        got = get(c, f"/products/{s.need('cap')}")
        assert (got.get("options") or []) == [], f"options {got.get('options')!r}"
        vs = got.get("variants") or []
        assert len(vs) == 1, f"{len(vs)} variants, expected 1"
        bad = fields_match({"sku": "CAP-1", "price": 30, "stock": 6}, vs[0])
        assert not bad and not strip_nulls(vs[0].get("options") or {}), f"variant differs: {bad or vs[0].get('options')}"
    s.check("old_shape_creates_one_variant", old_shape)

    def base(**kw: Any) -> dict:
        return {"shopId": sh, "name": "Rejected", "category": "tops", "options": ["size"],
                "variants": [{"sku": f"REJ-{uid()}", "options": {"size": "S"}, "price": 10, "stock": 1}], **kw}

    def v(size: Any = "S", **kw: Any) -> dict:
        return {"sku": f"REJ-{uid()}", "options": {"size": size}, "price": 10, "stock": 1, **kw}
    invalid = {
        "variants_and_sku": lambda: base(sku="REJ-TOP"),
        "variant_missing_option": lambda: base(variants=[{**v(), "options": {}}]),
        "variant_extra_option": lambda: base(variants=[{**v(), "options": {"size": "S", "fit": "slim"}}]),
        "variants_with_same_options": lambda: base(variants=[v(), v()]),
        "no_variants": lambda: base(variants=[]),
        "empty_option_value": lambda: base(variants=[v("")]),
        "invalid_variant_price": lambda: base(variants=[v(price=-1)]),
    }
    for name, body in invalid.items():
        s.check(f"rejects_{name}", lambda body=body, name=name: rejected(c.post("/products", json=body()), name))
    s.check("rejects_variant_sku_taken", lambda: status(c.post("/products", json=base(variants=[v(sku="CAP-1")])),
                                                         409, "variant with another product's sku"))
    s.check("invalid_products_not_stored", lambda: same_set(
        listing(c, "/products", {"shopId": sh}), {s.need("tee"), s.need("cap"), s.need("sock")}, "products"))

    def add_variant():
        body = ok(c.post(f"/products/{s.need('tee')}/variants",
                         json={"sku": "TEE-L-BLACK", "options": {"size": "L", "colour": "black"}, "price": 26, "stock": 5}),
                  "POST variant")
        got = variants_of(get(c, f"/products/{s.need('tee')}"))
        assert len(got) == 4 and get_id(got.get("TEE-L-BLACK")), f"variants after adding: {list(got)}"
        st["v"]["TEE-L-BLACK"] = get_id(got["TEE-L-BLACK"])
        assert isinstance(body, dict), "no product in the response"
    s.check("add_variant", add_variant)
    s.check("add_variant_rejects_same_options", lambda: rejected(c.post(f"/products/{s.need('tee')}/variants", json={
        "sku": f"TEE-{uid()}", "options": {"size": "S", "colour": "black"}, "price": 1, "stock": 1}), "same option values"))
    s.check("add_variant_rejects_sku_taken", lambda: status(c.post(f"/products/{s.need('tee')}/variants", json={
        "sku": "CAP-1", "options": {"size": "XL", "colour": "black"}, "price": 1, "stock": 1}), 409, "sku taken"))

    def patch_variant():
        ok(c.patch(f"/products/{s.need('tee')}/variants/{s.need('v')['TEE-M-BLACK']}", json={"price": 24, "stock": 3}),
           "PATCH variant")
        bad = fields_match({"price": 24, "stock": 3, "options": {"size": "M", "colour": "black"}},
                           variants_of(get(c, f"/products/{s.need('tee')}")).get("TEE-M-BLACK"))
        assert not bad, f"variant differs in {bad}"
    s.check("patch_variant", patch_variant)

    def old_patch():
        ok(c.patch(f"/products/{s.need('cap')}", json={"price": 32, "stock": 7}), "PATCH product price and stock")
        got = get(c, f"/products/{s.need('cap')}")
        bad = fields_match({"price": 32, "stock": 7}, got) + fields_match({"price": 32, "stock": 7}, (got.get("variants") or [{}])[0])
        assert not bad, f"differs in {bad}"
    s.check("old_patch_changes_only_variant", old_patch)

    def old_patch_several():
        rejected(c.patch(f"/products/{s.need('tee')}", json={"price": 20}), "PATCH price of a product with several variants")
        expect_amount(variants_of(get(c, f"/products/{s.need('tee')}")).get("TEE-S-BLACK", {}).get("price"), 25, "price")
    s.check("old_patch_rejected_with_several_variants", old_patch_several)
    s.check("patch_rejects_variants", lambda: rejected(c.patch(f"/products/{s.need('tee')}", json={"variants": []}),
                                                        "PATCH variants"))

    def product_filter(params: dict, keys: list[str]) -> Callable[[], None]:
        def fn():
            same_set(listing(c, "/products", {"shopId": sh, **params}), {s.need(k) for k in keys}, str(params))
        return fn
    s.check("filter_option", product_filter({"option.size": "M"}, ["tee"]))
    s.check("filter_option_colour", product_filter({"option.colour": "white"}, ["tee"]))
    s.check("filter_option_no_match", product_filter({"option.size": "XXL"}, []))
    s.check("filter_sku_any_variant", product_filter({"sku": "TEE-M-WHITE"}, ["tee"]))
    s.check("filter_in_stock_any_variant", product_filter({"inStock": "true"}, ["tee", "cap"]))

    def orders():
        vv = s.need("v")
        st["o1"] = get_id(place(c, order_body(sh, "ola@example.no", [by_variant(vv["TEE-M-WHITE"], 2),
                                                                      by_product(s.need("cap"))])))
        st["o2"] = get_id(place(c, order_body(sh, "kari@example.no", [by_variant(vv["TEE-S-BLACK"])])))
    s.check("create_orders", orders)

    def order_lines():
        vv = s.need("v")
        want = [{"productId": s.need("tee"), "variantId": vv["TEE-M-WHITE"], "sku": "TEE-M-WHITE", "name": "Heavy tee",
                 "options": {"size": "M", "colour": "white"}, "unitPrice": 27.5, "quantity": 2, "lineTotal": 55},
                {"productId": s.need("cap"), "variantId": vv["CAP-1"], "sku": "CAP-1", "name": "Wool cap",
                 "unitPrice": 32, "quantity": 1, "lineTotal": 32}]
        got = get(c, f"/orders/{s.need('o1')}")
        problem = line_problem(want, got)
        assert not problem, problem
        expect_amount(got.get("total"), 87, "total")
    s.check("order_lines_show_variant", order_lines)

    def stock_from_variant():
        assert variant_stock(c, s.need("tee"), "TEE-M-WHITE") == 2, "TEE-M-WHITE stock not 2"
        assert variant_stock(c, s.need("tee"), "TEE-S-BLACK") == 9, "TEE-S-BLACK stock not 9"
        stock_is(c, s.need("cap"), 6)
    s.check("order_takes_variant_stock", stock_from_variant)

    def base_order(*items: dict) -> dict:
        return order_body(sh, "rex@example.no", list(items))
    order_invalid = {
        "product_with_several_variants": lambda: base_order(by_product(s.need("tee"))),
        "variant_of_other_product": lambda: base_order(by_product(s.need("cap"), variantId=s.need("v")["TEE-S-BLACK"])),
        "same_variant_twice": lambda: base_order(by_variant(s.need("v")["TEE-S-BLACK"]), by_variant(s.need("v")["TEE-S-BLACK"])),
        "variant_of_other_shop": lambda: base_order(by_variant(s.need("foreign_variant"))),
    }
    for name, body in order_invalid.items():
        s.check(f"rejects_order_{name}", lambda body=body, name=name: rejected(c.post("/orders", json=body()), name))
    s.check("rejects_order_unknown_variant", lambda: rejected(c.post("/orders", json=base_order(
        by_variant("ffffffffffffffffffffffff"))), "unknown variant", allow_404=True))
    s.check("insufficient_variant_stock_409", lambda: status(c.post("/orders", json=base_order(
        by_variant(s.need("v")["TEE-M-BLACK"], 4))), 409, "4 of a variant with 3 in stock"))
    s.check("order_rejections_store_nothing", lambda: same_set(listing(c, "/orders", {"shopId": sh}),
                                                               {s.need("o1"), s.need("o2")}, "orders"))

    def order_filter(params: Callable[[], dict], keys: list[str]) -> Callable[[], None]:
        def fn():
            same_set(listing(c, "/orders", {"shopId": sh, **params()}), {s.need(k) for k in keys}, str(params()))
        return fn
    s.check("filter_orders_by_variant", order_filter(lambda: {"variantId": s.need("v")["TEE-M-WHITE"]}, ["o1"]))
    s.check("filter_orders_by_product_any_variant", order_filter(lambda: {"productId": s.need("tee")}, ["o1", "o2"]))

    def frozen():
        ok(c.patch(f"/products/{s.need('tee')}/variants/{s.need('v')['TEE-S-BLACK']}", json={"price": 30}), "PATCH variant")
        got = items_of(get(c, f"/orders/{s.need('o2')}"))[0]
        expect_amount(got.get("unitPrice"), 25, f"order {s.need('o2')}: unitPrice after the variant's price changed")
    s.check("order_keeps_variant_price", frozen)

    def cancel():
        ok(c.post(f"/orders/{s.need('o1')}/cancel"), "cancel")
        assert variant_stock(c, s.need("tee"), "TEE-M-WHITE") == 4, "TEE-M-WHITE stock not back to 4"
        stock_is(c, s.need("cap"), 7, " after cancelling")
    s.check("cancel_returns_variant_stock", cancel)

    def concurrent():
        vid = s.need("v")["TEE-L-BLACK"]
        won = wins(burst(8, lambda i: c.post("/orders", json=order_body(sh, f"drop{i}@example.no", [by_variant(vid)]))))
        assert len(won) == 5, f"{len(won)} of 8 orders for 5 in stock succeeded"
        assert variant_stock(c, s.need("tee"), "TEE-L-BLACK") == 0, "TEE-L-BLACK stock not 0"
    s.check("concurrent_variant_orders_never_oversell", concurrent)

    if seed:
        def seeded_converted():
            want = seed_stock(c, seed, st)

            def problem(x: dict, got: dict) -> Any:
                vs = got.get("variants")
                if (got.get("options") or []) != [] or not isinstance(vs, list) or len(vs) != 1:
                    return f"options {got.get('options')!r}, {len(vs) if isinstance(vs, list) else 'no'} variants"
                return fields_match({"sku": x["payload"]["sku"], "price": x["payload"]["price"], "stock": want[x["id"]]},
                                    vs[0]) or (strip_nulls(vs[0].get("options") or {}) and f"options {vs[0].get('options')}")
            first_mismatches(seed["products"], lambda x: get(c, f"/products/{x['id']}"), problem)
        s.check("seeded_products_converted", seeded_converted)

        def seeded_lines():
            variant = {p["id"]: get_id((get(c, f"/products/{p['id']}").get("variants") or [{}])[0]) for p in seed["products"]}
            got = seed_orders(c, seed, st)
            first_mismatches(seed["orders"], lambda o: got[o["id"]], lambda o, g: next((
                f"items[{i}]: variantId {x.get('variantId')!r}, expected {variant.get(x.get('productId'))!r}"
                for i, x in enumerate(items_of(g))
                if not x.get("variantId") or x.get("variantId") != variant.get(x.get("productId"))
                or strip_nulls(x.get("options") or {})), None))
            st["seed_variant"] = variant
        s.check("seeded_order_lines_have_variant", seeded_lines)

        def seeded_filter_variant():
            variant = st.get("seed_variant") or {p["id"]: get_id((get(c, f"/products/{p['id']}").get("variants") or [{}])[0])
                                                  for p in seed["products"]}
            for p in seed["products"][::9]:
                vid = variant[p["id"]]
                expected = {o["id"] for o in seed["orders"] if any(x["productId"] == p["id"] for x in o["lines"])}
                items = listing(c, "/orders", {"shopId": p["shop"], "variantId": vid})
                superset_matching(items, expected, lambda i: any(x.get("variantId") == vid for x in items_of(i)),
                                  f"variantId={vid}")
        s.check("seeded_filter_orders_by_variant", seeded_filter_variant)

        def seeded_cancel():
            o = next(x for x in seed["orders"] if x["key"] == "late_cancel")
            if get(c, f"/orders/{o['id']}").get("status") == "placed":
                before = {x["productId"]: get(c, f"/products/{x['productId']}").get("stock") for x in o["lines"]}
                ok(c.post(f"/orders/{o['id']}/cancel"), "cancel an existing order")
                for x in o["lines"]:
                    got = get(c, f"/products/{x['productId']}").get("stock")
                    assert got == before[x["productId"]] + x["quantity"], \
                        f"product {x['productId']}: stock {got!r} after cancelling, expected {before[x['productId']] + x['quantity']}"
            assert get(c, f"/orders/{o['id']}").get("status") == "cancelled", "existing order not cancelled"
        s.check("seeded_cancel_existing_order", seeded_cancel)

    return s.results
