"""Checks introduced by the `craftly_import` phase: importing marketplace listings as products."""
from __future__ import annotations

import copy
import time

import httpx

from harness.suite import Suite, get, get_id, listing, mk, ok, rejected, status
from scenarios.shop.checks.helpers import by_product, by_variant, expect_amount, fresh_shop, order_body, place, product


def price(amount: int, divisor: int = 100) -> dict:
    return {"amount": amount, "divisor": divisor, "currency_code": "EUR"}


EXPORT = {"listings": [
    {"listing_id": 9001, "title": "Speckled mug", "description": "Stoneware", "state": "active",
     "taxonomy_path": ["Home & Living", "Kitchen", "Mugs"], "tags": ["stoneware"], "price": price(2450),
     "quantity": 12, "sku": ["CR-MUG"]},
    {"listing_id": 9002, "title": "Linen tee", "state": "inactive", "taxonomy_path": ["Clothing", "Tops"],
     "price": price(3000),
     "variations": {"properties": [{"property_id": 1, "name": "Size"}, {"property_id": 2, "name": "Colour"}],
                    "offerings": [{"sku": "CR-TEE-S-RED", "values": {"1": "S", "2": "Red"}, "price": price(3000),
                                   "quantity": 4},
                                  {"sku": "CR-TEE-M-RED", "values": {"1": "M", "2": "Red"}, "price": price(3200),
                                   "quantity": 0}]}},
    {"listing_id": 9003, "description": "no title", "taxonomy_path": ["Home"], "price": price(100), "quantity": 1,
     "sku": ["CR-NOTITLE"]},
    {"listing_id": 9004, "title": "Taken sku", "taxonomy_path": ["Home"], "price": price(100), "quantity": 1,
     "sku": ["OWN-SKU"]},
    {"listing_id": 9005, "title": "Tea towel", "taxonomy_path": ["Home", "Textiles"], "price": price(12500, 1000),
     "quantity": 3, "sku": ["CR-TOWEL"]},
]}


def v14(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v14")
    st = s.state
    sh = fresh_shop(c, "craftly-maker")

    def post(body: dict, timeout: float = 30) -> dict:
        return ok(c.post(f"/shops/{sh}/imports/craftly", json=body, timeout=timeout), "import")

    def by_listing() -> dict[str, dict]:
        everything = listing(c, "/products", {"shopId": sh}) + listing(c, "/products", {"shopId": sh, "active": "false"})
        return {str((p.get("source") or {}).get("listingId")): p for p in everything if p.get("source")}

    def first():
        mk(c, "/products", product(sh, "OWN-SKU", "Own product", "home", 5, 5), "own product")
        got = post(EXPORT)
        counts = {k: got.get(k) for k in ("created", "updated", "unchanged")}
        assert counts == {"created": 3, "updated": 0, "unchanged": 0}, f"counts {counts}"
        rejected_ids = sorted(str(r.get("listingId")) for r in got.get("rejected") or [])
        assert rejected_ids == ["9003", "9004"], f"rejected {rejected_ids}"
    s.check("import_counts", first)

    def products():
        got = by_listing()
        assert set(got) == {"9001", "9002", "9005"}, \
            f"products of the shop with source.listingId: {sorted(got) or 'none'}, expected 9001, 9002 and 9005 " \
            f"(GET /products must show each imported product's source)"
        mug, tee, towel = got["9001"], got["9002"], got["9005"]
        assert (mug.get("name"), mug.get("category"), mug.get("description"), mug.get("stock"), mug.get("active")) == \
            ("Speckled mug", "mugs", "Stoneware", 12, True), f"mug {str(mug)[:300]}"
        expect_amount(mug.get("price"), 24.5, "mug price")
        expect_amount(towel.get("price"), 12.5, "price with divisor 1000")
        assert mug.get("source") == {"marketplace": "craftly", "listingId": "9001"}, f"source {mug.get('source')}"
        assert tee.get("options") == ["size", "colour"] and tee.get("active") is False, f"tee {str(tee)[:300]}"
        vs = {v.get("sku"): v for v in tee.get("variants") or []}
        assert vs.get("CR-TEE-S-RED", {}).get("options") == {"size": "S", "colour": "Red"}, "variant options"
        expect_amount(vs.get("CR-TEE-M-RED", {}).get("price"), 32, "offering price")
        assert get(c, f"/products/{get_id(mug)}/source") == EXPORT["listings"][0], "source listing not kept exactly"
        st.update(mug=get_id(mug), tee=get_id(tee), tee_s=get_id(vs.get("CR-TEE-S-RED")))
    s.check("listings_become_products", products)

    def inactive():
        tee = s.need("tee")
        rejected(c.post("/orders", json=order_body(sh, "x@example.pt", [by_variant(s.need("tee_s"))])), "ordering inactive")
        assert tee not in {get_id(p) for p in (get(c, "/products/search", {"shopId": sh, "q": "linen"}).get("results") or [])}, \
            "inactive product in search"
        assert tee in {get_id(p) for p in listing(c, "/products", {"shopId": sh, "active": "false"})}, "active=false"
        assert tee not in {get_id(p) for p in listing(c, "/products", {"shopId": sh, "active": "true"})}, "active=true"
    s.check("inactive_products", inactive)

    def again():
        got = post(EXPORT)
        counts = {k: got.get(k) for k in ("created", "updated", "unchanged")}
        assert counts == {"created": 0, "updated": 0, "unchanged": 3}, f"second import {counts}"
    s.check("reimport_changes_nothing", again)

    def updates():
        place(c, order_body(sh, "x@example.pt", [by_product(s.need("mug"), 2)]))
        export = copy.deepcopy(EXPORT)
        mug, tee = export["listings"][0], export["listings"][1]
        mug.update(title="Speckled mug XL", price=price(2600), quantity=50)
        tee["state"] = "active"
        tee["variations"]["offerings"].append({"sku": "CR-TEE-L-RED", "values": {"1": "L", "2": "Red"},
                                               "price": price(3400), "quantity": 7})
        got = post(export)
        counts = {k: got.get(k) for k in ("created", "updated", "unchanged")}
        assert counts == {"created": 0, "updated": 2, "unchanged": 1}, f"import with changes {counts}"
        m = get(c, f"/products/{s.need('mug')}")
        assert m.get("name") == "Speckled mug XL" and m.get("stock") == 10, \
            f"mug after update: {m.get('name')!r}, stock {m.get('stock')!r} (stock must stay the shop's: 10)"
        expect_amount(m.get("price"), 26, "updated price")
        t = get(c, f"/products/{s.need('tee')}")
        vs = {v.get("sku"): v.get("stock") for v in t.get("variants") or []}
        assert t.get("active") is True and vs.get("CR-TEE-L-RED") == 7 and len(vs) == 3, f"tee after update {vs}"
        assert get(c, f"/products/{s.need('mug')}/source")["price"] == price(2600), "source not updated"
        ok(c.patch(f"/products/{s.need('mug')}", json={"active": False}), "PATCH active")
        status(c.post("/orders", json=order_body(sh, "x@example.pt", [by_product(s.need("mug"))])), 400,
               "ordering a product made inactive")
    s.check("reimport_updates_but_keeps_stock", updates)

    def bulk():
        big = fresh_shop(c, "craftly-bulk")
        listings = [{"listing_id": 100000 + i, "title": f"Item {i}", "taxonomy_path": ["Shop", f"Cat {i % 5}"],
                     "price": price(1000 + i), "quantity": i % 7, "sku": [f"BULK-{i}"]} for i in range(2000)]
        start = time.monotonic()
        got = ok(c.post(f"/shops/{big}/imports/craftly", json={"listings": listings}, timeout=90), "bulk import")
        took = time.monotonic() - start
        assert got.get("created") == 2000, f"created {got.get('created')!r} of 2000"
        assert took <= 60, f"2,000 listings took {took:.0f} s"
        counts = [len(listing(c, "/products", {"shopId": big, "category": f"cat {k}"})) for k in range(5)]
        assert counts == [400] * 5, f"products per category {counts}"
    s.check("bulk_import_2000_listings", bulk)
    return s.results
