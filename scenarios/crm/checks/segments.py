"""Checks introduced by the `segments` phase: tags, and saved filters run on current data."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get, get_id, listing, mk, ok, rejected, same_set, status
from scenarios.crm.checks.helpers import fresh_agency, person


def cond(field: str, op: str, value) -> dict:
    return {"field": field, "op": op, "value": value}


def v8(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v8")
    st = s.state
    ag = fresh_agency(c, "golf-homes")

    def tags():
        cid = mk(c, "/contacts", person(ag, "Tag", "Tester", tags=["VIP", "Golf Day"]), "contact")
        got = get(c, f"/contacts/{cid}").get("tags")
        assert sorted(got or []) == ["golf day", "vip"], f"tags {got!r}, expected lower case"
        got = ok(c.patch(f"/contacts/{cid}", json={"tags": ["buyer"]}), "PATCH tags").get("tags")
        assert got == ["buyer"], f"PATCH should replace tags, got {got!r}"
        plain = mk(c, "/contacts", person(ag, "No", "Tags"), "contact")
        assert get(c, f"/contacts/{plain}").get("tags") == [], "contact without tags should show []"
        for bad in ([""], ["x" * 41], [7], "vip"):
            rejected(c.post("/contacts", json=person(ag, "Bad", "Tags", tags=bad)), f"tags {bad!r}")
        same_set(listing(c, "/contacts", {"agencyId": ag, "tag": "BUYER"}), {cid}, "?tag=BUYER")
        st["tagged"] = cid
    s.check("tags_on_contacts", tags)

    def merge_tags():
        keep = mk(c, "/contacts", person(ag, "Mia", "Keep", tags=["a", "b"]), "keep")
        dup = mk(c, "/contacts", person(ag, "Mia", "Keep", tags=["c", "a"]), "duplicate")
        got = ok(c.post(f"/contacts/{keep}/merge", json={"duplicateId": dup}), "merge").get("tags")
        assert got == ["a", "b", "c"], f"merged tags {got!r}, expected ['a', 'b', 'c']"
    s.check("merge_combines_tags", merge_tags)

    ag2 = fresh_agency(c, "golf-segments")

    def fixtures():
        ok(c.post(f"/agencies/{ag2}/fields", json={"entity": "contact", "key": "budget", "label": "Budget",
                                                  "type": "number"}), "budget field")
        ok(c.post(f"/agencies/{ag2}/fields", json={"entity": "contact", "key": "kind", "label": "Kind",
                                                  "type": "select", "options": ["buyer", "seller"]}), "kind field")
        co = mk(c, "/companies", {"agencyId": ag2, "name": "Fairway Ltd"}, "company")
        rows = {
            "ana": ("Ana", "Abbot", {"tags": ["buyer", "vip"], "custom": {"budget": 500000, "kind": "buyer"}}),
            "ben": ("Ben", "Baker", {"tags": ["buyer"], "custom": {"budget": 200000}}),
            "cid": ("Cid", "Smith", {"tags": ["buyer"], "custom": {"budget": 900000}}),
            "dot": ("Dot", "Dunn", {"tags": ["seller"], "companyId": co,
                                    "emails": [{"address": "dot@fairway.example", "label": "work"}]}),
            "eli": ("Eli", "Evans", {"title": "Golf Pro", "custom": {"kind": "seller"}}),
        }
        ids = {k: mk(c, "/contacts", person(ag2, first, last, **kw), k) for k, (first, last, kw) in rows.items()}
        mk(c, "/deals", {"agencyId": ag2, "contactId": ids["ben"], "title": "Villa", "value": 300000, "stage": "proposal"},
           "deal")
        mk(c, "/deals", {"agencyId": ag2, "contactId": ids["eli"], "title": "Lot", "value": 100000, "stage": "won"}, "deal")
        mk(c, "/activities", {"agencyId": ag2, "type": "note", "contactId": ids["dot"], "at": "2027-02-10T09:00:00Z",
                              "text": "Met at the club"}, "activity")
        st.update(ids=ids, company=co)
    s.check("create_fixtures", fixtures)

    def run(flt: dict, keys: list[str], ordered: bool = False) -> str:
        ids = s.need("ids")
        seg = created(c.post(f"/agencies/{ag2}/segments", json={"name": "S", "filter": flt}), f"segment {flt}")
        got = listing(c, f"/segments/{get_id(seg)}/contacts")
        want = [ids[k] for k in keys]
        if ordered:
            assert [get_id(x) for x in got] == want, f"{flt}: got {[get_id(x) for x in got]}, expected {want}"
        else:
            same_set(got, set(want), f"segment {flt}")
        count = get(c, f"/segments/{get_id(seg)}/count").get("count")
        assert count == len(keys), f"{flt}: count {count!r}, expected {len(keys)}"
        return get_id(seg)  # type: ignore[return-value]

    s.check("segment_nested_conditions", lambda: run(
        {"all": [cond("tags", "has", "BUYER"),
                 {"any": [cond("openPipeline", "gte", 250000), cond("custom.budget", "gt", 400000)]},
                 {"not": cond("lastName", "eq", "smith")}]}, ["ana", "ben"], ordered=True))

    def fields():
        ids = s.need("ids")
        run(cond("custom.kind", "eq", "seller"), ["eli"])
        run(cond("custom.budget", "exists", False), ["dot", "eli"])
        run(cond("custom.budget", "lte", 500000), ["ana", "ben"])
        run(cond("email", "contains", "FAIRWAY"), ["dot"])
        run(cond("title", "eq", "golf pro"), ["eli"])
        run(cond("companyId", "eq", s.need("company")), ["dot"])
        run(cond("lastActivityAt", "after", "2027-02-01T00:00:00Z"), ["dot"])
        run({"not": cond("lastActivityAt", "before", "2027-02-01T00:00:00Z")}, list(ids))
        run(cond("openPipeline", "eq", 0), ["ana", "cid", "dot", "eli"])
        run({"any": [cond("tags", "has", "vip"), {"all": [cond("firstName", "contains", "o"),
                                                          cond("tags", "has", "seller")]}]}, ["ana", "dot"])
    s.check("segment_fields_and_operators", fields)

    def follows():
        ids = s.need("ids")
        seg = run({"all": [cond("tags", "has", "buyer"), cond("openPipeline", "gt", 0)]}, ["ben"])
        ok(c.patch(f"/contacts/{ids['cid']}", json={"tags": ["buyer", "new"]}), "PATCH tags")
        mk(c, "/deals", {"agencyId": ag2, "contactId": ids["cid"], "title": "Flat", "value": 10, "stage": "lead"}, "deal")
        deals = listing(c, "/deals", {"agencyId": ag2, "contactId": ids["ben"]})
        ok(c.patch(f"/deals/{get_id(deals[0])}", json={"stage": "lost"}), "lose deal")
        same_set(listing(c, f"/segments/{seg}/contacts"), {ids["cid"]}, "segment after changes")
        ok(c.patch(f"/segments/{seg}", json={"filter": cond("tags", "has", "new")}), "PATCH segment")
        same_set(listing(c, f"/segments/{seg}/contacts"), {ids["cid"]}, "segment after PATCH")
        assert get(c, f"/segments/{seg}").get("name") == "S", "PATCH of the filter changed the name"
        assert seg in {get_id(x) for x in listing(c, f"/agencies/{ag2}/segments")}, "segment not listed"
        status(c.delete(f"/segments/{seg}"), 204, "DELETE segment")
        status(c.get(f"/segments/{seg}/contacts"), 404, "contacts of a deleted segment")
    s.check("segment_follows_current_data", follows)

    def rejects():
        before = len(listing(c, f"/agencies/{ag2}/segments"))
        for what, flt in {
            "unknown field": cond("shoeSize", "eq", 3),
            "unknown operator": cond("lastName", "like", "x"),
            "gt on a select field": cond("custom.kind", "gt", "buyer"),
            "value not an option": cond("custom.kind", "eq", "renter"),
            "text for a number": cond("openPipeline", "gte", "lots"),
            "undefined custom field": cond("custom.nope", "exists", True),
            "empty all": {"all": []},
            "bad nested condition": {"any": [cond("tags", "has", "x"), {"not": {"field": "tags"}}]},
            "bad timestamp": cond("lastActivityAt", "after", "yesterday"),
        }.items():
            rejected(c.post(f"/agencies/{ag2}/segments", json={"name": "Bad", "filter": flt}), what)
        assert len(listing(c, f"/agencies/{ag2}/segments")) == before, "a rejected segment was stored"
    s.check("rejects_invalid_filters", rejects)
    return s.results
