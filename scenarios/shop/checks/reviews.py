"""Checks introduced by the `reviews` phase: reviews by buyers, moderation, votes and ratings."""
from __future__ import annotations

import time

import httpx

from harness.suite import Suite, created, get, get_id, listing, mk, ok, rejected, status
from scenarios.shop.checks.helpers import by_product, fresh_shop, order_body, place, product


def v7(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v7")
    st = s.state
    sh = fresh_shop(c, "porto-ceramics")

    def review(key: str, email: str, rating: int, **kw) -> str:
        time.sleep(0.02)  # newest-first ordering needs distinct creation times
        return get_id(created(c.post(f"/products/{st['p'][key]}/reviews",
                                     json={"customerEmail": email, "rating": rating, "text": f"{key} by {email}", **kw}),
                              f"review of {key}"))  # type: ignore[return-value]

    def fixtures():
        st["p"] = {k: mk(c, "/products", product(sh, k.upper(), name, "home", 20, 50), name)
                   for k, name in (("mug", "Mug"), ("bowl", "Bowl"), ("vase", "Vase"), ("jug", "Jug"), ("plate", "Plate"))}
        p = st["p"]
        for email, keys in (("ana@example.pt", ["mug", "bowl", "jug"]), ("ben@example.pt", ["mug", "jug"]),
                            ("eve@example.pt", ["mug"]), ("dee@example.pt", ["vase"])):
            place(c, order_body(sh, email, [by_product(p[k]) for k in keys]))
        cancelled = place(c, order_body(sh, "cy@example.pt", [by_product(p["mug"])]))
        ok(c.post(f"/orders/{get_id(cancelled)}/cancel"), "cancel")
        first = created(c.post(f"/products/{p['mug']}/reviews", json={
            "customerEmail": "ana@example.pt", "rating": 5, "title": "Lovely", "text": "Holds a big coffee."}), "review")
        assert (first.get("rating"), first.get("status"), first.get("helpful")) == (5, "published", 0), str(first)[:300]
        st["r"] = {"ana_mug": get_id(first), "ben_mug": review("mug", "ben@example.pt", 4),
                   "eve_mug": review("mug", "eve@example.pt", 4), "ana_bowl": review("bowl", "ana@example.pt", 3),
                   "dee_vase": review("vase", "dee@example.pt", 4), "ana_jug": review("jug", "ana@example.pt", 4),
                   "ben_jug": review("jug", "BEN@example.pt", 4)}
    s.check("create_reviews", fixtures)

    def rejects():
        mug = s.need("p")["mug"]
        base = {"customerEmail": "ana@example.pt", "rating": 4, "text": "ok"}
        for what, body in (("rating 6", {"rating": 6}), ("rating 4.5", {"rating": 4.5}), ("empty text", {"text": ""}),
                           ("long title", {"title": "x" * 101}), ("bad email", {"customerEmail": "ana"})):
            rejected(c.post(f"/products/{mug}/reviews", json={**base, **body}), what)
        for email in ("cy@example.pt", "never@example.pt"):
            status(c.post(f"/products/{mug}/reviews", json={**base, "customerEmail": email}), 403, f"review by {email}")
        status(c.post(f"/products/{mug}/reviews", json={**base, "customerEmail": "ANA@example.pt"}), 409, "second review")
    s.check("rejects_invalid_and_unverified_reviews", rejects)

    def ratings(want: dict[str, tuple[float | None, int]]) -> None:
        p = s.need("p")
        listed = {get_id(x): x for x in listing(c, "/products", {"shopId": sh})}
        for k, (avg, count) in want.items():
            for where, prod in (("GET", get(c, f"/products/{p[k]}")), ("list", listed.get(p[k], {}))):
                r = prod.get("rating") or {}
                got = r.get("average")
                good = got is None if avg is None else isinstance(got, (int, float)) and abs(got - avg) <= 0.051
                assert good and r.get("count") == count, f"{k} ({where}): rating {r}, expected {avg} from {count}"

    s.check("ratings_on_products", lambda: ratings({"mug": (13 / 3, 3), "bowl": (3, 1), "vase": (4, 1), "jug": (4, 2),
                                                     "plate": (None, 0)}))

    def listed(params: dict) -> list[str]:
        return [get_id(r) for r in listing(c, f"/products/{s.need('p')['mug']}/reviews", params, limit=100)]

    def moderation():
        r = s.need("r")
        ok(c.patch(f"/reviews/{r['ben_mug']}", json={"status": "hidden"}), "hide review")
        ratings({"mug": (4.5, 2)})
        assert r["ben_mug"] not in listed({}), "hidden review still listed"
        ok(c.patch(f"/reviews/{r['ben_mug']}", json={"status": "published"}), "publish again")
        ratings({"mug": (13 / 3, 3)})
        got = ok(c.patch(f"/reviews/{r['ana_bowl']}", json={"rating": 1, "title": "Chipped"}), "edit review")
        assert got.get("rating") == 1 and got.get("title") == "Chipped", str(got)[:200]
        ratings({"bowl": (1, 1)})
        rejected(c.patch(f"/reviews/{r['ana_bowl']}", json={"status": "deleted"}), "unknown status")
    s.check("moderation_and_edits_update_ratings", moderation)

    def helpful():
        r = s.need("r")
        for email in ("x@example.pt", "X@EXAMPLE.PT", "y@example.pt"):
            got = ok(c.post(f"/reviews/{r['ben_mug']}/helpful", json={"customerEmail": email}), "vote")
        assert got.get("helpful") == 2, f"helpful {got.get('helpful')!r}, expected 2 (one vote per email)"
        ok(c.post(f"/reviews/{r['eve_mug']}/helpful", json={"customerEmail": "x@example.pt"}), "vote")
        assert listed({}) == [r["eve_mug"], r["ben_mug"], r["ana_mug"]], f"newest first: {listed({})}"
        assert listed({"sort": "helpful"}) == [r["ben_mug"], r["eve_mug"], r["ana_mug"]], \
            f"most helpful first: {listed({'sort': 'helpful'})}"
        assert listed({"rating": 5}) == [r["ana_mug"]], f"rating=5: {listed({'rating': 5})}"
    s.check("helpful_votes_and_review_lists", helpful)

    def sorting():
        p = s.need("p")
        got = [get_id(x) for x in listing(c, "/products", {"shopId": sh, "sort": "rating"})]
        want = [p[k] for k in ("mug", "jug", "vase", "bowl", "plate")]
        assert got == want, f"sort=rating: {got}, expected mug, jug, vase, bowl, plate"
        got = {get_id(x) for x in listing(c, "/products", {"shopId": sh, "minRating": 4})}
        assert got == {p["mug"], p["jug"], p["vase"]}, "minRating=4"
        got = {get_id(x) for x in listing(c, "/products", {"shopId": sh, "minRating": 4.2})}
        assert got == {p["mug"]}, "minRating=4.2"
    s.check("filter_and_sort_by_rating", sorting)

    def follows_orders():
        p = s.need("p")
        o = place(c, order_body(sh, "late@example.pt", [by_product(p["plate"])]))
        ok(c.post(f"/orders/{get_id(o)}/cancel"), "cancel")
        status(c.post(f"/products/{p['plate']}/reviews", json={"customerEmail": "late@example.pt", "rating": 5,
                                                              "text": "x"}), 403, "review after cancelling the order")
    s.check("cancelled_orders_dont_count", follows_orders)
    return s.results
