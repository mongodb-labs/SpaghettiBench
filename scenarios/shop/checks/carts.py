"""Checks introduced by the `carts` phase: carts that hold stock for a while."""
from __future__ import annotations

import time

import httpx

from harness.suite import Suite, created, get, get_id, listing, mk, ok, rejected, status
from scenarios.shop.checks.helpers import (burst, by_product, fresh_shop, items_of, order_body, product, variants_of,
                                           wins)
from scenarios.shop.common import parse_time

HOLD = 5  # the shortest hold the spec allows


def v10(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v10")
    st = s.state
    sh = fresh_shop(c, "drop-shop")

    def cart(shop: str = sh, email: str = "fan@example.pt") -> str:
        got = created(c.post("/carts", json={"shopId": shop, "customerEmail": email}), "cart")
        assert got.get("status") == "active" and got.get("items") == [], f"new cart {str(got)[:200]}"
        return get_id(got)  # type: ignore[return-value]

    def put(cid: str, vid: str, q: int) -> httpx.Response:
        return c.put(f"/carts/{cid}/items/{vid}", json={"quantity": q})

    def available(pid: str) -> tuple:
        p = get(c, f"/products/{pid}")
        return p.get("stock"), p.get("available")

    def settings():
        assert get(c, f"/shops/{sh}/settings").get("cartHoldSeconds") == 900, "default hold"
        for bad in (4, 86401, "60"):
            rejected(c.put(f"/shops/{sh}/settings", json={"cartHoldSeconds": bad}), f"hold {bad!r}")
        ok(c.put(f"/shops/{sh}/settings", json={"cartHoldSeconds": 600}), "PUT settings")
        assert get(c, f"/shops/{sh}/settings").get("cartHoldSeconds") == 600, "hold not stored"
    s.check("hold_settings", settings)

    def fixtures():
        pid = mk(c, "/products", product(sh, "DROP-1", "Drop tee", "tees", 30, 5), "product")
        engraved = mk(c, "/products", product(sh, "ENGR-1", "Engraved mug", "mugs", 10, 5, personalization=[
            {"key": "name", "label": "Name", "type": "text"}]), "personalised product")
        st.update(pid=pid, vid=get_id(next(iter(variants_of(get(c, f"/products/{pid}")).values()))),
                  engraved=engraved)
    s.check("create_fixtures", fixtures)

    def holds():
        pid, vid = s.need("pid"), s.need("vid")
        a = cart()
        got = ok(put(a, vid, 3), "add 3")
        item = (got.get("items") or [{}])[0]
        assert (item.get("variantId"), item.get("quantity")) == (vid, 3), f"cart items {got.get('items')}"
        assert abs(float(got.get("total") or 0) - 90) < 0.01, f"cart total {got.get('total')!r}"
        assert available(pid) == (5, 2), f"stock/available {available(pid)}, expected (5, 2)"
        b = cart(email="other@example.pt")
        status(put(b, vid, 3), 409, "holding more than is available")
        ok(put(b, vid, 2), "add the last 2")
        assert available(pid) == (5, 0), f"stock/available {available(pid)}, expected (5, 0)"
        status(c.post("/orders", json=order_body(sh, "x@example.pt", [by_product(pid)])), 409, "order of held units")
        assert pid not in {get_id(p) for p in listing(c, "/products", {"shopId": sh, "inStock": "true"})}, \
            "inStock lists a product whose stock is all held"
        ok(put(b, vid, 0), "remove item")
        assert available(pid) == (5, 2), "removing an item didn't release it"
        status(c.delete(f"/carts/{b}"), 204, "DELETE cart")
        st["a"] = a
    s.check("carts_hold_stock", holds)

    def renew():
        a, vid = s.need("a"), s.need("vid")
        before = parse_time(get(c, f"/carts/{a}").get("expiresAt"))
        time.sleep(1.1)
        after = parse_time(ok(put(a, vid, 2), "change quantity").get("expiresAt"))
        assert before and after and after > before, f"expiresAt not renewed: {before} -> {after}"
    s.check("changes_renew_the_hold", renew)

    def checkout():
        a, pid = s.need("a"), s.need("pid")
        o = created(c.post(f"/carts/{a}/checkout", json={}), "checkout")
        assert [(x.get("productId"), x.get("quantity")) for x in items_of(o)] == [(pid, 2)], f"order items {items_of(o)}"
        assert available(pid) == (3, 3), f"after checkout: stock/available {available(pid)}, expected (3, 3)"
        assert get(c, f"/carts/{a}").get("status") == "checked_out", "cart status"
        status(c.post(f"/carts/{a}/checkout", json={}), 409, "second checkout")
        status(put(a, s.need("vid"), 1), 409, "changing a checked-out cart")
        rejected(c.post(f"/carts/{cart()}/checkout", json={}), "empty cart")
        rejected(put(cart(), get_id(next(iter(variants_of(get(c, f"/products/{s.need('engraved')}")).values()))), 1),
                 "personalised product")  # type: ignore[arg-type]
    s.check("checkout", checkout)

    def concurrent():
        pid = mk(c, "/products", product(sh, "RUSH-1", "Rush tee", "tees", 10, 4), "product")
        vid = get_id(next(iter(variants_of(get(c, f"/products/{pid}")).values())))
        carts = [cart(email=f"fan{i}@example.pt") for i in range(10)]
        won = wins(burst(10, lambda i: put(carts[i], vid, 1)))  # type: ignore[arg-type]
        assert len(won) == 4, f"{len(won)} carts got one of 4 units"
        assert available(pid) == (4, 0), f"stock/available {available(pid)}, expected (4, 0)"
    s.check("concurrent_carts_never_over_hold", concurrent)

    def expiry():
        sh2 = fresh_shop(c, "drop-expiry")
        pid = mk(c, "/products", product(sh2, "EXP-1", "Expiring tee", "tees", 10, 3), "product")
        ok(c.put(f"/shops/{sh2}/settings", json={"cartHoldSeconds": HOLD}), "short hold")
        vid = get_id(next(iter(variants_of(get(c, f"/products/{pid}")).values())))
        a = cart(sh2)
        ok(put(a, vid, 3), "hold everything")
        assert available(pid) == (3, 0), "not held"
        time.sleep(HOLD + 1.5)
        assert get(c, f"/carts/{a}").get("status") == "expired", "cart not expired"
        assert available(pid) == (3, 3), f"expired hold still counted: stock/available {available(pid)}"
        created(c.post("/orders", json=order_body(sh2, "x@example.pt", [by_product(pid, 3)])), "order after expiry")
        status(c.post(f"/carts/{a}/checkout", json={}), 409, "checkout of an expired cart")
        status(put(a, vid, 1), 409, "changing an expired cart")
    s.check("expired_holds_free_stock_at_once", expiry)
    return s.results
