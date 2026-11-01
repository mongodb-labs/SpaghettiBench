"""Checks introduced by the `subscriptions` phase: recurring orders placed by a daily run."""
from __future__ import annotations

import datetime as dt

import httpx

from harness.suite import Suite, created, get, get_id, listing, mk, ok, rejected, same_set, status
from scenarios.shop.checks.helpers import burst, expect_amount, fresh_shop, items_of, product, variants_of


def day(offset: int) -> str:
    return (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=offset)).isoformat()


def v12(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v12")
    st = s.state
    sh = fresh_shop(c, "sock-club")

    def run(date: str) -> dict:
        got = ok(c.post(f"/shops/{sh}/subscriptions/run", json={"date": date}), f"run {date}")
        assert isinstance(got.get("placed"), list) and isinstance(got.get("skipped"), list), f"run result {got}"
        return got

    def sub(sid: str) -> dict:
        return get(c, f"/subscriptions/{sid}")

    def fixtures():
        pid = mk(c, "/products", product(sh, "SOCKS-1", "Wool socks", "socks", 12, 10), "product")
        vid = get_id(next(iter(variants_of(get(c, f"/products/{pid}")).values())))
        base = {"shopId": sh, "customerEmail": "ana@example.pt", "items": [{"variantId": vid, "quantity": 2}],
                "everyWeeks": 2, "startsOn": day(1)}
        got = created(c.post("/subscriptions", json=base), "subscription")
        assert (got.get("status"), got.get("nextOn"), got.get("history")) == ("active", day(1), []), str(got)[:300]
        big = mk(c, "/subscriptions", {**base, "customerEmail": "bo@example.pt",
                                       "items": [{"variantId": vid, "quantity": 100}]}, "subscription")
        st.update(pid=pid, vid=vid, sid=get_id(got), big=big, base=base)
    s.check("create_subscriptions", fixtures)

    def places():
        sid, big = s.need("sid"), s.need("big")
        assert run(day(0))["placed"] == [], "placed an order before the first date"
        got = run(day(1))
        assert len(got["placed"]) == 1 and got["skipped"] == [big], f"first run {got}"
        o = get(c, f"/orders/{got['placed'][0]}")
        assert o.get("subscriptionId") == sid and [x.get("quantity") for x in items_of(o)] == [2], f"order {str(o)[:200]}"
        info = sub(sid)
        assert info.get("nextOn") == day(15), f"nextOn {info.get('nextOn')!r}, expected {day(15)}"
        history = [(h.get("date"), h.get("orderId")) for h in info.get("history") or []]
        assert history == [(day(1), got["placed"][0])], f"history {info.get('history')}"
        skipped = sub(big).get("history") or [{}]
        assert skipped[0].get("date") == day(1) and skipped[0].get("skipped") and not skipped[0].get("orderId"), \
            f"skipped history {skipped}"
        assert sub(big).get("nextOn") == day(15), "a skipped subscription should move on too"
        st["first"] = got["placed"][0]
    s.check("run_places_due_orders", places)

    def repeat():
        again = run(day(1))
        assert again["placed"] == [] and again["skipped"] == [], f"second run for the same date {again}"
        assert len(listing(c, "/orders", {"shopId": sh, "subscriptionId": s.need("sid")})) == 1, "duplicate order"
    s.check("same_date_twice_places_nothing", repeat)

    def concurrent():
        sid = s.need("sid")
        rs = burst(6, lambda i: c.post(f"/shops/{sh}/subscriptions/run", json={"date": day(15)}))
        assert all(r.status_code == 200 for r in rs), f"statuses {sorted(r.status_code for r in rs)}"
        orders = listing(c, "/orders", {"shopId": sh, "subscriptionId": sid})
        assert len(orders) == 2, f"{len(orders)} orders after concurrent runs, expected 2"
        assert get(c, f"/products/{s.need('pid')}").get("stock") == 6, "stock after two orders of 2"
    s.check("concurrent_runs_place_one_order", concurrent)

    def catch_up():
        sid = s.need("sid")
        ok(c.patch(f"/products/{s.need('pid')}", json={"price": 15}), "new price")
        got = run(day(60))
        mine = [o for o in got["placed"] if get(c, f"/orders/{o}").get("subscriptionId") == sid]
        assert len(mine) == 1, f"{len(mine)} orders for a subscription several dates behind, expected 1"
        expect_amount(items_of(get(c, f"/orders/{mine[0]}"))[0].get("unitPrice"), 15, "current price")
        assert sub(sid).get("nextOn") == day(71), f"nextOn {sub(sid).get('nextOn')!r}, expected {day(71)}"
    s.check("late_run_places_one_order_at_current_price", catch_up)

    def pause():
        vid = s.need("vid")
        old = mk(c, "/subscriptions", {"shopId": sh, "customerEmail": "cy@example.pt", "everyWeeks": 1,
                                       "items": [{"variantId": vid, "quantity": 1}], "startsOn": day(-20)}, "subscription")
        ok(c.patch(f"/subscriptions/{old}", json={"status": "paused"}), "pause")
        assert old not in {o.get("subscriptionId") for o in [get(c, f"/orders/{x}") for x in run(day(100))["placed"]]}, \
            "a paused subscription got an order"
        ok(c.patch(f"/subscriptions/{old}", json={"status": "active"}), "resume")
        assert sub(old).get("nextOn") == day(1), f"resumed nextOn {sub(old).get('nextOn')!r}, expected {day(1)}"
        ok(c.patch(f"/subscriptions/{old}", json={"status": "cancelled"}), "cancel")
        status(c.patch(f"/subscriptions/{old}", json={"status": "active"}), 409, "changing a cancelled subscription")
        same_set(listing(c, "/subscriptions", {"shopId": sh, "status": "cancelled"}), {old}, "status filter")
        same_set(listing(c, "/subscriptions", {"shopId": sh, "customerEmail": "ANA@example.pt"}), {s.need("sid")},
                 "customerEmail filter")
    s.check("pause_resume_cancel", pause)

    def rejects():  # last: a wrongly accepted subscription must not disturb the checks above
        base, vid = s.need("base"), s.need("vid")
        before = len(listing(c, "/subscriptions", {"shopId": sh}))
        for what, bad in (("everyWeeks 0", {"everyWeeks": 0}), ("everyWeeks 53", {"everyWeeks": 53}),
                          ("bad date", {"startsOn": "tomorrow"}), ("no items", {"items": []}),
                          ("personalisation", {"items": [{"variantId": vid, "quantity": 1, "personalization": {"x": 1}}]})):
            rejected(c.post("/subscriptions", json={**base, **bad}), what)
        assert len(listing(c, "/subscriptions", {"shopId": sh})) == before, "a rejected subscription was stored"
    s.check("rejects_invalid_subscriptions", rejects)
    return s.results
