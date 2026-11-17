"""Checks of the `vat` change-request probe: tax rates per shop and category, VAT included in order lines,
shipping, returns and sales reports, frozen at order time.

Prices include VAT, so the expected tax of an amount is amount × rate / (100 + rate), rounded half up to the
cent. Where a check depends on discount shares, it takes them from the app's own order (the discount checks
cover those).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from harness.suite import Suite, created, get, get_id, listing, mk, ok, rejected
from scenarios.shop.checks.helpers import (by_product, expect_amount, fresh_shop, items_of, order_body, place, product,
                                           seed_orders, uid)
from scenarios.shop.common import money_eq, round_cents

CENTRE = [[-9.20, 38.69], [-9.10, 38.69], [-9.10, 38.75], [-9.20, 38.75]]
HERE = {"lat": 38.7223, "lng": -9.1393}


def tax_in(amount: Any, rate: float) -> float:
    """The VAT included in a tax-inclusive amount."""
    r = Decimal(str(rate))
    return round_cents(Decimal(str(amount)) * 100 * r / (100 + r)) / 100


def line_of(order: Any, pid: str) -> dict:
    found = [l for l in items_of(order) if l.get("productId") == pid]
    assert len(found) == 1, f"order {get_id(order)}: {len(found)} lines of product {pid}"
    return found[0]


def expect_line(order: Any, pid: str, rate: float, tax: float, what: str) -> None:
    line = line_of(order, pid)
    assert money_eq(line.get("taxRate"), rate), f"{what}: line taxRate {line.get('taxRate')!r}, expected {rate}"
    expect_amount(line.get("tax"), tax, f"{what}: line tax")


def vat(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("vat")
    st = s.state

    def set_rates(shop: str, standard: float, categories: dict | None = None) -> None:
        body: dict = {"standardRate": standard}
        if categories is not None:
            body["categoryRates"] = categories
        ok(c.put(f"/shops/{shop}/tax", json=body), f"PUT /shops/{shop}/tax")

    def products(shop: str, *specs: tuple[str, str, float]) -> list[str]:
        return [mk(c, "/products", product(shop, f"VAT-{uid()}", name, category, price, 100), "product")
                for name, category, price in specs]

    def rates():
        sh = fresh_shop(c, "vat-shop")
        t = get(c, f"/shops/{sh}/tax")
        assert money_eq(t.get("standardRate"), 0) and not t.get("categoryRates"), f"default rates: {t}"
        for what, body in (("rate above 100", {"standardRate": 101}), ("negative rate", {"standardRate": -1}),
                           ("3 decimals", {"standardRate": 2.555}), ("rate as text", {"standardRate": "23"}),
                           ("no standard rate", {"categoryRates": {"mugs": 6}}),
                           ("category rate above 100", {"standardRate": 23, "categoryRates": {"mugs": 150}}),
                           ("categoryRates not an object", {"standardRate": 23, "categoryRates": [6]})):
            rejected(c.put(f"/shops/{sh}/tax", json=body), what)
        set_rates(sh, 23, {"mugs": 6})
        t = get(c, f"/shops/{sh}/tax")
        assert money_eq(t.get("standardRate"), 23) and money_eq((t.get("categoryRates") or {}).get("mugs"), 6), \
            f"rates read back: {t}"
        st["shop"] = sh
    s.check("rates", rates)

    def lines():
        sh = s.need("shop")
        mug, plate = products(sh, ("Mug", "mugs", 21.20), ("Plate", "plates", 12.30))
        o = place(c, order_body(sh, "ana@example.pt", [by_product(mug, 2), by_product(plate, 1)]))
        for got in (o, get(c, f"/orders/{get_id(o)}")):
            expect_line(got, mug, 6, 2.40, "2 mugs at 21.20, 6%")
            expect_line(got, plate, 23, 2.30, "a plate at 12.30, 23%")
            expect_amount(got.get("shippingTax"), 0, "no delivery: shippingTax")
            expect_amount(got.get("tax"), 4.70, "order tax")
            expect_amount(got.get("total"), 54.70, "total (prices include VAT)")
        st.update(mug=mug, plate=plate, order=get_id(o))
    s.check("line_and_order_tax", lines)

    def discounted():
        sh, mug, plate = s.need("shop"), s.need("mug"), s.need("plate")
        ok(c.post(f"/shops/{sh}/discounts", json={"code": "VAT10", "type": "percent", "value": 10}), "discount code")
        o = place(c, order_body(sh, "rui@example.pt", [by_product(mug, 2), by_product(plate, 1)], discountCode="VAT10"))
        total = 0.0
        for pid, rate in ((mug, 6), (plate, 23)):
            line = line_of(o, pid)
            paid = Decimal(str(line.get("lineTotal"))) - Decimal(str(line.get("discount") or 0))
            want = tax_in(paid, rate)
            expect_line(o, pid, rate, want, f"line paying {paid} at {rate}%")
            total += want
        expect_amount(o.get("tax"), round(total, 2), "order tax with a discount")
    s.check("tax_on_what_was_paid_after_discount", discounted)

    def shipping():
        sh, plate = s.need("shop"), s.need("plate")
        ok(c.post(f"/shops/{sh}/delivery-zones", json={"name": "Centre", "area": CENTRE, "fee": 4.92}), "zone")
        o = place(c, order_body(sh, "ines@example.pt", [by_product(plate, 1)],
                                delivery={"type": "shipping", "location": HERE}))
        expect_amount(o.get("shippingFee"), 4.92, "shippingFee")
        expect_amount(o.get("shippingTax"), 0.92, "shippingTax on 4.92 at 23%")
        expect_amount(o.get("tax"), 3.22, "order tax with shipping")
        st["shipped"] = get_id(o)
    s.check("shipping_tax", shipping)

    def checkout():
        sh, mug = s.need("shop"), s.need("mug")
        vid = get_id((get(c, f"/products/{mug}").get("variants") or [{}])[0])
        cart = created(c.post("/carts", json={"shopId": sh, "customerEmail": "cart@example.pt"}), "cart")
        ok(c.put(f"/carts/{get_id(cart)}/items/{vid}", json={"quantity": 1}), "cart item")
        o = created(c.post(f"/carts/{get_id(cart)}/checkout", json={}), "checkout")
        expect_line(o, mug, 6, 1.20, "checked-out mug at 21.20, 6%")
        expect_amount(o.get("tax"), 1.20, "checked-out order tax")
    s.check("cart_checkout_taxed", checkout)

    def frozen():
        sh, mug, plate = s.need("shop"), s.need("mug"), s.need("plate")
        order, shipped = s.need("order"), s.need("shipped")
        set_rates(sh, 21, {"mugs": 13})
        old = get(c, f"/orders/{order}")
        expect_line(old, mug, 6, 2.40, "existing order after the rates changed: mugs")
        expect_line(old, plate, 23, 2.30, "existing order after the rates changed: plate")
        expect_amount(old.get("tax"), 4.70, "existing order tax after the rates changed")
        expect_amount(get(c, f"/orders/{shipped}").get("shippingTax"), 0.92, "existing shippingTax after the change")
        new = place(c, order_body(sh, "ana@example.pt", [by_product(mug, 1), by_product(plate, 1)]))
        expect_line(new, mug, 13, 2.44, "new order, mug at 21.20, 13%")
        expect_line(new, plate, 21, 2.13, "new order, plate at 12.30, 21%")
    s.check("rates_frozen_at_order_time", frozen)

    def edits():
        sh = fresh_shop(c, "vat-edits")
        set_rates(sh, 23, {"mugs": 6})
        mug, plate = products(sh, ("Mug", "mugs", 21.20), ("Plate", "plates", 12.30))
        o = place(c, order_body(sh, "ana@example.pt", [by_product(mug, 1)]))
        set_rates(sh, 21, {"mugs": 13})
        edited = ok(c.patch(f"/orders/{get_id(o)}", json={"version": o.get("version"),
                                                         "items": [by_product(mug, 2), by_product(plate, 1)]}),
                    "edit order")
        expect_line(edited, mug, 6, 2.40, "kept line, now 2 mugs, keeps 6%")
        expect_line(edited, plate, 21, 2.13, "new line gets the current 21%")
        expect_amount(edited.get("tax"), 4.53, "edited order tax")
    s.check("edits_keep_rates_of_kept_lines", edits)

    def returns():
        sh = fresh_shop(c, "vat-returns")
        set_rates(sh, 23)
        [cable] = products(sh, ("Cable", "cables", 9.99))
        o = place(c, order_body(sh, "ana@example.pt", [by_product(cable, 3)]))
        expect_line(o, cable, 23, 5.60, "3 cables at 9.99, 23%")
        line = get_id(line_of(o, cable))
        for want in (1.87, 1.87, 1.86):
            r = created(c.post(f"/orders/{get_id(o)}/returns", json={"items": [{"lineId": line, "quantity": 1}]}),
                        "return")
            items = r.get("items") or []
            assert len(items) == 1, f"return items: {items}"
            expect_amount(items[0].get("tax"), want, "returned cable: item tax")
            expect_amount(r.get("tax"), want, "return tax")
        got = [x.get("tax") for x in listing(c, f"/orders/{get_id(o)}/returns")]
        assert len(got) == 3 and all(money_eq(g, w) for g, w in zip(got, (1.87, 1.87, 1.86))), \
            f"returns read back: tax {got}, expected [1.87, 1.87, 1.86] (the last makes them add up to 5.60)"
    s.check("returns_refund_tax_adding_up", returns)

    def report():
        sh = fresh_shop(c, "vat-report")
        set_rates(sh, 23, {"books": 6})
        prints, book = products(sh, ("Print", "prints", 10.00), ("Book", "books", 12.72))
        o = place(c, order_body(sh, "ana@example.pt", [by_product(prints, 3)]))
        line = get_id(line_of(o, prints))
        created(c.post(f"/orders/{get_id(o)}/returns", json={"items": [{"lineId": line, "quantity": 1}]}), "return")
        place(c, order_body(sh, "rui@example.pt", [by_product(book, 1)]))
        gone = place(c, order_body(sh, "rui@example.pt", [by_product(book, 1)]))
        ok(c.post(f"/orders/{get_id(gone)}/cancel"), "cancel")
        groups = {g.get("productId"): g for g in as_groups(get(c, "/reports/sales", {"shopId": sh, "groupBy": "productId"}))}
        expect_amount((groups.get(prints) or {}).get("tax"), 3.74, "report tax of prints: 5.61 - 1.87 returned")
        expect_amount((groups.get(book) or {}).get("tax"), 0.72, "report tax of books (cancelled order left out)")
    s.check("sales_report_tax", report)

    if seed:
        def seeded():
            bad = []
            for oid, got in seed_orders(c, seed, st).items():
                lines = items_of(got)
                if not (money_eq(got.get("tax"), 0) and money_eq(got.get("shippingTax"), 0)
                        and all(money_eq(l.get("taxRate"), 0) and money_eq(l.get("tax"), 0) for l in lines)):
                    bad.append(f"{oid}: tax {got.get('tax')!r}, shippingTax {got.get('shippingTax')!r}, lines "
                               f"{[(l.get('taxRate'), l.get('tax')) for l in lines]}")
            assert not bad, f"{len(bad)} existing orders don't show 0 tax, e.g. {bad[:3]}"
        s.check("existing_orders_untaxed", seeded)
    return s.results


def as_groups(body: Any) -> list[dict]:
    groups = body.get("groups") if isinstance(body, dict) else None
    assert isinstance(groups, list), f"report without groups: {str(body)[:200]}"
    return groups
