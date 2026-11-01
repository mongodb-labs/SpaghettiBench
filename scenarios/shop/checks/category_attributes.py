"""Checks introduced by the `category_attributes` phase (2: category attributes)."""
from __future__ import annotations

from typing import Callable

import httpx

from harness.suite import (Suite, as_list, first_mismatches, get, listing, mk, ok, rejected, same_set, status,
                           strip_nulls, superset_matching)
from scenarios.shop.checks.helpers import NORTHLOOM_MATERIALS, fields_match, fresh_shop, product, uid, values_equal
from scenarios.shop.common import material


MUG_DEFS = [{"key": "capacity_ml", "label": "Capacity (ml)", "type": "number"},
            {"key": "dishwasher_safe", "label": "Dishwasher safe", "type": "boolean"},
            {"key": "glaze", "label": "Glaze", "type": "select", "options": ["matte", "gloss", "speckled"]},
            {"key": "maker_note", "label": "Maker's note", "type": "text"}]
PLATE_DEFS = [{"key": "dishwasher_safe", "label": "Dishwasher safe", "type": "boolean"},
              {"key": "glaze", "label": "Glaze", "type": "text"},
              {"key": "diameter_cm", "label": "Diameter (cm)", "type": "number"}]


def v2(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v2")
    st = s.state
    sh = fresh_shop(c, "porto-home")

    def path(category: str) -> str:
        return f"/shops/{sh}/categories/{category}/attributes"

    def define():
        for category, defs in (("mugs", MUG_DEFS), ("plates", PLATE_DEFS)):
            for d in defs:
                ok(c.post(path(category), json=d), f"define {category}.{d['key']}")
        st["defined"] = True
    s.check("define_attributes", define)

    def list_defs():
        s.need("defined")
        got = as_list(get(c, path("mugs")))
        assert [d.get("key") for d in got] == [d["key"] for d in MUG_DEFS], f"keys {[d.get('key') for d in got]}"
        for want, d in zip(MUG_DEFS, got):
            bad = fields_match(want, d)
            assert not bad, f"{want['key']}: differs in {bad}"
    s.check("list_definitions", list_defs)

    invalid = {
        "invalid_key": {"key": "cap ml", "label": "Cap", "type": "number"},
        "unknown_type": {"key": "colour", "label": "Colour", "type": "color"},
        "select_without_options": {"key": "finish", "label": "Finish", "type": "select"},
        "select_duplicate_options": {"key": "finish", "label": "Finish", "type": "select", "options": ["a", "a"]},
        "missing_label": {"key": "finish", "type": "text"},
    }
    for name, body in invalid.items():
        s.check(f"rejects_definition_{name}", lambda body=body, name=name: rejected(c.post(path("mugs"), json=body), name))
    s.check("rejects_duplicate_definition", lambda: status(c.post(path("mugs"), json=MUG_DEFS[0]), 409, "duplicate key"))
    def defs_unchanged():
        n = len(as_list(get(c, path("mugs"))))
        assert n == 4, f"{n} definitions, expected 4"
    s.check("definitions_unchanged_after_rejections", defs_unchanged)

    attrs = {"m1": ("mugs", {"capacity_ml": 350, "dishwasher_safe": True, "glaze": "matte", "maker_note": "thrown by Rui"}),
             "m2": ("mugs", {"capacity_ml": 500, "dishwasher_safe": False, "glaze": "gloss"}),
             "m3": ("mugs", {"capacity_ml": 250, "glaze": "matte"}),
             "m4": ("mugs", {}),
             "p1": ("plates", {"diameter_cm": 27, "glaze": "ash"})}

    def create():
        s.need("defined")
        st["p"] = {k: mk(c, "/products", product(sh, f"PH-{k.upper()}", f"Item {k}", category, 20, 5,
                                                 **({"attributes": a} if a else {})), f"product {k}")
                   for k, (category, a) in attrs.items()}
    s.check("create_products_with_attributes", create)

    def read_back():
        for k, (_, a) in attrs.items():
            got = get(c, f"/products/{s.need('p')[k]}").get("attributes")
            assert values_equal(a, got), f"product {s.need('p')[k]}: attributes {got!r}, expected {a}"
    s.check("attributes_read_back", read_back)

    value_invalid = {
        "unknown_key": {"colour": "red"},
        "number_as_string": {"capacity_ml": "350"},
        "select_not_in_options": {"glaze": "satin"},
        "boolean_as_string": {"dishwasher_safe": "true"},
        "key_of_other_category": {"diameter_cm": 20},
    }
    for name, a in value_invalid.items():
        s.check(f"rejects_attribute_{name}", lambda a=a, name=name: rejected(c.post("/products", json=product(
            sh, f"REJ-{uid()}", "Rejected", "mugs", 5, 1, attributes=a)), name))
    s.check("invalid_attribute_products_not_stored", lambda: same_set(
        listing(c, "/products", {"shopId": sh}), set(s.need("p").values()), "products"))

    def patch_invalid():
        pid = s.need("p")["m3"]
        rejected(c.patch(f"/products/{pid}", json={"attributes": {"glaze": "satin"}}), "PATCH invalid attribute")
        got = get(c, f"/products/{pid}").get("attributes")
        assert values_equal(attrs["m3"][1], got), f"attributes {got!r} changed"
    s.check("patch_rejects_invalid_attribute", patch_invalid)

    def attr_filter(params: dict, keys: list[str]) -> Callable[[], None]:
        def fn():
            p = s.need("p")
            same_set(listing(c, "/products", {"shopId": sh, "category": "mugs", **params}), {p[k] for k in keys}, str(params))
        return fn
    s.check("filter_select", attr_filter({"attr.glaze": "matte"}, ["m1", "m3"]))
    s.check("filter_boolean_true", attr_filter({"attr.dishwasher_safe": "true"}, ["m1"]))
    s.check("filter_boolean_false", attr_filter({"attr.dishwasher_safe": "false"}, ["m2"]))
    s.check("filter_number_exact", attr_filter({"attr.capacity_ml": "350"}, ["m1"]))
    s.check("filter_number_min", attr_filter({"attr.capacity_ml.min": "300"}, ["m1", "m2"]))
    s.check("filter_number_max", attr_filter({"attr.capacity_ml.max": "300"}, ["m3"]))
    s.check("filter_number_range_and_select", attr_filter({"attr.capacity_ml.min": "300", "attr.capacity_ml.max": "400",
                                                           "attr.glaze": "matte"}, ["m1"]))
    s.check("filter_requires_category", lambda: rejected(c.get("/products", params={"shopId": sh, "attr.glaze": "matte"}),
                                                         "attribute filter without category"))
    s.check("filter_rejects_undefined_attribute", lambda: rejected(c.get("/products", params={
        "shopId": sh, "category": "mugs", "attr.diameter_cm": "27"}), "attribute of another category"))

    def facets(params: dict, total: int, want: dict) -> Callable[[], None]:
        def fn():
            s.need("p")
            got = get(c, "/products/facets", {"shopId": sh, "category": "mugs", **params})
            assert got.get("total") == total, f"total {got.get('total')!r}, expected {total}"
            counts = got.get("attributes") or {}
            for key, values in want.items():
                assert strip_nulls(counts.get(key)) == values, f"{key}: {counts.get(key)!r}, expected {values}"
        return fn
    s.check("facets", facets({}, 4, {"dishwasher_safe": {"true": 1, "false": 1}, "glaze": {"matte": 2, "gloss": 1}}))
    s.check("facets_with_filter", facets({"attr.capacity_ml.min": "300"}, 2,
                                         {"dishwasher_safe": {"true": 1, "false": 1}, "glaze": {"matte": 1, "gloss": 1}}))
    s.check("facets_require_category", lambda: rejected(c.get("/products/facets", params={"shopId": sh}), "no category"))

    def patch_merge():
        pid = s.need("p")["m3"]
        ok(c.patch(f"/products/{pid}", json={"attributes": {"dishwasher_safe": True, "glaze": None}}), "PATCH attributes")
        got = get(c, f"/products/{pid}").get("attributes")
        assert values_equal({"capacity_ml": 250, "dishwasher_safe": True}, got), f"attributes {got!r}"
    s.check("patch_merges_and_null_removes", patch_merge)

    def move():
        pid = s.need("p")["m2"]
        ok(c.patch(f"/products/{pid}", json={"category": "plates"}), "PATCH category")
        got = get(c, f"/products/{pid}").get("attributes")
        assert values_equal({"dishwasher_safe": False}, got), \
            f"attributes {got!r} after moving to plates, expected only dishwasher_safe (glaze is text there)"
    s.check("category_move_keeps_only_matching_attributes", move)

    def move_with_values():
        pid = s.need("p")["m1"]
        ok(c.patch(f"/products/{pid}", json={"category": "plates", "attributes": {"glaze": "ash", "diameter_cm": 11}}),
           "PATCH category and attributes")
        got = get(c, f"/products/{pid}").get("attributes")
        assert values_equal({"dishwasher_safe": True, "glaze": "ash", "diameter_cm": 11}, got), f"attributes {got!r}"
        p = s.need("p")
        same_set(listing(c, "/products", {"shopId": sh, "category": "plates", "attr.glaze": "ash"}), {p["p1"], p["m1"]},
                 "plates with glaze=ash")
    s.check("category_move_validates_new_attributes", move_with_values)

    if seed:
        tops = [p for p in seed["products"] if p["shop"] == "northloom" and p["payload"]["category"] == "tops"]

        def seeded_definition():
            got = {d.get("key"): d for d in as_list(get(c, "/shops/northloom/categories/tops/attributes"))}
            d = got.get("material")
            assert d, f"no material attribute for Northloom's tops (keys {list(got)})"
            assert d.get("type") == "select" and set(d.get("options") or []) == NORTHLOOM_MATERIALS, \
                f"material: type {d.get('type')!r}, options {d.get('options')!r}"
            assert d.get("label") == "Material", f"label {d.get('label')!r}"
        s.check("seeded_material_defined", seeded_definition)

        def seeded_backfill():
            first_mismatches(tops, lambda x: get(c, f"/products/{x['id']}"), lambda x, got: (
                (got.get("attributes") or {}).get("material") != material(x["payload"]["name"])
                and f"{x['payload']['name']!r}: material {(got.get('attributes') or {}).get('material')!r}, "
                    f"expected {material(x['payload']['name'])!r}"))
        s.check("seeded_material_backfilled", seeded_backfill)

        def seeded_filter():
            for value in sorted(NORTHLOOM_MATERIALS):
                expected = {p["id"] for p in tops if material(p["payload"]["name"]) == value}
                items = listing(c, "/products", {"shopId": "northloom", "category": "tops", "attr.material": value})
                superset_matching(items, expected, lambda i: (i.get("attributes") or {}).get("material") == value,
                                  f"attr.material={value}")
        s.check("seeded_filter_by_material", seeded_filter)

        def seeded_others():
            others = [p for p in seed["products"] if p not in tops]
            first_mismatches(others, lambda x: get(c, f"/products/{x['id']}"),
                             lambda x, got: strip_nulls(got.get("attributes") or {}) and f"attributes {got.get('attributes')}")
        s.check("seeded_material_only_on_tops", seeded_others)

    return s.results
