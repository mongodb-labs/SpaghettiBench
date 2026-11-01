"""Checks introduced by the `search` phase: search as you type over products."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get, get_id, mk, ok, rejected
from scenarios.shop.checks.helpers import by_product, fresh_shop, order_body, place, product


def v8(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v8")
    st = s.state
    sh = fresh_shop(c, "lisbon-search")

    def results(q: str, **params) -> list[str]:
        body = get(c, "/products/search", {"shopId": sh, "q": q, **params})
        got = body.get("results") if isinstance(body, dict) else None
        assert isinstance(got, list), f"search {q!r}: expected {{'results': [...]}}, got {str(body)[:200]}"
        return [get_id(x) for x in got]

    def names(ids: list[str]) -> list[str]:
        back = {v: k for k, v in s.need("p").items()}
        return [back.get(i, i) for i in ids]

    def fixtures():
        ok(c.post(f"/shops/{sh}/categories/vases/attributes", json={"key": "glaze", "label": "Glaze", "type": "select",
                                                                   "options": ["matte", "gloss"]}), "attribute")
        rows = {
            "speckled": ("SPECK-MUG", "Speckled mug", "mugs", {"description": "Stoneware with a speckled glaze"}),
            "bowl": ("PLAIN-BOWL", "Plain bowl", "bowls", {"description": "Pairs well with the speckled mug"}),
            "tumbler": ("CAFE-TUMBLER", "Café tumbler", "cups", {"description": "Glass"}),
            "vase": ("VASE-1", "Stoneware vase", "vases", {"attributes": {"glaze": "matte"}}),
            "set": ("GIFT-SET", "Mugs gift set", "sets", {}),
        }
        st["p"] = {k: mk(c, "/products", product(sh, sku, name, cat, 20, 10, **kw), name)
                   for k, (sku, name, cat, kw) in rows.items()}
        got = get(c, f"/products/{st['p']['bowl']}")
        assert got.get("description") == "Pairs well with the speckled mug", f"description {got.get('description')!r}"
        place(c, order_body(sh, "fan@example.pt", [by_product(st["p"]["set"], 2)]))
        for bad in (5, "x" * 5001):
            rejected(c.post("/products", json=product(sh, "BAD", "Bad", "x", 1, 1, description=bad)), f"description {bad!r:.20}")
    s.check("descriptions", fixtures)

    def expect(q: str, keys: list[str], **params) -> None:
        p = s.need("p")
        got = results(q, **params)
        assert got == [p[k] for k in keys], f"{q!r} {params or ''}: {names(got)}, expected {keys}"

    s.check("names_rank_first", lambda: expect("speckled", ["speckled", "bowl"]))
    s.check("prefix_of_last_word", lambda: expect("speck", ["speckled", "bowl"]))
    s.check("word_forms_and_best_sellers_first", lambda: expect("mug", ["set", "speckled", "bowl"]))
    s.check("every_word_must_match", lambda: expect("speckled bowl", ["bowl"]))
    s.check("ignores_case_and_accents", lambda: expect("CAFE", ["tumbler"]))
    s.check("attribute_values", lambda: expect("stoneware matte", ["vase"]))
    s.check("whole_sku", lambda: expect("speck-mug", ["speckled"]))

    def filters():
        expect("speckled", ["bowl"], category="bowls")
        ok(c.patch(f"/products/{s.need('p')['tumbler']}", json={"stock": 0}), "sell out")
        expect("cafe", [], inStock="true")
        assert len(results("speckled", limit=1)) == 1, "limit"
        rejected(c.get("/products/search", params={"shopId": sh}), "search without q")
        other = fresh_shop(c, "lisbon-search-other")
        assert get(c, "/products/search", {"shopId": other, "q": "speckled"}).get("results") == [], "shop isolation"
    s.check("filters_limit_and_validation", filters)

    def current():
        p = s.need("p")
        ok(c.patch(f"/products/{p['bowl']}", json={"description": None}), "clear description")
        expect("speckled", ["speckled"])
        ok(c.patch(f"/products/{p['vase']}", json={"name": "Stoneware urn"}), "rename")
        expect("urn", ["vase"])
        new = get_id(created(c.post("/products", json=product(sh, "NEW-1", "Speckled plate", "plates", 5, 1)), "new"))
        assert new in results("speckled"), "new product not found at once"
    s.check("reflects_current_data", current)
    return s.results
