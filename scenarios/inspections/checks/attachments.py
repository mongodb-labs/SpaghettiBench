"""Checks introduced by the `attachments` phase (photos, readings and documents on items, filtered per kind)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, as_list, created, get_id, ok, rejected, status
from scenarios.inspections.checks.helpers import comparable, fresh_company, get_doc, get_item, walk_form, walk_sub

PHOTO = {"kind": "photo", "url": "https://img.example/1.jpg", "takenAt": "2027-09-29T10:02:00Z",
         "marks": [{"x": 0.4, "y": 0.7, "label": "Rust"}]}
CO = {"kind": "reading", "sensor": "co", "unit": "ppm",
      "values": [{"at": "2027-09-29T10:05:00Z", "value": 12.5}, {"at": "2027-09-29T10:06:00Z", "value": 48}]}
TEMP = {"kind": "reading", "sensor": "temp", "unit": "C", "values": [{"at": "2027-09-29T10:07:00Z", "value": 5}]}
REPORT = {"kind": "document", "url": "https://docs.example/r.pdf", "title": "Service report", "pages": 3}


def v22(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v22")
    st = s.state

    def attach(sid: str, iid: str, body: dict) -> httpx.Response:
        return c.post(f"/submissions/{sid}/items/{iid}/attachments", json=body)

    def search(**params: str) -> list[str]:
        got = ok(c.get("/attachments", params={"companyId": s.need("company"), **params}), f"GET /attachments {params}")
        return sorted(get_id(a) for a in as_list(got))  # type: ignore[type-var]

    def fixtures():
        company, other = fresh_company(c, "saltmarsh"), fresh_company(c, "saltmarsh-east")
        sid = walk_sub(c, walk_form(c, company))
        ids = {name: get_id(created(attach(sid, iid, body), f"attach {name}"))
               for name, iid, body in (("photo", "i1", PHOTO), ("co", "i2", CO), ("temp", "i3", TEMP), ("report", "r1", REPORT))}
        foreign = walk_sub(c, walk_form(c, other))
        created(attach(foreign, "i2", CO), "other company's reading")
        st.update(company=company, sid=sid, ids=ids)
    s.check("create_fixtures", fixtures)

    def on_items():
        sid, ids = s.need("sid"), s.need("ids")
        for iid, name, body in (("i1", "photo", PHOTO), ("i2", "co", CO), ("r1", "report", REPORT)):
            got = get_item(c, sid, iid).get("attachments") or []
            assert [get_id(a) for a in got] == [ids[name]], f"attachments of {iid}: {[get_id(a) for a in got]}"
            assert comparable({k: got[0].get(k) for k in body}) == comparable(body) and got[0].get("createdAt"), \
                f"stored {name} {got[0]}"
        second = get_id(created(attach(sid, "i2", TEMP), "second attachment"))
        got = [get_id(a) for a in get_item(c, sid, "i2").get("attachments") or []]
        assert got == [ids["co"], second], f"order {got}, expected oldest first"
        status(c.delete(f"/submissions/{sid}/items/i2/attachments/{second}"), 204, "DELETE attachment")
        assert [get_id(a) for a in get_item(c, sid, "i2").get("attachments") or []] == [ids["co"]], "deleted attachment listed"
        status(c.delete(f"/submissions/{sid}/items/i2/attachments/{second}"), 404, "DELETE it again")
    s.check("attachments_on_items", on_items)

    def invalid():
        sid = s.need("sid")
        for name, body in (("unknown_kind", {"kind": "video", "url": "https://v.example/1"}),
                           ("photo_without_url", {k: v for k, v in PHOTO.items() if k != "url"}),
                           ("mark_outside_photo", {**PHOTO, "marks": [{"x": 1.5, "y": 0.2, "label": "x"}]}),
                           ("reading_without_values", {**CO, "values": []}),
                           ("reading_value_not_number", {**CO, "values": [{"at": "2027-09-29T10:05:00Z", "value": "high"}]}),
                           ("document_zero_pages", {**REPORT, "pages": 0}),
                           ("field_of_another_kind", {**PHOTO, "pages": 2})):
            rejected(attach(sid, "i4", body), name)
        status(attach(sid, "no-such-item", REPORT), 404, "attachment on an unknown item")
        assert not get_item(c, sid, "i4").get("attachments"), "a rejected attachment was stored"
    s.check("rejects_invalid_attachments", invalid)

    def searches():
        ids, sid = s.need("ids"), s.need("sid")
        assert search(kind="reading") == sorted([ids["co"], ids["temp"]]), "kind=reading"
        assert search() == sorted(ids.values()), "all of the company's attachments (and no other company's)"
        assert search(mark="rust") == [ids["photo"]] and search(mark="crack") == [], "mark filter"
        assert search(sensor="co", above="40") == [ids["co"]] and search(sensor="co", above="50") == [], "sensor and above"
        assert search(above="10") == [ids["co"]], "above"
        entry = next(a for a in as_list(ok(c.get("/attachments", params={"companyId": s.need("company"), "kind": "document"}),
                                           "search")))
        assert (entry.get("submissionId"), entry.get("itemId")) == (sid, "r1"), f"submissionId/itemId {entry}"
        assert search(submissionId="no-such-submission") == [], "submissionId filter"
    s.check("search_attachments", searches)

    def everywhere():
        sid, ids = s.need("sid"), s.need("ids")
        doc = get_doc(c, sid)
        assert [get_id(a) for a in doc["items"]["i1"].get("attachments") or []] == [ids["photo"]], "not in the document"
        ok(c.put(f"/submissions/{sid}/document", params={"ifDocRev": doc["docRev"]},
                 json={"answers": {**doc["answers"], "site_name": "Pier 5"}}), "upload")
        assert get_item(c, sid, "i1").get("attachments"), "an upload removed attachments"
        extra = get_id(created(attach(sid, "i4", REPORT), "attach"))
        now = get_doc(c, sid)
        assert now.get("docRev") == doc["docRev"] + 2, "adding an attachment doesn't count in docRev"
        ok(c.post(f"/submissions/{sid}/restore", json={"docRev": doc["docRev"]}), "restore")
        assert not get_item(c, sid, "i4").get("attachments"), "restore didn't remove a later attachment"
        assert extra not in search(), "removed attachment still listed"
        new = ok(c.post(f"/submissions/{sid}/reinspect", json={"submittedBy": "a", "submittedAt": "2027-09-29T12:00:00Z"}),
                 "reinspect")
        assert not any(e.get("attachments") for e in (new.get("items") or {}).values()), "re-inspection copied attachments"
        export = ok(c.get(f"/forms/{doc['formId']}/export"), "export")
        mine = next(d for d in export.get("submissions") or [] if get_id(d) == sid)
        kinds = sorted(a.get("kind") for e in mine["items"].values() for a in e.get("attachments") or [])
        assert kinds == ["document", "photo", "reading", "reading"], f"exported attachments {kinds}"
    s.check("attachments_part_of_the_item", everywhere)

    return s.results
