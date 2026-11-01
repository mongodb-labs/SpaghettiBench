"""Checks introduced by the `stock_history` phase: every stock change recorded as a movement."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get, get_id, listing, ok, rejected, status
from scenarios.shop.checks.helpers import (burst, by_variant, fresh_shop, items_of, order_body, place, variants_of,
                                           wins)
from scenarios.shop.common import parse_time

REASONS = {"initial", "order", "cancel", "return", "edit", "adjustment", "opening"}


def v15(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v15")
    st = s.state
    sh = fresh_shop(c, "ledger-shop")

    def moves(vid: str, **params) -> list[dict]:
        return listing(c, f"/variants/{vid}/movements", params, limit=1000)

    def adds_up(vid: str, stock: int | None = None) -> list[dict]:
        ms = moves(vid)
        total, running = 0, []
        for m in ms:
            assert m.get("reason") in REASONS, f"reason {m.get('reason')!r}"
            total += m.get("change") or 0
            running.append((m.get("stockAfter"), total))
        if stock is not None:
            assert total == stock, f"variant {vid}: movements add up to {total}, stock is {stock}"
        assert all(a == b for a, b in running), f"stockAfter doesn't follow the changes: {running}"
        return ms

    def fixtures():
        p = created(c.post("/products", json={
            "shopId": sh, "name": "Ledger tee", "category": "tees", "options": ["size"],
            "variants": [{"sku": "LG-S", "options": {"size": "S"}, "price": 10, "stock": 10},
                         {"sku": "LG-M", "options": {"size": "M"}, "price": 10, "stock": 0}]}), "product")
        v = {sku: get_id(x) for sku, x in variants_of(p).items()}
        ms = moves(v["LG-S"])
        assert [(m.get("reason"), m.get("change"), m.get("stockAfter")) for m in ms] == [("initial", 10, 10)], \
            f"movements of a new variant {ms}"
        st.update(pid=get_id(p), v=v)
    s.check("initial_movement", fixtures)

    def history():
        v, pid = s.need("v"), s.need("pid")
        vs = v["LG-S"]
        o1 = place(c, order_body(sh, "a@example.pt", [by_variant(vs, 3)]))
        ok(c.post(f"/orders/{get_id(o1)}/cancel"), "cancel")
        o2 = place(c, order_body(sh, "b@example.pt", [by_variant(vs, 2)]))
        created(c.post(f"/orders/{get_id(o2)}/returns", json={"items": [{"lineId": get_id(items_of(o2)[0]), "quantity": 1}]}),
                "return")
        o3 = place(c, order_body(sh, "c@example.pt", [by_variant(vs, 1)]))
        ok(c.patch(f"/orders/{get_id(o3)}", json={"version": 1, "items": [by_variant(vs, 2)]}), "edit")
        ok(c.patch(f"/products/{pid}/variants/{vs}", json={"stock": 20}), "set stock")
        cart = created(c.post("/carts", json={"shopId": sh, "customerEmail": "d@example.pt"}), "cart")
        ok(c.put(f"/carts/{get_id(cart)}/items/{vs}", json={"quantity": 1}), "hold")
        got = [(m.get("reason"), m.get("change"), m.get("orderId")) for m in adds_up(vs, 20)]
        want = [("initial", 10, None), ("order", -3, get_id(o1)), ("cancel", 3, get_id(o1)), ("order", -2, get_id(o2)),
                ("return", 1, get_id(o2)), ("order", -1, get_id(o3)), ("edit", -1, get_id(o3)), ("adjustment", 13, None)]
        assert got == want, f"movements {got}"
    s.check("every_change_recorded_in_order", history)

    def adjustments():
        vs = s.need("v")["LG-S"]
        m = created(c.post(f"/variants/{vs}/adjustments", json={"change": -5, "note": "broken in storage"}), "adjustment")
        assert (m.get("reason"), m.get("change"), m.get("stockAfter"), m.get("note")) == \
            ("adjustment", -5, 15, "broken in storage"), f"adjustment {m}"
        rejected(c.post(f"/variants/{vs}/adjustments", json={"change": 0}), "zero change")
        rejected(c.post(f"/variants/{vs}/adjustments", json={"change": 1.5}), "fractional change")
        status(c.post(f"/variants/{vs}/adjustments", json={"change": -1000}), 409, "below zero")
        assert get(c, f"/products/{s.need('pid')}").get("stock") == 15, "stock after adjustments"
        adds_up(vs, 15)
    s.check("adjustments", adjustments)

    def windows():
        vs = s.need("v")["LG-S"]
        ms = moves(vs)
        k = next((i for i in range(1, len(ms) - 1) if ms[i - 1].get("at") != ms[i].get("at") != ms[i + 1].get("at")), None)
        assert k is not None, "every movement shares its timestamp with a neighbour"
        at = ms[k].get("at")
        later = moves(vs, **{"from": at})
        assert [get_id(m) for m in later] == [get_id(m) for m in ms[k:]], "from is inclusive"
        earlier = moves(vs, to=at)
        assert [get_id(m) for m in earlier] == [get_id(m) for m in ms[:k + 1]], "to is inclusive"
        report = get(c, "/reports/stock", {"shopId": sh, "at": at})
        rows = {r.get("sku"): r.get("stock") for r in report.get("variants") or []}
        assert rows == {"LG-S": ms[k].get("stockAfter"), "LG-M": 0}, f"stock at {at}: {rows}"
        rows = {r.get("sku"): r.get("stock") for r in get(c, "/reports/stock", {"shopId": sh, "at": "2000-01-01T00:00:00Z"})
                .get("variants") or []}
        assert rows == {"LG-S": 0, "LG-M": 0}, f"stock before anything existed: {rows}"
        assert parse_time(ms[0].get("at")) <= parse_time(ms[-1].get("at")), "oldest first"  # type: ignore[operator]
    s.check("time_windows_and_stock_at_a_moment", windows)

    def concurrent():
        vm = s.need("v")["LG-M"]
        ok(c.post(f"/variants/{vm}/adjustments", json={"change": 5}), "restock")
        won = wins(burst(10, lambda i: c.post("/orders", json=order_body(sh, f"r{i}@example.pt", [by_variant(vm)]))))
        assert len(won) == 5, f"{len(won)} of 10 orders for 5 units"
        ms = adds_up(vm, 0)
        assert sum(m.get("reason") == "order" for m in ms) == 5, "one order movement per order"
    s.check("movements_add_up_under_concurrency", concurrent)

    def imported():
        got = ok(c.post(f"/shops/{sh}/imports/craftly", json={"listings": [
            {"listing_id": 1, "title": "Imported", "taxonomy_path": ["Home"], "quantity": 4, "sku": ["LG-IMP"],
             "price": {"amount": 500, "divisor": 100, "currency_code": "EUR"}}]}), "import")
        assert got.get("created") == 1, f"import {got}"
        p = next(p for p in listing(c, "/products", {"shopId": sh}) if p.get("sku") == "LG-IMP")
        ms = adds_up(get_id(p["variants"][0]), 4)  # type: ignore[arg-type]
        assert [m.get("reason") for m in ms] == ["initial"], f"imported product movements {ms}"
    s.check("imports_record_initial_stock", imported)

    if seed:
        def seeded():
            bad = []
            for sp in seed["products"]:
                prod = get(c, f"/products/{sp['id']}")
                for v in prod.get("variants") or []:
                    ms = moves(get_id(v))  # type: ignore[arg-type]
                    total = sum(m.get("change") or 0 for m in ms)
                    if total != v.get("stock") or (ms and ms[0].get("reason") not in ("opening", "initial")):
                        bad.append(f"{v.get('sku')}: movements {total}, stock {v.get('stock')}")
            assert not bad, f"{len(bad)} seeded variants whose movements don't add up, e.g. {bad[:3]}"
        s.check("seeded_variants_have_opening_balances", seeded)
    return s.results
