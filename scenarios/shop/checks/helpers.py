"""Helpers shared by the shop checks."""
from __future__ import annotations

import datetime as dt
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import httpx

from harness.suite import created, get, get_id, listing, strip_nulls
from scenarios.shop.common import is_num, money_eq


NORTHLOOM_MATERIALS = {"cotton", "linen", "wool", "other"}


def fresh_shop(c: httpx.Client, base: str) -> str:
    """First unused shop id of the form base, base-2, ... so repeated runs never share fixtures."""
    for n in range(1, 1000):
        shop = base if n == 1 else f"{base}-{n}"
        if not listing(c, "/products", {"shopId": shop}) and not listing(c, "/orders", {"shopId": shop}):
            return shop
    raise AssertionError(f"no unused shop id for {base}")


def uid() -> str:
    return uuid.uuid4().hex[:8].upper()


def iso(days: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def num(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def product(shop: str, sku: str, name: str, category: str, price: float, stock: int, **kw: Any) -> dict:
    return {"shopId": shop, "sku": sku, "name": name, "category": category, "price": price, "stock": stock, **kw}


def order_body(shop: str, email: str, items: list[dict], **kw: Any) -> dict:
    return {"shopId": shop, "customerEmail": email, "items": items, **kw}


def by_product(pid: str, qty: int = 1, **kw: Any) -> dict:
    return {"productId": pid, "quantity": qty, **kw}


def by_variant(vid: str, qty: int = 1, **kw: Any) -> dict:
    return {"variantId": vid, "quantity": qty, **kw}


def place(c: httpx.Client, body: dict, what: str = "order") -> dict:
    return created(c.post("/orders", json=body), what)


def items_of(order: Any) -> list:
    items = order.get("items") if isinstance(order, dict) else None
    assert isinstance(items, list), f"order has no items list: {str(order)[:150]}"
    return items


def same_field(key: str, sent: Any, got: Any) -> bool:
    if key == "customerEmail":
        return isinstance(got, str) and got.lower() == sent.lower()
    if is_num(sent):
        return money_eq(got, sent)
    if isinstance(sent, dict):
        return isinstance(strip_nulls(got or {}), dict) and values_equal(sent, got or {})
    return sent == got


def values_equal(sent: dict, got: Any) -> bool:
    got = strip_nulls(got or {})
    return isinstance(got, dict) and set(got) == set(sent) and all(
        (money_eq(got[k], v) if is_num(v) else got[k] == v) for k, v in sent.items())


def fields_match(sent: dict, got: Any) -> list[str]:
    """What differs, as 'key: sent X, got Y'; null in the response counts as omitted."""
    if not isinstance(got, dict):
        return [f"expected an object, got {str(got)[:120]}"]
    return [f"{k}: sent {v!r}, got {got.get(k)!r}"[:200]
            for k, v in sent.items() if v is not None and not same_field(k, v, got.get(k))]


def line_problem(want: list[dict], order: Any) -> str | None:
    """Items are matched by sku: the spec doesn't fix their order (apps may sort them, e.g. to take stock in id order)."""
    got = items_of(order)
    if len(got) != len(want):
        return f"{len(got)} items, expected {len(want)}"
    by_sku = {g.get("sku"): g for g in got if isinstance(g, dict)}
    for w in want:
        g = by_sku.get(w["sku"])
        if g is None:
            return f"no item with sku {w['sku']!r}"
        bad = fields_match(w, g)
        if bad:
            return f"item {w['sku']}: {bad[0]}"
    return None


def expect_amount(got: Any, want: float, what: str) -> None:
    assert money_eq(got, want), f"{what}: {got!r}, expected {want}"


def stock_is(c: httpx.Client, pid: str, want: int, what: str = "") -> None:
    got = get(c, f"/products/{pid}").get("stock")
    assert got == want, f"product {pid}{what}: stock {got!r}, expected {want}"


def variants_of(p: dict) -> dict[str, dict]:
    vs = p.get("variants")
    assert isinstance(vs, list), f"product {get_id(p)}: no variants list"
    return {v.get("sku"): v for v in vs}


def variant_stock(c: httpx.Client, pid: str, sku: str) -> Any:
    return variants_of(get(c, f"/products/{pid}")).get(sku, {}).get("stock")


def burst(n: int, fn: Callable[[int], httpx.Response]) -> list[httpx.Response]:
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, range(n)))


def wins(rs: list[httpx.Response], loser: Callable[[int], bool] = lambda code: code == 409) -> list[dict]:
    """The successful responses of a burst; every other one must be a clean rejection."""
    bad = [r.status_code for r in rs if r.status_code not in (200, 201) and not loser(r.status_code)]
    assert not bad, f"unexpected statuses {sorted(bad)}"
    return [r.json() for r in rs if r.status_code in (200, 201)]


def seed_orders(c: httpx.Client, seed: dict, st: dict) -> dict[str, dict]:
    """The app's current view of every seeded order, fetched once per suite."""
    if "seed_orders" not in st:
        st["seed_orders"] = {o["id"]: get(c, f"/orders/{o['id']}") for o in seed["orders"]}
    return st["seed_orders"]


def seed_stock(c: httpx.Client, seed: dict, st: dict) -> dict[str, int]:
    """Seeded stock minus what the (non-cancelled) seeded orders still hold. Checks never order seeded products."""
    left = {p["id"]: p["stock"] for p in seed["products"]}
    for got in seed_orders(c, seed, st).values():
        if got.get("status") == "cancelled":
            continue
        for line in items_of(got):
            if line.get("productId") in left:
                left[line["productId"]] -= (line.get("quantity") or 0) - (line.get("returnedQuantity") or 0)
    return left


def seed_status(o: dict) -> tuple[str, ...]:
    special = {"late_cancel": ("placed", "cancelled"), "late_return": ("placed", "partially_returned", "returned")}
    return special.get(o["key"], ("cancelled",) if o["cancelled"] else ("placed",))
