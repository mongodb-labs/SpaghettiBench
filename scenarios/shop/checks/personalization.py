"""Checks introduced by the `personalization` phase (3: personalisation)."""
from __future__ import annotations

from typing import Any, Callable

import httpx

from harness.suite import Suite, first_mismatches, get, get_id, listing, mk, ok, rejected, same_set, status, strip_nulls
from scenarios.shop.checks.helpers import (by_product, fields_match, fresh_shop, items_of, order_body, place, product,
                                           seed_orders, stock_is, uid)


ENGRAVING = [{"key": "engraving", "label": "Name to engrave", "type": "text", "required": True, "maxLength": 12},
             {"key": "font", "label": "Font", "type": "select", "options": ["serif", "script"]},
             {"key": "gift_wrap", "label": "Gift wrap", "type": "boolean"}]


def field_problem(want: list[dict], got: Any) -> str | None:
    if not isinstance(got, list) or len(got) != len(want):
        return f"personalization {str(got)[:200]}"
    for w, g in zip(want, got):
        bad = fields_match({**w, "required": w.get("required", False)}, g) if w.get("required") else fields_match(w, g)
        if bad or (not w.get("required") and g.get("required") not in (None, False)):
            return f"{w['key']}: {bad or 'required ' + repr(g.get('required'))}"
    return None


def v3(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v3")
    st = s.state
    sh = fresh_shop(c, "braga-gifts")

    def create():
        st["mug"] = mk(c, "/products", product(sh, "MUG-P", "Name mug", "mugs", 22, 10, personalization=ENGRAVING), "mug")
        st["bowl"] = mk(c, "/products", product(sh, "BOWL-1", "Bowl", "bowls", 15, 10), "product without fields")
        st["scarf"] = mk(c, "/products", product(sh, "SCARF-1", "Scarf", "accessories", 40, 5, personalization=[
            {"key": "initials", "label": "Initials", "type": "text", "maxLength": 3}]), "scarf")
    s.check("create_products", create)

    def read_back():
        problem = field_problem(ENGRAVING, get(c, f"/products/{s.need('mug')}").get("personalization"))
        assert not problem, problem
        got = get(c, f"/products/{s.need('bowl')}").get("personalization")
        assert (got or []) == [], f"product without fields: personalization {got!r}"
    s.check("fields_read_back", read_back)

    def base(fields: list) -> dict:
        return product(sh, f"REJ-{uid()}", "Rejected", "mugs", 5, 1, personalization=fields)
    invalid = {
        "unknown_type": [{"key": "size", "label": "Size", "type": "number"}],
        "select_without_options": [{"key": "font", "label": "Font", "type": "select"}],
        "duplicate_keys": [{"key": "name", "label": "Name", "type": "text"}, {"key": "name", "label": "Name 2", "type": "text"}],
        "invalid_key": [{"key": "gift wrap", "label": "Gift wrap", "type": "boolean"}],
        "zero_max_length": [{"key": "name", "label": "Name", "type": "text", "maxLength": 0}],
    }
    for name, fields in invalid.items():
        s.check(f"rejects_field_{name}", lambda fields=fields, name=name: rejected(c.post("/products", json=base(fields)), name))

    def patch_replaces():
        new = [{"key": "initials", "label": "Initials", "type": "text", "required": True, "maxLength": 3}]
        ok(c.patch(f"/products/{s.need('scarf')}", json={"personalization": new}), "PATCH personalization")
        problem = field_problem(new, get(c, f"/products/{s.need('scarf')}").get("personalization"))
        assert not problem, problem
    s.check("patch_replaces_fields", patch_replaces)

    def orders():
        st["o1"] = get_id(place(c, order_body(sh, "rita@example.pt", [
            by_product(s.need("mug"), 1, personalization={"engraving": "Ana", "font": "script"}),
            by_product(s.need("mug"), 2, personalization={"engraving": "Rui", "gift_wrap": True}),
            by_product(s.need("bowl"))])))
    s.check("order_same_variant_with_different_values", orders)

    def values_read_back():
        got = items_of(get(c, f"/orders/{s.need('o1')}"))
        want = [{"engraving": "Ana", "font": "script"}, {"engraving": "Rui", "gift_wrap": True}, {}]
        assert len(got) == 3, f"{len(got)} items"
        for i, (w, g) in enumerate(zip(want, got)):
            assert strip_nulls(g.get("personalization") or {}) == w, f"items[{i}].personalization {g.get('personalization')!r}"
        assert [g.get("quantity") for g in got] == [1, 2, 1], f"quantities {[g.get('quantity') for g in got]}"
        stock_is(c, s.need("mug"), 7)
    s.check("personalization_read_back", values_read_back)

    def mug(values: dict, qty: int = 1) -> dict:
        return by_product(s.need("mug"), qty, personalization=values)
    order_invalid = {
        "missing_required": lambda: [mug({"font": "serif"})],
        "empty_required": lambda: [mug({"engraving": ""})],
        "too_long": lambda: [mug({"engraving": "Bartholomew J"})],
        "unknown_key": lambda: [mug({"engraving": "Ana", "colour": "red"})],
        "wrong_type": lambda: [mug({"engraving": "Ana", "gift_wrap": "yes"})],
        "not_an_option": lambda: [mug({"engraving": "Ana", "font": "comic"})],
        "product_without_fields": lambda: [by_product(s.need("bowl"), personalization={"engraving": "Ana"})],
        "same_variant_same_values": lambda: [mug({"engraving": "Ana"}), mug({"engraving": "Ana"})],
    }
    for name, items in order_invalid.items():
        s.check(f"rejects_order_{name}", lambda items=items, name=name: rejected(c.post("/orders", json=order_body(
            sh, "rex@example.pt", items())), name))

    def not_stored():
        same_set(listing(c, "/orders", {"shopId": sh}), {s.need("o1")}, "orders after rejections")
        stock_is(c, s.need("mug"), 7)
    s.check("order_rejections_store_nothing", not_stored)

    def total_quantity():
        status(c.post("/orders", json=order_body(sh, "rex@example.pt", [mug({"engraving": "Eva"}, 4), mug({"engraving": "Ivo"}, 4)])),
               409, "8 mugs in two lines with 7 in stock")
        stock_is(c, s.need("mug"), 7)
    s.check("stock_checked_for_total_quantity", total_quantity)

    def second_order():
        st["o2"] = get_id(place(c, order_body(sh, "lu@example.pt", [mug({"engraving": "Lu", "gift_wrap": False})])))
    s.check("create_second_order", second_order)

    def order_filter(params: dict, keys: list[str]) -> Callable[[], None]:
        def fn():
            same_set(listing(c, "/orders", {"shopId": sh, **params}), {s.need(k) for k in keys}, str(params))
        return fn
    s.check("filter_boolean_true", order_filter({"personalization.gift_wrap": "true"}, ["o1"]))
    s.check("filter_boolean_false", order_filter({"personalization.gift_wrap": "false"}, ["o2"]))
    s.check("filter_text_any_line", order_filter({"personalization.engraving": "Rui"}, ["o1"]))
    s.check("filter_select", order_filter({"personalization.font": "script"}, ["o1"]))

    def fields_change():
        ok(c.patch(f"/products/{s.need('mug')}", json={"personalization": []}), "PATCH personalization")
        got = items_of(get(c, f"/orders/{s.need('o1')}"))
        assert strip_nulls(got[0].get("personalization") or {}) == {"engraving": "Ana", "font": "script"}, \
            f"personalization {got[0].get('personalization')!r} after the product's fields changed"
        same_set(listing(c, "/orders", {"shopId": sh, "personalization.engraving": "Ana"}), {s.need("o1")}, "engraving=Ana")
        place(c, order_body(sh, "rex@example.pt", [by_product(s.need("mug"))]), "order without values after fields removed")
        rejected(c.post("/orders", json=order_body(sh, "rex@example.pt", [mug({"engraving": "Ana"})])),
                 "values for removed fields")
    s.check("changed_fields_keep_orders", fields_change)

    if seed:
        def seeded_orders():
            got = seed_orders(c, seed, st)
            first_mismatches(seed["orders"], lambda o: got[o["id"]], lambda o, g: next((
                f"items[{i}].personalization {x.get('personalization')!r}" for i, x in enumerate(items_of(g))
                if strip_nulls(x.get("personalization") or {})), None))
        s.check("seeded_orders_without_personalization", seeded_orders)

    return s.results
