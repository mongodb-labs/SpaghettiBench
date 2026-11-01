"""Checks introduced by the `order_edits` phase: changing placed orders with versions and revisions."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get, get_id, listing, mk, ok, rejected, status
from scenarios.shop.checks.helpers import (burst, by_product, by_variant, expect_amount, fresh_shop, items_of, order_body,
                                           place, product, variant_stock, variants_of)


def v13(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v13")
    st = s.state
    sh = fresh_shop(c, "edit-shop")

    def fixtures():
        tee = created(c.post("/products", json={
            "shopId": sh, "name": "Tee", "category": "tees", "options": ["size"],
            "variants": [{"sku": f"E-TEE-{z}", "options": {"size": z}, "price": 20, "stock": 10} for z in "SML"]}), "tee")
        mug = mk(c, "/products", product(sh, "E-MUG", "Mug", "mugs", 10, 10, personalization=[
            {"key": "engraving", "label": "Engraving", "type": "text"}]), "mug")
        v = {sku[-1]: get_id(x) for sku, x in variants_of(tee).items()}
        o = place(c, order_body(sh, "ana@example.pt", [by_variant(v["S"], 2),
                                                       by_product(mug, 1, personalization={"engraving": "Ana"})]))
        assert o.get("version") == 1, f"new order version {o.get('version')!r}"
        st.update(tee=get_id(tee), mug=mug, v=v, order=o)
    s.check("orders_have_versions", fixtures)

    def lines(o: dict) -> dict[str, dict]:
        return {x.get("sku"): x for x in items_of(o)}

    def edit(oid: str, version: int, items: list[dict]) -> httpx.Response:
        return c.patch(f"/orders/{oid}", json={"version": version, "items": items})

    def edits():
        o, v, mug, tee = s.need("order"), s.need("v"), s.need("mug"), s.need("tee")
        mug_line = lines(o)["E-MUG"]
        got = ok(edit(get_id(o), 1, [by_variant(v["M"], 2), by_product(mug, 3, personalization={"engraving": "Ana"})]),
                 "edit")
        new = lines(got)
        assert set(new) == {"E-TEE-M", "E-MUG"} and new["E-MUG"].get("quantity") == 3, f"lines {list(new)}"
        assert get_id(new["E-MUG"]) == get_id(mug_line), "a line that stays must keep its id"
        assert (got.get("version"), got.get("number"), got.get("createdAt")) == (2, o.get("number"), o.get("createdAt")), \
            "version, number or createdAt"
        expect_amount(got.get("total"), 70, "new total")
        stocks = {z: variant_stock(c, tee, f"E-TEE-{z}") for z in "SM"}
        assert stocks == {"S": 10, "M": 8} and get(c, f"/products/{mug}").get("stock") == 7, f"stock after edit {stocks}"
    s.check("edit_lines_and_stock", edits)

    def prices():
        o, v, mug, tee = s.need("order"), s.need("v"), s.need("mug"), s.need("tee")
        ok(c.patch(f"/products/{tee}/variants/{v['M']}", json={"price": 25}), "price M")
        ok(c.patch(f"/products/{tee}/variants/{v['L']}", json={"price": 25}), "price L")
        got = ok(edit(get_id(o), 2, [by_variant(v["M"], 3), by_variant(v["L"], 1),
                                     by_product(mug, 3, personalization={"engraving": "Ana"})]), "edit again")
        new = lines(got)
        expect_amount(new["E-TEE-M"].get("unitPrice"), 20, "kept line keeps its price")
        expect_amount(new["E-TEE-L"].get("unitPrice"), 25, "new line at the current price")
    s.check("kept_lines_keep_their_price", prices)

    def conflicts():
        o, v = s.need("order"), s.need("v")
        oid = get_id(o)
        status(edit(oid, 2, [by_variant(v["S"], 1)]), 409, "edit with an old version")
        status(edit(oid, 3, [by_variant(v["S"], 11)]), 409, "not enough stock")
        rejected(edit(oid, 3, [by_variant(v["S"], 0)]), "invalid quantity")
        got = get(c, f"/orders/{oid}")
        assert got.get("version") == 3 and "E-TEE-S" not in lines(got), "a rejected edit changed the order"
        assert variant_stock(c, s.need("tee"), "E-TEE-S") == 10, "a rejected edit changed the stock"
    s.check("rejected_edits_change_nothing", conflicts)

    def revisions():
        o = s.need("order")
        revs = listing(c, f"/orders/{get_id(o)}/revisions")
        assert [r.get("version") for r in revs] == [1, 2], f"revision versions {[r.get('version') for r in revs]}"
        assert sorted(lines(revs[0])) == ["E-MUG", "E-TEE-S"] and lines(revs[0])["E-TEE-S"].get("quantity") == 2, \
            "first revision is not the order as placed"
        ok(c.post(f"/orders/{get_id(o)}/cancel"), "cancel")
        got = get(c, f"/orders/{get_id(o)}")
        assert got.get("version") == 4 and len(listing(c, f"/orders/{get_id(o)}/revisions")) == 3, "cancel is a change"
        status(edit(get_id(o), 4, [by_variant(s.need("v")["S"], 1)]), 409, "editing a cancelled order")
    s.check("revisions_and_cancel", revisions)

    def concurrent():
        v = s.need("v")
        o = place(c, order_body(sh, "bo@example.pt", [by_variant(v["S"], 1)]))
        rs = burst(2, lambda i: edit(get_id(o), 1, [by_variant(v["S"], 2 + i)]))  # type: ignore[arg-type]
        codes = sorted(r.status_code for r in rs)
        assert codes == [200, 409], f"two edits of the same version: {codes}, expected one 200 and one 409"
        got = get(c, f"/orders/{get_id(o)}")
        assert got.get("version") == 2, f"version {got.get('version')!r}"
    s.check("concurrent_edits_one_wins", concurrent)

    def discount():
        v = s.need("v")
        ok(c.post(f"/shops/{sh}/discounts", json={"code": "TEN", "type": "percent", "value": 10, "minSubtotal": 30}), "code")
        o = place(c, order_body(sh, "cy@example.pt", [by_variant(v["S"], 2)], discountCode="TEN"))
        expect_amount((o.get("discount") or {}).get("amount"), 4, "discount on 40")
        got = ok(edit(get_id(o), 1, [by_variant(v["S"], 3)]), "edit with a code")
        expect_amount((got.get("discount") or {}).get("amount"), 6, "discount on 60")
        expect_amount(got.get("total"), 54, "total after the edit")
        rejected(edit(get_id(o), 2, [by_variant(v["S"], 1)]), "below the code's minimum")
        ret = get(c, f"/orders/{get_id(o)}")
        created(c.post(f"/orders/{get_id(o)}/returns", json={"items": [{"lineId": get_id(items_of(ret)[0]), "quantity": 1}]}),
                "return")
        assert get(c, f"/orders/{get_id(o)}").get("version") == 3, "a return is a change"
        assert len([x for x in listing(c, "/orders", {"shopId": sh}) if x.get("number") == o.get("number")]) == 1, \
            "order number duplicated"
    s.check("discount_recomputed", discount)
    return s.results
