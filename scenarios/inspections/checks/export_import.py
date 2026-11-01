"""Checks introduced by the `export_import` phase (a form with everything as one JSON, imported with new ids)."""
from __future__ import annotations

import copy

import httpx

from harness.suite import Suite, as_list, difference, get_id, ok, rejected
from scenarios.inspections.common import subset
from scenarios.inspections.checks.helpers import (WALK_ANSWERS, WALK_FORM, add_finding, all_findings, doc_tree,
                                                  export_tree, first_difference, fresh_company, get_doc, get_item,
                                                  items_with_paths, late, patch_finding, patch_item, walk_form,
                                                  walk_sub)

V2 = {**WALK_FORM, "name": "Plant Room Walk (2027)",
      "fields": [*WALK_FORM["fields"], {"key": "weather", "label": "Weather", "type": "text"}]}


def v18(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v18")
    st = s.state

    def export(fid: str) -> dict:
        return ok(c.get(f"/forms/{fid}/export"), f"GET /forms/{fid}/export")

    def forms_of(company: str) -> list:
        return as_list(ok(c.get("/forms", params={"companyId": company}), "GET /forms"))

    def fixtures():
        company = fresh_company(c, "summit-housing")
        ok(c.post(f"/companies/{company}/item-types", json={"code": "DOOR-1", "name": "Fire door", "intervalDays": 180}),
           "item type")
        fid = walk_form(c, company)
        ok(c.put(f"/forms/{fid}", json={**V2, "companyId": company}), "new form version")
        body = {"submittedBy": "k.mensah", "submittedAt": "2027-07-01T08:00:00Z", "formVersion": 1}
        s1 = get_id(ok(c.post(f"/forms/{fid}/submissions", json={**body, "answers": copy.deepcopy(WALK_ANSWERS)}),
                       "submission on version 1"))
        s2 = walk_sub(c, fid, answers={**copy.deepcopy(WALK_ANSWERS), "weather": "Rain"}, at="2027-08-01T08:00:00Z")
        ok(c.put(f"/submissions/{s1}/items/i2/type", json={"code": "DOOR-1"}), "set type")
        f1 = get_id(add_finding(c, s1, "i1", {"title": "Sounder dead", "severity": "high", "assignee": "w-1",
                                              "dueDate": "2027-09-01"}))
        ok(c.post(f"/submissions/{s1}/items/i1/findings/{f1}/comments", json={"author": "w-1", "text": "On it"}), "comment")
        f2 = get_id(add_finding(c, s2, "r3", {"title": "Door wedged open", "severity": "medium"}))
        ok(patch_finding(c, s2, "r3", f2, {"status": "resolved"}), "resolve")
        st.update(company=company, form=fid, s1=s1, s2=s2)
    s.check("create_fixtures", fixtures)

    def shape():
        got = export(s.need("form"))
        versions = (got.get("form") or {}).get("versions") or []
        want = [{k: WALK_FORM[k] for k in ("name", "fields")}, {k: V2[k] for k in ("name", "fields")}]
        assert subset(want, versions), f"form versions differ: {difference(want, versions)}"
        types = [{k: t.get(k) for k in ("code", "name", "intervalDays")} for t in got.get("itemTypes") or []]
        assert types == [{"code": "DOOR-1", "name": "Fire door", "intervalDays": 180}], f"itemTypes {types}"
        subs = {get_id(d): d for d in got.get("submissions") or []}
        assert set(subs) == {s.need("s1"), s.need("s2")}, f"{len(subs)} submissions exported"
        for sid, d in subs.items():
            first_difference(doc_tree(get_doc(c, sid)), doc_tree(d), f"exported submission {sid}")
        st["export"] = got
    s.check("export_has_everything", shape)

    def import_other():
        target = fresh_company(c, "summit-north")
        got = ok(c.post(f"/companies/{target}/import", json=s.need("export")), "import")
        fid, mapping = got.get("formId"), got.get("submissions") or {}
        assert fid and fid != s.need("form"), f"formId {fid!r}"
        assert set(mapping) == {s.need("s1"), s.need("s2")} and not set(mapping.values()) & set(mapping), \
            f"submissions mapping {mapping}"
        assert [get_id(f) for f in forms_of(target)] == [fid], "imported form not listed for the company"
        st.update(target=target, imported=fid, mapping=mapping)
    s.check("import_into_other_company", import_other)

    def same_content():
        for old, new in s.need("mapping").items():
            a, b = get_doc(c, old), get_doc(c, new)
            first_difference(doc_tree(a), doc_tree(b), f"imported copy of {old}")
            shared = set(a.get("items") or {}) & set(b.get("items") or {})
            assert not shared, f"imported items keep their ids: {sorted(shared)[:3]}"
            fa = {get_id(f) for e in a["items"].values() for f in e.get("findings") or []}
            fb = {get_id(f) for e in b["items"].values() for f in e.get("findings") or []}
            assert len(fb) == len(fa) and not fa & fb, "imported findings keep their ids"
            assert b.get("docRev") == 1 and {e.get("rev") for e in b["items"].values()} == {1}, \
                f"docRev {b.get('docRev')!r} and item revs of an imported submission"
    s.check("imported_submissions_match", same_content)

    def round_trip():
        first_difference(export_tree(s.need("export")), export_tree(export(s.need("imported"))), "re-exported form")
    s.check("round_trip_same_document", round_trip)

    def types_and_use():
        target, mapping = s.need("target"), s.need("mapping")
        types = {t.get("code"): (t.get("name"), t.get("intervalDays"))
                 for t in as_list(ok(c.get(f"/companies/{target}/item-types"), "types"))}
        assert types == {"DOOR-1": ("Fire door", 180)}, f"types of the target company {types}"
        new = mapping[s.need("s1")]
        doc = get_doc(c, new)
        iid = next(i["id"] for g, i, _ in items_with_paths(doc["answers"]) if g == "items" and i.get("kind") == "extinguisher")
        assert (get_item(c, new, iid).get("type") or {}).get("code") == "DOOR-1", "imported item lost its type"
        ok(patch_item(c, new, iid, {"notes": "After import"}), "PATCH an imported item")
        hist = [e.get("action") for e in ok(c.get(f"/submissions/{new}/items/{iid}/history"), "history")]
        assert hist[:1] == ["created"] and "updated" in hist, f"history of an imported item {hist}"
        listed = {(f.get("submissionId"), f.get("title")) for f in all_findings(c, {"companyId": target})}
        assert (new, "Sounder dead") in listed, "imported finding not in the company's finding search"
    s.check("imported_data_usable", types_and_use)

    def same_company():
        company = s.need("company")
        got = ok(c.post(f"/companies/{company}/import", json=s.need("export")), "import into the same company")
        assert got.get("formId") not in (None, s.need("form")), "import replaced the original form"
        assert len(forms_of(company)) == 2, f"{len(forms_of(company))} forms, expected 2"
        codes = [t.get("code") for t in as_list(ok(c.get(f"/companies/{company}/item-types"), "types"))]
        assert codes == ["DOOR-1"], f"types after importing into the same company {codes}"
    s.check("import_into_same_company", same_company)

    def invalid():
        empty = fresh_company(c, "summit-empty")
        broken = copy.deepcopy(s.need("export"))
        last = max(broken["submissions"], key=lambda d: d.get("submittedAt"))
        last["answers"].pop("site_name")
        rejected(c.post(f"/companies/{empty}/import", json=broken), "an export with invalid answers")
        no_types = {**copy.deepcopy(s.need("export")), "itemTypes": []}
        rejected(c.post(f"/companies/{empty}/import", json=no_types), "an export with an unknown type code")
        assert not forms_of(empty), "a rejected import created a form"
        assert not as_list(ok(c.get(f"/companies/{empty}/item-types"), "types")), "a rejected import created types"
    s.check("invalid_import_creates_nothing", invalid)

    if late(seed):
        def seeded():
            fid = late(seed)["forms"][0]["id"]
            got = export(fid)
            ids = {get_id(d) for d in got.get("submissions") or []}
            missing = {x["id"] for x in late(seed)["submissions"]} - ids
            assert not missing, f"{len(missing)} of Tidewater's submissions not exported"
            target = fresh_company(c, "tidewater-archive")
            new = ok(c.post(f"/companies/{target}/import", json=got), "import Tidewater's form")
            first_difference(export_tree(got), export_tree(export(new.get("formId"))), "re-exported Tidewater form")
        s.check("seeded_form_round_trip", seeded)

    return s.results
