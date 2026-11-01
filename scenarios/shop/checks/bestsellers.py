"""Checks introduced by the `bestsellers` phase (improvement: best sellers)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get, get_id, listing, mk, ok
from scenarios.shop.checks.helpers import (by_product, by_variant, fresh_shop, items_of, num, order_body, place,
                                           product, variants_of)


def i2(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("i2")
    st = s.state
    sh = fresh_shop(c, "aveiro-bags")

    def setup():
        alpha = created(c.post("/products", json={
            "shopId": sh, "name": "Alpha tote", "category": "totes", "options": ["colour"],
            "variants": [{"sku": "ALPHA-N", "options": {"colour": "natural"}, "price": 30, "stock": 20},
                         {"sku": "ALPHA-B", "options": {"colour": "black"}, "price": 30, "stock": 20}]}), "Alpha tote")
        st["p"] = {"alpha": get_id(alpha)}
        st["av"] = {sku: get_id(v) for sku, v in variants_of(alpha).items()}
        for k, name, category in (("beta", "Beta tote", "totes"), ("gamma", "Gamma tote", "totes"),
                                  ("aard", "Aardvark tote", "totes"), ("delta", "Delta tote", "totes"),
                                  ("pouch", "Zip pouch", "pouches")):
            st["p"][k] = mk(c, "/products", product(sh, k.upper(), name, category, 25, 20), name)
        p, av = st["p"], st["av"]
        st["ox"] = get_id(place(c, order_body(sh, "x@example.pt", [by_variant(av["ALPHA-N"], 2), by_variant(av["ALPHA-B"]),
                                                                   by_product(p["beta"])])))
        oy = get_id(place(c, order_body(sh, "y@example.pt", [by_product(p["beta"], 2)])))
        place(c, order_body(sh, "z@example.pt", [by_product(p["gamma"])]))
        place(c, order_body(sh, "a@example.pt", [by_product(p["aard"])]))
        oc = get_id(place(c, order_body(sh, "c@example.pt", [by_product(p["gamma"], 4)])))
        ok(c.post(f"/orders/{oc}/cancel"), "cancel")
        lid = get_id(items_of(get(c, f"/orders/{oy}"))[0])
        created(c.post(f"/orders/{oy}/returns", json={"items": [{"lineId": lid, "quantity": 1}]}), "return")
    s.check("create_orders", setup)

    def sold(want: dict[str, int], items: list[dict] | None = None) -> None:
        p = s.need("p")
        got = {get_id(i): i.get("unitsSold") for i in items} if items is not None else \
            {pid: get(c, f"/products/{pid}").get("unitsSold") for pid in p.values()}
        for k, v in want.items():
            assert num(got.get(p[k])) == v, f"product {p[k]} ({k}): unitsSold {got.get(p[k])!r}, expected {v}"

    want = {"alpha": 3, "beta": 2, "gamma": 1, "aard": 1, "delta": 0, "pouch": 0}
    s.check("units_sold_on_product", lambda: sold(want))
    s.check("units_sold_in_list", lambda: sold(want, listing(c, "/products", {"shopId": sh})))

    def order(params: dict, keys: list[str], limit: int = 500) -> None:
        p = s.need("p")
        got = [get_id(i) for i in listing(c, "/products", {"shopId": sh, "sort": "bestselling", **params}, limit=limit)]
        names = {v: k for k, v in p.items()}
        assert got == [p[k] for k in keys], f"order {[names.get(g, g) for g in got]}, expected {keys}"

    s.check("sort_bestselling", lambda: order({}, ["alpha", "beta", "aard", "gamma", "delta", "pouch"]))
    s.check("sort_with_filter_and_limit", lambda: order({"category": "totes"}, ["alpha", "beta"], limit=2))

    def follows():
        ok(c.post(f"/orders/{s.need('ox')}/cancel"), "cancel")
        place(c, order_body(sh, "d@example.pt", [by_product(s.need("p")["delta"], 5)]))
        sold({"alpha": 0, "beta": 1, "delta": 5})
        order({}, ["delta", "aard", "beta", "gamma", "alpha", "pouch"])
    s.check("units_sold_follow_orders", follows)

    if seed:
        def seeded_units():
            for shop in sorted({p["shop"] for p in seed["products"]}):
                want: dict[str, int] = {}
                for o in listing(c, "/orders", {"shopId": shop}):
                    if o.get("status") != "cancelled":
                        for x in items_of(o):
                            want[x.get("productId")] = want.get(x.get("productId"), 0) + \
                                (x.get("quantity") or 0) - (x.get("returnedQuantity") or 0)
                ours = [p for p in seed["products"] if p["shop"] == shop]
                got = {get_id(i): i.get("unitsSold") for i in listing(c, "/products", {"shopId": shop})}
                bad = [f"{p['id']}: {got.get(p['id'])!r} != {want.get(p['id'], 0)}" for p in ours
                       if num(got.get(p["id"])) != want.get(p["id"], 0)]
                assert not bad, f"{shop}: {len(bad)} products with a wrong unitsSold, e.g. {bad[:3]}"
        s.check("seeded_units_sold", seeded_units)

        def seeded_sort():
            for shop in sorted({p["shop"] for p in seed["products"]}):
                got = [num(i.get("unitsSold")) for i in listing(c, "/products", {"shopId": shop, "sort": "bestselling"})]
                assert got and None not in got, f"{shop}: no unitsSold values"
                assert got == sorted(got, reverse=True), f"{shop}: not sorted by unitsSold, most first"  # type: ignore[type-var]
        s.check("seeded_sort_bestselling", seeded_sort)

    return s.results
