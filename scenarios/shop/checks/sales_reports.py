"""Checks introduced by the `sales_reports` phase (6: sales reports)."""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable

import httpx

from harness.suite import Suite, as_list, created, get, get_id, listing, mk, ok, rejected
from scenarios.shop.checks.helpers import (by_product, by_variant, fresh_shop, items_of, order_body, place, product,
                                           variants_of)
from scenarios.shop.common import money_eq, parse_time


METRICS = ("orders", "units", "gross", "discounts", "refunds", "net")


def aggregate(rows: list[dict], keys: list[str]) -> dict[tuple, dict]:
    """Expected groups from line rows: {key values..., order, units, gross, discounts, refunds}."""
    out: dict[tuple, dict] = {}
    for r in rows:
        g = out.setdefault(tuple(r.get(k) for k in keys),
                           {"orders": set(), "units": 0, "gross": 0.0, "discounts": 0.0, "refunds": 0.0})
        g["orders"].add(r["order"])
        for m in ("units", "gross", "discounts", "refunds"):
            g[m] += r[m]
    return {k: {**g, "orders": len(g["orders"]), "net": g["gross"] - g["discounts"] - g["refunds"]} for k, g in out.items()}


def report_problem(got: Any, expected: dict[tuple, dict], keys: list[str]) -> str | None:
    groups = got.get("groups") if isinstance(got, dict) else None
    if not isinstance(groups, list):
        return f"no groups list: {str(got)[:150]}"
    seen: dict[tuple, dict] = {}
    for g in groups:
        if not isinstance(g, dict):
            return f"group is not an object: {str(g)[:100]}"
        seen[tuple(g.get(k) for k in keys)] = g
    missing, extra = set(expected) - set(seen), set(seen) - set(expected)
    if missing or extra:
        return f"groups missing {sorted(map(str, missing))[:3]}, unexpected {sorted(map(str, extra))[:3]}"
    for key, want in expected.items():
        g = seen[key]
        for m in METRICS:
            same = g.get(m) == want[m] if m in ("orders", "units") else money_eq(g.get(m), round(want[m], 2))
            if not same:
                return f"group {dict(zip(keys, key))}: {m} {g.get(m)!r}, expected {round(want[m], 2)}"
    return None


def v6(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v6")
    st = s.state
    sh = fresh_shop(c, "evora-studio")

    def setup():
        ok(c.post(f"/shops/{sh}/categories/mugs/attributes",
                  json={"key": "glaze", "label": "Glaze", "type": "select", "options": ["matte", "gloss"]}), "definition")
        ok(c.post(f"/shops/{sh}/discounts", json={"code": "TEN", "type": "percent", "value": 10}), "code")
        mug = mk(c, "/products", product(sh, "MUG", "Studio mug", "mugs", 20, 50, attributes={"glaze": "matte"}), "mug")
        tee = created(c.post("/products", json={
            "shopId": sh, "name": "Studio tee", "category": "tops", "options": ["size"],
            "variants": [{"sku": "TEE-S", "options": {"size": "S"}, "price": 30, "stock": 50},
                         {"sku": "TEE-M", "options": {"size": "M"}, "price": 30, "stock": 50}]}), "tee")
        tv = {sku: get_id(v) for sku, v in variants_of(tee).items()}
        bottle = mk(c, "/products", product(sh, "BOTTLE", "Bottle", "bottles", 15, 50), "bottle")
        o1 = get_id(place(c, order_body(sh, "a@example.pt", [by_product(mug, 2), by_variant(tv["TEE-S"])], discountCode="TEN")))
        o2 = get_id(place(c, order_body(sh, "b@example.pt", [by_variant(tv["TEE-M"], 2)])))
        o3 = get_id(place(c, order_body(sh, "c@example.pt", [by_product(bottle)])))
        ok(c.post(f"/orders/{o3}/cancel"), "cancel")
        o4 = get_id(place(c, order_body(sh, "d@example.pt", [by_product(mug)])))
        created(c.post(f"/orders/{o4}/returns", json={"items": [{"lineId": get_id(items_of(get(c, f"/orders/{o4}"))[0]),
                                                                 "quantity": 1}]}), "return")
        day = {oid: (parse_time(get(c, f"/orders/{oid}").get("createdAt")) or dt.datetime.min).date().isoformat()
               for oid in (o1, o2, o4)}
        tee_id = get_id(tee)
        st.update(mug=mug, tee=tee_id, bottle=bottle, days=sorted(set(day.values())))
        rows = [("o1", o1, mug, None, "mugs", None, "matte", "TEN", 2, 40, 4, 0),
                ("o1", o1, tee_id, tv["TEE-S"], "tops", "S", None, "TEN", 1, 30, 3, 0),
                ("o2", o2, tee_id, tv["TEE-M"], "tops", "M", None, None, 2, 60, 0, 0),
                ("o4", o4, mug, None, "mugs", None, "matte", None, 0, 20, 0, 20)]
        variant_of_mug = get_id((get(c, f"/products/{mug}").get("variants") or [{}])[0])
        st["rows"] = [{"order": oid, "day": day[oid], "month": day[oid][:7], "productId": pid,
                       "variantId": vid or variant_of_mug, "category": cat, "option.size": size, "attr.glaze": glaze,
                       "discountCode": code, "units": units, "gross": gross, "discounts": disc, "refunds": refund}
                      for _, oid, pid, vid, cat, size, glaze, code, units, gross, disc, refund in rows]
    s.check("create_orders", setup)

    def report(keys: list[str], **params: Any) -> Callable[[], None]:
        def fn():
            rows = s.need("rows")
            got = get(c, "/reports/sales", {"shopId": sh, "groupBy": ",".join(keys), **params})
            problem = report_problem(got, aggregate(rows, keys), keys)
            assert not problem, f"groupBy={','.join(keys)}: {problem}"
        return fn
    s.check("report_by_category", report(["category"]))
    s.check("report_by_option", report(["option.size"]))
    s.check("report_by_discount_code", report(["discountCode"]))
    s.check("report_by_product", report(["productId"]))
    s.check("report_by_variant", report(["variantId"]))
    s.check("report_by_attribute", report(["attr.glaze"]))
    s.check("report_by_day_and_month", report(["day", "month"]))
    s.check("report_two_keys", report(["category", "discountCode"]))

    def date_range():
        days = s.need("days")
        report(["category"], **{"from": days[0], "to": days[-1]})()
        after = (dt.date.fromisoformat(days[-1]) + dt.timedelta(days=1)).isoformat()
        before = (dt.date.fromisoformat(days[0]) - dt.timedelta(days=1)).isoformat()
        for params in ({"from": after}, {"to": before}):
            got = get(c, "/reports/sales", {"shopId": sh, "groupBy": "category", **params})
            assert got.get("groups") == [], f"{params}: groups {str(got.get('groups'))[:150]}, expected none"
    s.check("report_date_range", date_range)

    def no_cancelled():
        groups = get(c, "/reports/sales", {"shopId": sh, "groupBy": "productId"}).get("groups") or []
        assert s.need("bottle") not in {g.get("productId") for g in groups}, "the cancelled order's product is in the report"
    s.check("report_excludes_cancelled_orders", no_cancelled)

    invalid = {
        "missing_group_by": {"shopId": sh},
        "unknown_key": {"shopId": sh, "groupBy": "colour"},
        "invalid_date": {"shopId": sh, "groupBy": "category", "from": "2026-13-01"},
        "missing_shop": {"groupBy": "category"},
    }
    for name, params in invalid.items():
        s.check(f"report_rejects_{name}", lambda params=params, name=name: rejected(c.get("/reports/sales", params=params), name))

    def follows():
        ok(c.patch(f"/products/{s.need('tee')}", json={"category": "shirts"}), "PATCH category")
        st["rows"] = [{**r, "category": "shirts" if r["category"] == "tops" else r["category"]} for r in s.need("rows")]
        report(["category"])()
    s.check("report_follows_current_category", follows)

    if seed:
        def seeded(keys: list[str]) -> Callable[[], None]:
            def fn():
                for shop in sorted({p["shop"] for p in seed["products"]}):
                    category = {get_id(p): p.get("category") for p in listing(c, "/products", {"shopId": shop})}
                    rows = []
                    for o in listing(c, "/orders", {"shopId": shop}):
                        if o.get("status") == "cancelled":
                            continue
                        refunds: dict[str, float] = {}
                        if any(x.get("returnedQuantity") for x in items_of(o)):
                            for r in as_list(get(c, f"/orders/{get_id(o)}/returns")):
                                for x in r.get("items") or []:
                                    refunds[x.get("lineId")] = refunds.get(x.get("lineId"), 0) + float(x.get("refund") or 0)
                        created_at = parse_time(o.get("createdAt"))
                        assert created_at, f"order {get_id(o)}: createdAt {o.get('createdAt')!r}"
                        day = created_at.astimezone(dt.timezone.utc).date().isoformat()
                        for x in items_of(o):
                            rows.append({"order": get_id(o), "month": day[:7], "category": category.get(x.get("productId")),
                                         "units": (x.get("quantity") or 0) - (x.get("returnedQuantity") or 0),
                                         "gross": float(x.get("lineTotal") or 0), "discounts": float(x.get("discount") or 0),
                                         "refunds": refunds.get(get_id(x), 0.0)})
                    got = get(c, "/reports/sales", {"shopId": shop, "groupBy": ",".join(keys)})
                    problem = report_problem(got, aggregate(rows, keys), keys)
                    assert not problem, f"{shop}: {problem}"
            return fn
        s.check("seeded_report_by_category", seeded(["category"]))
        s.check("seeded_report_by_month", seeded(["month"]))

    return s.results
