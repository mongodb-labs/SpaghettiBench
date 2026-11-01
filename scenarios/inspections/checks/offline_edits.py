"""Checks introduced by the `offline_edits` phase (item revisions, conditional changes, sync)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, ok, status
from scenarios.inspections.checks.helpers import (burst, fetch_sub, fresh_company, get_item, items_with_paths, late,
                                                  patch_item, path_of, walk_form, walk_sub)


def v13(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v13")
    st = s.state
    base = str(c.base_url)

    def fixtures():
        st["form"] = walk_form(c, fresh_company(c, "offshore-sites"))
        st["sub"] = walk_sub(c, st["form"])
    s.check("create_fixtures", fixtures)

    def rev(iid: str) -> object:
        return get_item(c, s.need("sub"), iid).get("rev")

    s.check("new_items_start_at_rev_1", lambda: _equal([rev(i) for i in ("i1", "r1", "c1")], [1, 1, 1], "revs"))

    def increments():
        sid = s.need("sub")
        got = ok(patch_item(c, sid, "i1", {"notes": "First change"}), "PATCH")
        assert got.get("rev") == 2, f"rev {got.get('rev')!r} in the PATCH response, expected 2"
        ok(c.post(f"/submissions/{sid}/items/i1/move", json={"path": path_of([("floors", "f1"), ("rooms", "r2")]),
                                                            "group": "items"}), "move")
        assert rev("i1") == 3, f"rev {rev('i1')!r} after a PATCH and a move, expected 3"
    s.check("patch_and_move_increment_rev", increments)

    def if_rev_match():
        got = ok(patch_item(c, s.need("sub"), "i1", {"notes": "Second change"}, ifRev=3), "PATCH ifRev=3")
        assert got.get("rev") == 4 and got.get("answers", {}).get("notes") == "Second change", f"item {got}"
    s.check("if_rev_match_applies", if_rev_match)

    def if_rev_conflict():
        r = patch_item(c, s.need("sub"), "i1", {"notes": "Stale change"}, ifRev=2)
        status(r, 409, "PATCH with a stale ifRev")
        body = r.json()
        assert body.get("rev") == 4 and (body.get("answers") or {}).get("notes") == "Second change", \
            f"409 body is not the current item: {str(body)[:200]}"
        assert get_item(c, s.need("sub"), "i1")["answers"].get("notes") == "Second change", "stale change applied"
    s.check("if_rev_conflict_409", if_rev_conflict)

    def sync():
        sid = s.need("sub")
        edits = [{"itemId": "i2", "ifRev": 1, "answers": {"notes": "Synced"}},
                 {"itemId": "i1", "ifRev": 1, "answers": {"notes": "Offline, stale"}},
                 {"itemId": "i3", "ifRev": 1, "answers": {"status": "broken"}},
                 {"itemId": "nope", "ifRev": 1, "answers": {"notes": "x"}}]
        got = ok(c.post(f"/submissions/{sid}/items/sync", json={"edits": edits}), "sync")
        results = got.get("results") or []
        assert [r.get("status") for r in results] == [200, 409, 400, 404], f"statuses {[r.get('status') for r in results]}"
        assert [r.get("itemId") for r in results] == ["i2", "i1", "i3", "nope"], "results not in the order of the edits"
        assert (results[0].get("item") or {}).get("rev") == 2 and (results[1].get("item") or {}).get("rev") == 4, \
            "results don't carry the item"
        answers = {i["id"]: i for _, i, _ in items_with_paths(fetch_sub(c, sid)["answers"])}
        assert (answers["i2"].get("notes"), answers["i1"].get("notes"), answers["i3"].get("status")) == \
            ("Synced", "Second change", "pass"), "sync applied the wrong edits"
    s.check("sync_handles_edits_independently", sync)

    def one_winner():
        sid, current = s.need("sub"), get_item(c, s.need("sub"), "i3").get("rev")
        rs = burst(base, 10, lambda k, own: own.patch(f"/submissions/{sid}/items/i3", params={"ifRev": current},
                                                      json={"notes": f"tablet {k}"}))
        codes = sorted(r.status_code for r in rs)
        assert codes == [200] + [409] * 9, f"statuses {codes}, expected one 200 and nine 409"
        assert get_item(c, sid, "i3").get("rev") == current + 1, "rev not increased exactly once"  # type: ignore[operator]
    s.check("concurrent_if_rev_exactly_one_wins", one_winner)

    if late(seed):
        def seeded_revs():
            sub = late(seed)["submissions"][7]
            bad = [i["id"] for _, i, _ in items_with_paths(fetch_sub(c, sub["id"])["answers"])
                   if not isinstance(get_item(c, sub["id"], i["id"]).get("rev"), int)]
            assert not bad, f"{len(bad)} existing items without an integer rev"
        s.check("seeded_items_have_rev", seeded_revs)

    return s.results


def _equal(got: object, want: object, what: str) -> None:
    assert got == want, f"{what} {got!r}, expected {want!r}"
