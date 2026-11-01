"""Checks introduced by the `users_and_roles` phase (users, X-User-Id, company isolation, inspector limits)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get_id, listing, ok, rejected, status
from scenarios.inspections.checks.helpers import (WALK_FORM, add_finding, all_findings, finding_of, fresh_company,
                                                  patch_finding, patch_item, walk_form, walk_sub)


def as_user(uid: str) -> dict:
    return {"X-User-Id": uid}


def v10(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v10")
    st = s.state

    def users():
        a, b = fresh_company(c, "seaview-housing"), fresh_company(c, "hilltop-housing")
        walk_form(c, a)
        walk_form(c, b)

        def user(company: str, name: str, email: str, role: str) -> str:
            return get_id(created(c.post(f"/companies/{company}/users",
                                         json={"name": name, "email": email, "role": role}), f"user {name}"))
        st.update(a=a, b=b, manager=user(a, "Mara Boss", "mara@seaview.example", "manager"),
                  inspector=user(a, "Ivo Field", "ivo@seaview.example", "inspector"),
                  other=user(b, "Otto Hill", "otto@hilltop.example", "manager"))
        listed = {get_id(u): u.get("role") for u in listing(c, f"/companies/{a}/users")}
        assert listed == {st["manager"]: "manager", st["inspector"]: "inspector"}, f"users {listed}"
    s.check("create_and_list_users", users)

    invalid = {"unknown_role": {"name": "X", "email": "x@seaview.example", "role": "admin"},
               "invalid_email": {"name": "X", "email": "x.seaview.example", "role": "inspector"},
               "missing_name": {"email": "y@seaview.example", "role": "inspector"}}
    for name, body in invalid.items():
        s.check(f"rejects_user_{name}", lambda body=body, name=name: rejected(
            c.post(f"/companies/{s.need('a')}/users", json=body), name))
    s.check("rejects_duplicate_email", lambda: status(c.post(f"/companies/{s.need('a')}/users", json={
        "name": "Mara Again", "email": "MARA@seaview.example", "role": "inspector"}), 409, "email used twice"))

    def fixtures():
        st["form"] = get_id(created(c.post("/forms", json={**WALK_FORM, "companyId": s.need("a")},
                                           headers=as_user(s.need("manager"))), "manager creates a form"))
        st["sub"] = walk_sub(c, st["form"])
        st["mine"] = get_id(add_finding(c, st["sub"], "i1", {"title": "Mine", "severity": "low",
                                                             "assignee": s.need("inspector"), "dueDate": "2027-04-01"}))
        st["theirs"] = get_id(add_finding(c, st["sub"], "i2", {"title": "Theirs", "severity": "low",
                                                               "assignee": "someone-else", "dueDate": "2027-04-01"}))
    s.check("manager_creates_form", fixtures)

    s.check("unknown_user_401", lambda: status(c.get(f"/forms/{s.need('form')}", headers=as_user("no-such-user")),
                                               401, "unknown X-User-Id"))
    s.check("without_header_works_as_before", lambda: ok(c.get(f"/forms/{s.need('form')}"), "GET form without X-User-Id"))

    inspector_forbidden = {
        "create_form": lambda h: c.post("/forms", json={**WALK_FORM, "companyId": s.need("a")}, headers=h),
        "edit_form": lambda h: c.put(f"/forms/{s.need('form')}", json={**WALK_FORM, "companyId": s.need("a")}, headers=h),
        "create_user": lambda h: c.post(f"/companies/{s.need('a')}/users",
                                        json={"name": "Z", "email": "z@seaview.example", "role": "manager"}, headers=h),
    }
    for name, call in inspector_forbidden.items():
        s.check(f"inspector_cannot_{name}", lambda call=call, name=name: status(
            call(as_user(s.need("inspector"))), 403, f"inspector: {name}"))

    other_company = {
        "form": lambda h: c.get(f"/forms/{s.need('form')}", headers=h),
        "form_list": lambda h: c.get("/forms", params={"companyId": s.need("a")}, headers=h),
        "submission": lambda h: c.get(f"/submissions/{s.need('sub')}", headers=h),
        "item": lambda h: c.get(f"/submissions/{s.need('sub')}/items/i1", headers=h),
        "item_change": lambda h: patch_item(c, s.need("sub"), "i1", {"notes": "x"}, headers=h),
        "new_submission": lambda h: c.post(f"/forms/{s.need('form')}/submissions", headers=h, json={
            "submittedBy": "x", "submittedAt": "2027-03-17T08:00:00Z", "answers": {"site_name": "X", "contacts": [{"name": "Y"}]}}),
        "findings": lambda h: c.get("/findings", params={"companyId": s.need("a")}, headers=h),
        "users": lambda h: c.get(f"/companies/{s.need('a')}/users", headers=h),
        "company_search": lambda h: c.get("/submissions", params={"companyId": s.need("a")}, headers=h),
        "report": lambda h: c.get("/reports", params={"companyId": s.need("a"), "groupBy": "month"}, headers=h),
    }
    for name, call in other_company.items():
        s.check(f"other_company_{name}_403", lambda call=call, name=name: status(
            call(as_user(s.need("other"))), 403, f"another company's {name}"))

    def inspector_works():
        h = as_user(s.need("inspector"))
        sid = walk_sub(c, s.need("form"), headers=h)
        ok(patch_item(c, sid, "i1", {"notes": "Checked"}, headers=h), "inspector changes an item")
        created(c.post(f"/submissions/{sid}/items", headers=h, json={
            "path": [{"group": "floors", "id": "f1"}, {"group": "rooms", "id": "r1"}], "group": "items",
            "item": {"kind": "alarm", "status": "pass"}}), "inspector appends an item")
        fid = get_id(add_finding(c, sid, "i1", {"title": "Seen", "severity": "low"}, headers=h))
        ok(patch_finding(c, sid, "i1", fid, {"title": "Seen twice"}, headers=h), "inspector edits a finding's title")
    s.check("inspector_can_inspect", inspector_works)

    def author_default():
        h = as_user(s.need("inspector"))
        got = ok(c.post(f"/submissions/{s.need('sub')}/items/i1/findings/{s.need('mine')}/comments",
                        json={"text": "On it"}, headers=h), "comment without author")
        assert got.get("author") == s.need("inspector"), f"author {got.get('author')!r}, expected the user's id"
    s.check("comment_author_defaults_to_user", author_default)

    def status_rules():
        h = as_user(s.need("inspector"))
        status(patch_finding(c, s.need("sub"), "i2", s.need("theirs"), {"status": "resolved"}, headers=h), 403,
               "inspector resolves someone else's finding")
        assert finding_of(c, s.need("sub"), "i2", s.need("theirs")).get("status") == "open", "status changed anyway"
        ok(patch_finding(c, s.need("sub"), "i1", s.need("mine"), {"status": "resolved"}, headers=h),
           "inspector resolves their own finding")
        ok(patch_finding(c, s.need("sub"), "i2", s.need("theirs"), {"status": "in_progress"},
                         headers=as_user(s.need("manager"))), "manager changes any status")
    s.check("inspector_status_only_on_own_findings", status_rules)

    def assignee_me():
        got = [get_id(f) for f in all_findings(c, {"companyId": s.need("a"), "assignee": "me"},
                                               headers=as_user(s.need("inspector")))]
        assert got == [s.need("mine")], f"assignee=me returned {got}"
        rejected(c.get("/findings", params={"companyId": s.need("a"), "assignee": "me"}), "assignee=me without X-User-Id")
    s.check("assignee_me", assignee_me)

    return s.results
