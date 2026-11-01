"""Checks introduced by the `item_catalog` phase (equipment types shared by many items)."""
from __future__ import annotations

import datetime as dt

import httpx

from harness.suite import Suite, get_id, listing, ok, rejected, status
from scenarios.inspections.checks.helpers import (add_finding, all_findings, fresh_company, get_item, walk_form,
                                                  walk_sub)

SUBMITTED = "2027-02-01T09:00:00Z"


def v15(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v15")
    st = s.state

    def types_url(company: str | None = None) -> str:
        return f"/companies/{company or s.need('company')}/item-types"

    def set_type(sid: str, iid: str, code: str | None) -> httpx.Response:
        return c.put(f"/submissions/{sid}/items/{iid}/type", json={"code": code})

    def fixtures():
        company, other = fresh_company(c, "harbour-gate"), fresh_company(c, "harbour-gate-west")
        walk_form(c, other)
        for code, name, days in (("EXT-CO2-6", "6 kg CO2 extinguisher", 180), ("ALARM-PANEL", "Alarm panel", 365)):
            ok(c.post(f"/companies/{company}/item-types", json={"code": code, "name": name, "intervalDays": days}),
               f"type {code}")
        ok(c.post(f"/companies/{other}/item-types", json={"code": "FOREIGN-1", "name": "Foreign", "intervalDays": 30}),
           "other company's type")
        st.update(company=company, other=other)
        st["sub"] = walk_sub(c, walk_form(c, company), at=SUBMITTED)
        st["finding"] = get_id(add_finding(c, st["sub"], "i2", {"title": "Pressure low", "severity": "medium",
                                                                "dueDate": "2027-07-01"}))
    s.check("create_types", fixtures)

    def list_types():
        got = {t.get("code"): (t.get("name"), t.get("intervalDays")) for t in listing(c, types_url())}
        assert got == {"EXT-CO2-6": ("6 kg CO2 extinguisher", 180), "ALARM-PANEL": ("Alarm panel", 365)}, f"types {got}"
    s.check("list_types", list_types)

    invalid = {"lowercase_code": {"code": "ext-1", "name": "x", "intervalDays": 1},
               "missing_name": {"code": "EXT-2", "intervalDays": 1},
               "zero_interval": {"code": "EXT-3", "name": "x", "intervalDays": 0}}
    for name, body in invalid.items():
        s.check(f"rejects_type_{name}", lambda body=body, name=name: rejected(c.post(types_url(), json=body), name))
    s.check("rejects_duplicate_code", lambda: status(c.post(types_url(), json={
        "code": "EXT-CO2-6", "name": "Again", "intervalDays": 10}), 409, "duplicate code"))

    def due(days: int) -> str:
        return (dt.date.fromisoformat(SUBMITTED[:10]) + dt.timedelta(days=days)).isoformat()

    def assign():
        got = ok(set_type(s.need("sub"), "i2", "EXT-CO2-6"), "set type")
        shown = {k: (got.get("type") or {}).get(k) for k in ("code", "name", "intervalDays")}
        assert shown == {"code": "EXT-CO2-6", "name": "6 kg CO2 extinguisher", "intervalDays": 180}, \
            f"type {got.get('type')}"
        assert get_item(c, s.need("sub"), "i2").get("nextInspectionDue") == due(180), "nextInspectionDue"
        st["typed"] = True
    s.check("assign_type", assign)

    def untyped():
        got = get_item(c, s.need("sub"), "i1")
        assert got.get("type") is None and got.get("nextInspectionDue") is None, f"untyped item {got.get('type')}"
    s.check("untyped_item", untyped)

    s.check("rejects_unknown_code", lambda: rejected(set_type(s.need("sub"), "i1", "NOPE"), "unknown code"))
    s.check("rejects_other_company_code", lambda: rejected(set_type(s.need("sub"), "i1", "FOREIGN-1"),
                                                           "another company's type"))

    def rename():
        s.need("typed")
        ok(c.patch(f"{types_url()}/EXT-CO2-6", json={"name": "CO2 extinguisher 6 kg"}), "rename type")
        assert (get_item(c, s.need("sub"), "i2").get("type") or {}).get("name") == "CO2 extinguisher 6 kg", \
            "item shows the old name"
        entry = next(f for f in all_findings(c, {"companyId": s.need("company")}) if get_id(f) == s.need("finding"))
        assert entry.get("itemType") == {"code": "EXT-CO2-6", "name": "CO2 extinguisher 6 kg"}, \
            f"finding search itemType {entry.get('itemType')}"
    s.check("rename_shows_everywhere", rename)

    def interval():
        s.need("typed")
        ok(c.patch(f"{types_url()}/EXT-CO2-6", json={"intervalDays": 90}), "change interval")
        assert get_item(c, s.need("sub"), "i2").get("nextInspectionDue") == due(90), "nextInspectionDue not updated"
    s.check("interval_change_shows_everywhere", interval)

    def type_filter():
        s.need("typed")
        got = [get_id(f) for f in all_findings(c, {"companyId": s.need("company"), "typeCode": "EXT-CO2-6"})]
        assert got == [s.need("finding")], f"typeCode filter {got}"
        assert not all_findings(c, {"companyId": s.need("company"), "typeCode": "ALARM-PANEL"}), "untyped finding matched"
    s.check("finding_search_by_type", type_filter)

    def delete():
        s.need("typed")
        status(c.delete(f"{types_url()}/EXT-CO2-6"), 409, "deleting a type in use")
        got = ok(set_type(s.need("sub"), "i2", None), "remove type")
        assert got.get("type") is None, f"type {got.get('type')} not removed"
        r = c.delete(f"{types_url()}/EXT-CO2-6")
        assert r.status_code in (200, 204), f"DELETE unused type: HTTP {r.status_code}"
        assert "EXT-CO2-6" not in {t.get("code") for t in listing(c, types_url())}, "type still listed"
    s.check("delete_only_unused_types", delete)

    return s.results
