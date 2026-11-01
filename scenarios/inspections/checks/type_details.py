"""Checks introduced by the `type_details` phase (per-type nested fields on items, searchable)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, as_list, difference, get_id, ok, rejected
from scenarios.inspections.checks.helpers import fresh_company, get_doc, get_item, walk_form, walk_sub
from scenarios.inspections.common import strip_item_ids, subset

FIRE_DOOR = {"code": "FIRE-DOOR", "name": "Fire door", "intervalDays": 180, "fields": [
    {"key": "rating", "label": "Rating", "type": "select", "options": ["30", "60", "90"], "required": True},
    {"key": "hinges", "label": "Hinges", "type": "group", "maxItems": 6, "fields": [
        {"key": "position", "label": "Position", "type": "select", "options": ["top", "middle", "bottom"]},
        {"key": "ok", "label": "OK", "type": "boolean", "required": True}]}]}
LIFT = {"code": "LIFT", "name": "Passenger lift", "intervalDays": 90, "fields": [
    {"key": "load_kg", "label": "Load", "type": "number", "min": 0},
    {"key": "cars", "label": "Cars", "type": "group", "fields": [
        {"key": "car_no", "label": "Car", "type": "number", "required": True},
        {"key": "landings", "label": "Landings", "type": "group", "fields": [
            {"key": "floor", "label": "Floor", "type": "text", "required": True},
            {"key": "door_ok", "label": "Door OK", "type": "boolean"}]}]}]}
DOOR_A = {"rating": "60", "hinges": [{"position": "top", "ok": True}, {"position": "bottom", "ok": False}]}
DOOR_B = {"rating": "30", "hinges": [{"position": "top", "ok": True}]}
LIFT_A = {"load_kg": 630, "cars": [{"car_no": 1, "landings": [{"floor": "Ground", "door_ok": True},
                                                              {"floor": "First", "door_ok": False}]}]}


def v21(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v21")
    st = s.state

    def set_type(sid: str, iid: str, body: dict) -> httpx.Response:
        return c.put(f"/submissions/{sid}/items/{iid}/type", json=body)

    def details(sid: str, iid: str) -> dict:
        return strip_item_ids(get_item(c, sid, iid).get("details"))

    def search(**params: str) -> list[tuple[str, str]]:
        r = c.get("/items", params={"companyId": s.need("company"), **params})
        got = ok(r, f"GET /items {params}")
        return sorted((i.get("submissionId"), get_id(i)) for i in as_list(got))

    def fixtures():
        company = fresh_company(c, "ironbridge")
        for t in (FIRE_DOOR, LIFT, {"code": "PLAIN-1", "name": "Plain", "intervalDays": 30}):
            ok(c.post(f"/companies/{company}/item-types", json=t), f"type {t['code']}")
        fid = walk_form(c, company)
        s1, s2 = walk_sub(c, fid), walk_sub(c, fid)
        st.update(company=company, s1=s1, s2=s2)
        for sid, iid, body in ((s1, "i1", {"code": "FIRE-DOOR", "details": DOOR_A}),
                               (s1, "i2", {"code": "FIRE-DOOR", "details": DOOR_B}),
                               (s2, "i4", {"code": "LIFT", "details": LIFT_A}),
                               (s2, "i1", {"code": "PLAIN-1"})):
            ok(set_type(sid, iid, body), f"type and details for {iid}")
    s.check("create_fixtures", fixtures)

    def listed():
        types = {t.get("code"): t for t in as_list(ok(c.get(f"/companies/{s.need('company')}/item-types"), "types"))}
        fields = types["FIRE-DOOR"].get("fields")
        assert subset(FIRE_DOOR["fields"], fields), f"FIRE-DOOR fields differ: {difference(FIRE_DOOR['fields'], fields)}"
        assert types["PLAIN-1"].get("fields") == [], f"fields of a type without fields {types['PLAIN-1'].get('fields')!r}"
    s.check("type_fields_listed", listed)

    def bad_types():
        url = f"/companies/{s.need('company')}/item-types"
        deep = {"key": "a", "label": "A", "type": "group", "fields": [{"key": "b", "label": "B", "type": "group", "fields": [
            {"key": "c", "label": "C", "type": "group", "fields": [{"key": "d", "label": "D", "type": "text"}]}]}]}
        for name, fields in (("groups_three_levels_deep", [deep]),
                             ("field_without_key", [{"label": "X", "type": "text"}]),
                             ("select_without_options", [{"key": "x", "label": "X", "type": "select"}])):
            rejected(c.post(url, json={"code": "BAD-1", "name": "Bad", "intervalDays": 1, "fields": fields}), name)
    s.check("rejects_invalid_type_fields", bad_types)

    def on_items():
        s1 = s.need("s1")
        assert details(s1, "i1") == DOOR_A, f"details {details(s1, 'i1')}"
        hinges = get_item(c, s1, "i1")["details"]["hinges"]
        assert all(h.get("id") for h in hinges), "group items in details have no ids"
        assert get_item(c, s1, "i3").get("details") in ({}, None), "untyped item has details"
        assert get_item(c, s.need("s2"), "i1").get("details") in ({}, None), "type without fields has details"
    s.check("details_on_items", on_items)

    def bad_details():
        s1, s2 = s.need("s1"), s.need("s2")
        for name, sid, body in (
                ("missing_required", s1, {"code": "FIRE-DOOR", "details": {"hinges": [{"ok": True}]}}),
                ("unknown_key", s1, {"code": "FIRE-DOOR", "details": {"rating": "30", "colour": "red"}}),
                ("wrong_option", s1, {"code": "FIRE-DOOR", "details": {"rating": "45"}}),
                ("nested_missing_required", s2, {"code": "LIFT", "details": {"cars": [{"car_no": 2, "landings": [{}]}]}}),
                ("too_many_group_items", s1, {"code": "FIRE-DOOR", "details": {"rating": "30", "hinges": [{"ok": True}] * 7}})):
            rejected(set_type(sid, "i3", body), name)
        assert get_item(c, s1, "i3").get("type") is None, "a rejected change set the type"
    s.check("rejects_invalid_details", bad_details)

    def searches():
        s1, s2 = s.need("s1"), s.need("s2")
        assert search(typeCode="FIRE-DOOR") == sorted([(s1, "i1"), (s1, "i2")]), "typeCode alone"
        assert search(typeCode="FIRE-DOOR", **{"details.hinges.ok": "false"}) == [(s1, "i1")], "details.hinges.ok=false"
        assert search(typeCode="FIRE-DOOR", **{"details.rating": "30"}) == [(s1, "i2")], "details.rating=30"
        assert search(typeCode="FIRE-DOOR", **{"details.hinges.position": "bottom", "details.hinges.ok": "true"}) == [], \
            "conditions on the same group must hold on the same hinge"
        assert search(typeCode="LIFT", **{"details.cars.landings.floor": "ground"}) == [(s2, "i4")], \
            "nested group, text ignoring case"
        assert search(typeCode="LIFT", **{"details.load_kg[gte]": "600"}) == [(s2, "i4")], "range operator"
        assert search(typeCode="LIFT", **{"details.load_kg[gt]": "630"}) == [], "range operator excludes"
        rejected(c.get("/items", params={"companyId": s.need("company")}), "search without typeCode")
    s.check("search_by_details", searches)

    def patch():
        s1 = s.need("s1")
        url = f"/submissions/{s1}/items/i2/details"
        ok(c.patch(url, json={"rating": "90"}), "PATCH details")
        assert details(s1, "i2") == {**DOOR_B, "rating": "90"}, f"details {details(s1, 'i2')}"
        ok(c.patch(url, json={"hinges": [{"position": "middle", "ok": False}]}), "replace a group")
        assert details(s1, "i2") == {"rating": "90", "hinges": [{"position": "middle", "ok": False}]}, \
            f"details {details(s1, 'i2')}"
        rejected(c.patch(url, json={"rating": None}), "removing a required field")
        rejected(c.patch(f"/submissions/{s1}/items/i3/details", json={"rating": "30"}), "details of an untyped item")
        assert details(s1, "i2")["rating"] == "90", "a rejected PATCH changed the details"
    s.check("patch_details", patch)

    def everywhere():
        s1 = s.need("s1")
        doc = get_doc(c, s1)
        assert strip_item_ids(doc["items"]["i1"].get("details")) == DOOR_A, "details missing in the document"
        ok(c.patch(f"/submissions/{s1}/items/i1/details", json={"rating": "90"}), "PATCH details")
        after = get_doc(c, s1)
        assert after.get("docRev") == doc["docRev"] + 1, "a details change doesn't count in docRev"
        ok(c.put(f"/submissions/{s1}/document", params={"ifDocRev": after["docRev"]},
                 json={"answers": after["answers"]}), "upload without items")
        assert details(s1, "i1")["rating"] == "90", "an upload changed details"
        ok(c.post(f"/submissions/{s1}/restore", json={"docRev": doc["docRev"]}), "restore")
        assert details(s1, "i1") == DOOR_A, "restore didn't bring the details back"
        kept = get_doc(c, s1, docRev=after["docRev"])["items"]["i1"].get("details")
        assert strip_item_ids(kept)["rating"] == "90", "kept version without the details"
        export = ok(c.get(f"/forms/{doc['formId']}/export"), "export")
        fd = next((t for t in export.get("itemTypes") or [] if t.get("code") == "FIRE-DOOR"), {})
        assert subset(FIRE_DOOR["fields"], fd.get("fields")), \
            f"exported FIRE-DOOR fields differ: {difference(FIRE_DOOR['fields'], fd.get('fields'))}"
        new = ok(c.post(f"/submissions/{s1}/reinspect", json={"submittedBy": "a", "submittedAt": "2027-09-15T08:00:00Z"}),
                 "reinspect")
        copied = sum(strip_item_ids(e.get("details")) == DOOR_A for e in (new.get("items") or {}).values())
        assert copied == 1, "re-inspection didn't copy the details"
    s.check("details_part_of_the_item", everywhere)

    def change_type():
        s2 = s.need("s2")
        ok(set_type(s2, "i2", {"code": "FIRE-DOOR", "details": DOOR_B}), "set FIRE-DOOR")
        ok(set_type(s2, "i2", {"code": "LIFT", "details": {"cars": [{"car_no": 3}]}}), "change to LIFT")
        assert details(s2, "i2") == {"cars": [{"car_no": 3}]}, f"details after changing the type {details(s2, 'i2')}"
    s.check("changing_type_replaces_details", change_type)

    def fields_change():
        company, s1 = s.need("company"), s.need("s1")
        ok(c.patch(f"/companies/{company}/item-types/FIRE-DOOR", json={"fields": FIRE_DOOR["fields"][:1]}),
           "drop the hinges field")
        assert details(s1, "i1").get("hinges") == DOOR_A["hinges"], "stored details changed with the type's fields"
        rejected(set_type(s1, "i3", {"code": "FIRE-DOOR", "details": DOOR_A}), "details with a removed field")
    s.check("field_changes_keep_stored_details", fields_change)

    return s.results
