"""In-memory reference implementation of the spec (all phases), used to test the harness's own checks.

`/__migrate1`, `/__migrate2`, `/__backfill_findings`, `/__rename_notes` and `/__due_dates` stand in for
the data changes an agent would make in the repeating-groups, multi-level, findings, rename and
due-date phases (form versions, item revisions, history and document versions need no migration here).
Findings, revisions, types, details, attachments and kept document versions live next to the answers,
never inside them.

Deliberately wrong modes:
- `naive_filters`: each filter condition may match a different group item (the same-item checks).
- `latest_version_filters`: every submission is filtered with the latest form version, not its own.
- `lost_updates`: item changes read a copy of the submission, then write it back without holding the
  lock in between (read-modify-write of a whole document), so concurrent changes overwrite each other.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qsl, unquote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

OLD = (("extinguisher_location", "location"), ("extinguisher_expiry", "expiry"), ("pressure_ok", "pressure_ok"))
SEVERITY_DAYS = {"critical": 1, "high": 7, "medium": 14, "low": 30}
STATUSES = {"open", "in_progress", "resolved"}
FINDING_KEYS = {"title", "severity", "status", "assignee", "dueDate"}
FINDING_REPORT_KEYS = {"severity", "status", "assignee", "formId", "typeCode", "month"}
TYPE_CODE = re.compile(r"^[A-Z0-9-]+$")


class ApiError(Exception):
    def __init__(self, code: int, msg: str = ""):
        super().__init__(msg)
        self.code, self.msg = code, msg


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(v: str) -> dt.datetime:
    return dt.datetime.fromisoformat(v.replace("Z", "+00:00"))


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def due_from(created_at: str, severity: str) -> str:
    return (parse_time(created_at).date() + dt.timedelta(days=SEVERITY_DAYS[severity])).isoformat()


def overdue(x: dict) -> bool:
    return x["status"] != "resolved" and bool(x.get("dueDate")) and x["dueDate"] < today()


def finding_view(x: dict) -> dict:
    return {**copy.deepcopy(x), "carriedOverFrom": x.get("carriedOverFrom"), "overdue": overdue(x)}


def valid_interval(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def check_finding_changes(b: Any, allowed: set[str]) -> None:
    if not isinstance(b, dict) or set(b) - allowed:
        raise ApiError(400, "unknown fields")
    if (("title" in b and (not isinstance(b["title"], str) or not b["title"]))
            or ("severity" in b and b["severity"] not in SEVERITY_DAYS)
            or ("status" in b and b["status"] not in STATUSES)
            or ("assignee" in b and b["assignee"] is not None and not isinstance(b["assignee"], str))
            or ("dueDate" in b and b["dueDate"] is not None and not valid_date(b["dueDate"]))):
        raise ApiError(400, "invalid finding")


def group_items(value: Any) -> list[dict]:
    """Every group item (a dict inside a list) at any depth below value."""
    out = []
    for v in (value.values() if isinstance(value, dict) else value if isinstance(value, list) else []):
        if isinstance(v, list):
            for i in v:
                if isinstance(i, dict):
                    out += [i, *group_items(i)]
        elif isinstance(v, dict):
            out += group_items(v)
    return out


def find_item(fields: list, node: dict, iid: str, path: tuple = ()) -> tuple[dict, list, int, list] | None:
    """(group field, the list holding the item, its index, path of parent items) for an item id."""
    for f in fields:
        if f["type"] != "group":
            continue
        for i, item in enumerate(node.get(f["key"]) or []):
            if not isinstance(item, dict):
                continue
            if item.get("id") == iid:
                return f, node[f["key"]], i, [dict(p) for p in path]
            found = find_item(f["fields"], item, iid, path + ({"group": f["key"], "id": item.get("id")},))
            if found:
                return found
    return None


def walk(fields: list, answers: dict, path: Any) -> tuple[list, dict, list[str]]:
    """Follow a path of {group, id} steps; (fields, node, group keys) at its end; 404 if it doesn't exist."""
    if not isinstance(path, list):
        raise ApiError(400, "invalid path")
    node, chain = answers, []
    for step in path:
        f = {x["key"]: x for x in fields}.get(step.get("group")) if isinstance(step, dict) else None
        nxt = next((i for i in node.get(step["group"]) or [] if i.get("id") == step.get("id")), None) if f else None
        if nxt is None:
            raise ApiError(404, "path not found")
        fields, node = f["fields"], nxt
        chain.append(f["key"])
    return fields, node, chain


def alias_item(form: dict, chain: list[str], item: Any) -> Any:
    """Old field keys a form still accepts for the items of a group (the remarks rename)."""
    names = (form.get("aliases") or {}).get(".".join(chain))
    if not names or not isinstance(item, dict):
        return item
    return {names.get(k, k): v for k, v in item.items()}


def alias_answers(form: dict, answers: Any, chain: tuple = ()) -> Any:
    if not isinstance(answers, dict):
        return answers
    out = alias_item(form, list(chain), answers) if chain else dict(answers)
    for k, v in out.items():
        if isinstance(v, list):
            out[k] = [alias_answers(form, i, chain + (k,)) if isinstance(i, dict) else i for i in v]
    return out


def alias_path(form: dict, path: list[str]) -> list[str]:
    names = (form.get("aliases") or {}).get(".".join(path[:-1]))
    return path[:-1] + [names.get(path[-1], path[-1])] if names else path


def valid_date(v: Any) -> bool:
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return False
    try:
        dt.date.fromisoformat(v)
        return True
    except ValueError:
        return False


def check_value(f: dict, v: Any) -> bool:
    t = f["type"]
    if t == "text":
        return isinstance(v, str)
    if t == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
        return ("min" not in f or v >= f["min"]) and ("max" not in f or v <= f["max"])
    if t == "boolean":
        return isinstance(v, bool)
    if t == "date":
        return valid_date(v)
    if t == "select":
        return v in f["options"]
    if t == "group":
        if not isinstance(v, list) or len(v) < f.get("minItems", 0) or len(v) > f.get("maxItems", 10**9):
            return False
        return all(isinstance(i, dict) and validate(f["fields"], i, item=True) for i in v)
    return False


def validate(fields: list, answers: Any, item: bool = False) -> bool:
    keys = {f["key"]: f for f in fields}
    if not isinstance(answers, dict):
        return False
    if set(answers) - set(keys) - ({"id"} if item else set()):
        return False
    if item and "id" in answers and not isinstance(answers["id"], str):
        return False
    for f in fields:
        if f["key"] not in answers:
            if f.get("required"):
                return False
        elif not check_value(f, answers[f["key"]]):
            return False
    return True


def depth(fields: list) -> int:
    return max([1 + depth(f["fields"]) for f in fields if f["type"] == "group"] + [0])


def assign_ids(value: Any) -> None:
    if isinstance(value, dict):
        for v in value.values():
            assign_ids(v)
    elif isinstance(value, list):
        for i in value:
            if isinstance(i, dict):
                i.setdefault("id", uuid.uuid4().hex)
                assign_ids(i)


def convert(answers: dict) -> dict:
    out = dict(answers)
    item = {new: out.pop(old) for old, new in OLD if old in out}
    out["extinguishers"] = [item] if item else []
    return out


NO_MATCH = object()
OPS = {"eq", "gt", "gte", "lt", "lte", "in", "contains"}
RANGE_OPS = {"gt", "gte", "lt", "lte"}
FILTER_KEY = re.compile(r"^(answers\.[^\[\]]+|submittedAt)(?:\[(\w+)\])?$")


def coerce(f: dict, v: str) -> Any:
    """Interpret a filter value with a field's type; NO_MATCH if it doesn't fit."""
    if f["type"] == "number":
        try:
            return float(v)
        except ValueError:
            return NO_MATCH
    if f["type"] == "boolean":
        return {"true": True, "false": False}.get(v, NO_MATCH)
    return v


def compare(op: str, a: Any, b: Any) -> bool:
    return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]


def same_text(stored: Any, target: Any) -> bool:
    return isinstance(stored, str) and isinstance(target, str) and stored.casefold() == target.casefold()


def test_value(f: dict, op: str, stored: Any, v: str) -> bool:
    t = f["type"]
    if op == "eq":
        target = coerce(f, v)
        return target is not NO_MATCH and (same_text(stored, target) if t == "text" else stored == target)
    if op in RANGE_OPS:
        if t == "number" and isinstance(stored, (int, float)) and not isinstance(stored, bool):
            target = coerce(f, v)
            return target is not NO_MATCH and compare(op, stored, target)
        if t == "date" and isinstance(stored, str) and valid_date(v):
            return compare(op, stored, v)
        return False
    if op == "in":
        return t in ("text", "select", "number") and any(
            (target := coerce(f, part)) is not NO_MATCH and (same_text(stored, target) if t == "text" else stored == target)
            for part in v.split(","))
    if op == "contains":
        return t == "text" and isinstance(stored, str) and v.lower() in stored.lower()
    return False


def parse_filters(query: list) -> tuple[list, list]:
    """Split query params into answer conditions (path, op, value) and submittedAt conditions; ValueError -> 400."""
    answers, times = [], []
    for k, v in query:
        m = FILTER_KEY.match(k)
        if not m:
            continue
        target, op = m.group(1), m.group(2) or "eq"
        if op not in OPS:
            raise ValueError(f"unknown operator {op}")
        if target == "submittedAt":
            if op not in RANGE_OPS:
                raise ValueError("submittedAt needs gt, gte, lt or lte")
            times.append((op, dt.datetime.fromisoformat(v.replace("Z", "+00:00"))))
        else:
            answers.append((target.split(".")[1:], op, v))
    return answers, times


def time_matches(submitted_at: str, times: list) -> bool:
    at = dt.datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
    return all(compare(op, at, v) for op, v in times)


def filter_paths(form: dict, conds: list) -> list:
    out = []
    for path, op, v in conds:
        if form.get("converted") and len(path) == 1 and path[0] in dict(OLD):
            path = ["extinguishers", dict(OLD)[path[0]]]
        out.append((alias_path(form, path), op, v))
    return out


def match_node(fields: list, node: dict, conds: list) -> bool:
    """Conditions sharing a group prefix must hold on the same item of that group."""
    by_key = {f["key"]: f for f in fields}
    groups: dict[str, list] = {}
    for path, op, v in conds:
        f = by_key.get(path[0])
        if f is None:
            return False
        if len(path) == 1:
            if path[0] not in node or not test_value(f, op, node[path[0]], v):
                return False
        else:
            groups.setdefault(path[0], []).append((path[1:], op, v))
    return all(by_key[g]["type"] == "group" and any(match_node(by_key[g]["fields"], i, sub)
                                                      for i in node.get(g) or [])
               for g, sub in groups.items())


def values_at(fields: list, node: dict, path: list) -> list:
    f = {x["key"]: x for x in fields}.get(path[0])
    if f is None:
        return []
    if len(path) == 1:
        return [node[path[0]]] if path[0] in node else []
    return [v for i in node.get(path[0]) or [] for v in values_at(f.get("fields", []), i, path[1:])]


def leaf_field(fields: list, path: list) -> dict | None:
    f: dict | None = None
    for p in path:
        f = {x["key"]: x for x in fields}.get(p)
        if f is None:
            return None
        fields = f.get("fields", [])
    return f


def matches(form: dict, fields: list, answers: dict, conds: list, naive: bool) -> bool:
    conds = filter_paths(form, conds)
    if naive:
        for p, op, v in conds:
            f = leaf_field(fields, p)
            if not f or not any(test_value(f, op, stored, v) for stored in values_at(fields, answers, p)):
                return False
        return True
    return match_node(fields, answers, conds)


def render(fields: list, answers: dict) -> list:
    out = []
    for f in fields:
        value = answers.get(f["key"])
        if f["type"] == "group" and isinstance(value, list):
            value = [{"id": i.get("id"), "fields": render(f["fields"], i)} for i in value]
        out.append({"key": f["key"], "label": f.get("label"), "type": f["type"], "value": value})
    return out


def form_view(form: dict, version: int | None = None) -> dict:
    version = version or len(form["versions"])
    return {**form["versions"][version - 1], "id": form["id"], "version": version}


def sub_fields(st: "State", s: dict) -> list:
    return st.forms[s["formId"]]["versions"][s["formVersion"] - 1]["fields"]


# ---------------------------------------------------------------- whole documents and shapes (phases 21-26)

META_ITEM_KEYS = ("findings", "type", "details", "attachments", "prev")
FIELD_TYPES = {"text", "number", "boolean", "date", "select", "group"}
DETAIL_KEY = re.compile(r"^details\.([^\[\]]+)(?:\[(\w+)\])?$")
ATTACHMENT_KEYS = {"photo": ({"url", "takenAt"}, {"marks"}), "reading": ({"sensor", "unit", "values"}, set()),
                   "document": ({"url", "title", "pages"}, set())}


def new_meta() -> dict:
    """Everything kept next to a submission's answers; rev is never removed, so it only ever grows per item id."""
    return {"findings": {}, "rev": {}, "type": {}, "history": {}, "details": {}, "attachments": {}, "prev": {},
            "docRev": 1, "snapshots": []}


def own(item: dict) -> dict:
    """An item's own fields, without its id and nested groups."""
    return {k: v for k, v in item.items() if k != "id" and not isinstance(v, list)}


def item_paths(value: dict, path: tuple = ()) -> dict[str, tuple[str, list]]:
    """item id -> (group key, path of parent items) for every group item."""
    out: dict[str, tuple[str, list]] = {}
    for k, v in value.items():
        if isinstance(v, list):
            for i in v:
                if isinstance(i, dict) and i.get("id"):
                    out[i["id"]] = (k, [dict(p) for p in path])
                    out.update(item_paths(i, path + ({"group": k, "id": i["id"]},)))
    return out


def renumber(value: Any, ids: dict[str, str]) -> Any:
    """A copy of answers with a new id for every group item; ids maps old to new."""
    if isinstance(value, dict):
        return {k: renumber(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        out = []
        for i in value:
            if isinstance(i, dict):
                new = uuid.uuid4().hex
                ids[i.get("id") or new] = new
                out.append({**{k: renumber(v, ids) for k, v in i.items() if k != "id"}, "id": new})
            else:
                out.append(i)
        return out
    return value


def valid_fields(fields: Any, levels: int = 3) -> bool:
    """Field definitions of an item type: groups at most two levels deep."""
    if not isinstance(fields, list):
        return False
    keys: set[str] = set()
    for f in fields:
        if not isinstance(f, dict) or not isinstance(f.get("key"), str) or not f["key"] or f["key"] in keys \
                or f.get("type") not in FIELD_TYPES:
            return False
        keys.add(f["key"])
        if f["type"] == "select" and (not isinstance(f.get("options"), list) or not f["options"]):
            return False
        if f["type"] == "group" and (levels <= 1 or not valid_fields(f.get("fields"), levels - 1)):
            return False
    return True


def is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_text(v: Any) -> bool:
    return isinstance(v, str) and bool(v)


def is_instant(v: Any) -> bool:
    try:
        return isinstance(v, str) and bool(parse_time(v))
    except ValueError:
        return False


def check_attachment(b: Any) -> None:
    kind = b.get("kind") if isinstance(b, dict) else None
    if kind not in ATTACHMENT_KEYS:
        raise ApiError(400, "unknown kind")
    required, optional = ATTACHMENT_KEYS[kind]
    if required - set(b) or set(b) - required - optional - {"kind"}:
        raise ApiError(400, "missing or foreign fields")
    if kind == "photo":
        marks = b.get("marks", [])
        good = is_text(b["url"]) and is_instant(b["takenAt"]) and isinstance(marks, list) and all(
            isinstance(m, dict) and set(m) == {"x", "y", "label"} and is_text(m["label"])
            and all(is_number(m[k]) and 0 <= m[k] <= 1 for k in ("x", "y")) for m in marks)
    elif kind == "reading":
        values = b["values"]
        good = is_text(b["sensor"]) and is_text(b["unit"]) and isinstance(values, list) and bool(values) and all(
            isinstance(v, dict) and set(v) == {"at", "value"} and is_instant(v["at"]) and is_number(v["value"])
            for v in values)
    else:
        good = is_text(b["url"]) and is_text(b["title"]) and isinstance(b["pages"], int) \
            and not isinstance(b["pages"], bool) and b["pages"] >= 1
    if not good:
        raise ApiError(400, "invalid attachment")


def raw_findings(findings: list) -> list:
    return [{k: v for k, v in x.items() if k != "overdue"} for x in copy.deepcopy(findings or [])]


class State:
    def __init__(self, naive_filters: bool, latest_version_filters: bool, lost_updates: bool = False):
        self.forms: dict[str, dict] = {}
        self.subs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.naive = naive_filters
        self.latest_version_filters = latest_version_filters
        self.client_ids: dict[tuple[str, str], tuple[str, str]] = {}
        self.racy = lost_updates
        self.users: dict[str, dict] = {}
        self.types: dict[tuple[str, str], dict] = {}
        self.due_dates = False

    # ------------------------------------------------------------ items (phases 9-20)

    def sub(self, sid: str) -> dict:
        s = self.subs.get(sid)
        if not s:
            raise ApiError(404, "submission not found")
        return s

    def company(self, s: dict) -> str:
        return self.forms[s["formId"]]["companyId"]

    def mutate(self, s: dict, fn: Callable[[dict, dict], Any]) -> Any:
        """Apply a change to a submission's answers and item data; the racy mode writes back a stale copy."""
        if not self.racy:
            with self.lock:
                return fn(s["answers"], s["meta"])
        with self.lock:
            answers, meta = copy.deepcopy(s["answers"]), copy.deepcopy(s["meta"])
        time.sleep(0.05)
        with self.lock:
            out = fn(answers, meta)
            s["answers"], s["meta"] = answers, meta
            return out

    def locate(self, s: dict, answers: dict, iid: str) -> tuple[dict, list, int, list]:
        found = find_item(sub_fields(self, s), answers, iid)
        if not found:
            raise ApiError(404, "item not found")
        return found

    def item_view(self, s: dict, answers: dict, meta: dict, iid: str) -> dict:
        f, sibs, idx, path = self.locate(s, answers, iid)
        code = meta["type"].get(iid)
        t = self.types.get((self.company(s), code)) if code else None
        due = None
        if t:
            day = dt.date.fromisoformat(s["submittedAt"][:10])
            due = (day + dt.timedelta(days=t["intervalDays"])).isoformat()
        return {"id": iid, "submissionId": s["id"], "group": f["key"], "path": path,
                "answers": copy.deepcopy({k: v for k, v in sibs[idx].items() if k != "id"}),
                "findings": [finding_view(x) for x in meta["findings"].get(iid, [])],
                "rev": meta["rev"].get(iid, 1),
                "type": {k: t[k] for k in ("code", "name", "intervalDays")} if t else None, "nextInspectionDue": due,
                "details": copy.deepcopy(meta["details"].get(iid, {})),
                "attachments": copy.deepcopy(meta["attachments"].get(iid, [])), "previousItemId": meta["prev"].get(iid)}

    def item_extra(self, meta: dict, iid: str) -> dict:
        return {"rev": meta["rev"].get(iid, 1), "typeCode": meta["type"].get(iid),
                "details": copy.deepcopy(meta["details"].get(iid, {})),
                "findings": [finding_view(x) for x in meta["findings"].get(iid, [])],
                "attachments": copy.deepcopy(meta["attachments"].get(iid, [])), "previousItemId": meta["prev"].get(iid)}

    def doc_view(self, s: dict, answers: dict, meta: dict) -> dict:
        return {"id": s["id"], "formId": s["formId"], "formVersion": s["formVersion"], "submittedBy": s["submittedBy"],
                "submittedAt": s["submittedAt"], "previousSubmissionId": s.get("previous"), "docRev": meta["docRev"],
                "answers": copy.deepcopy(answers),
                "items": {i["id"]: self.item_extra(meta, i["id"]) for i in group_items(answers) if i.get("id")}}

    def keep(self, s: dict, answers: dict, meta: dict, user: str | None) -> None:
        meta["snapshots"].append({"docRev": meta["docRev"], "at": now_iso(), "by": user,
                                  "doc": self.doc_view(s, answers, meta)})

    def bump(self, s: dict, answers: dict, meta: dict, user: str | None) -> None:
        """One change to a submission: docRev + 1, and the document after it is kept."""
        meta["docRev"] += 1
        self.keep(s, answers, meta, user)

    def refresh_all(self) -> None:
        """After a data migration, the kept current version of every submission is its migrated document (caller holds the lock)."""
        for s in self.subs.values():
            if s["meta"]["snapshots"]:
                s["meta"]["snapshots"][-1]["doc"] = self.doc_view(s, s["answers"], s["meta"])

    def record(self, meta: dict, iid: str, user: str | None, action: str, **extra: Any) -> None:
        meta["history"].setdefault(iid, []).append({"at": now_iso(), "by": user, "action": action, **extra})

    def append(self, sid: str, b: dict, user: str | None) -> dict:
        s = self.sub(sid)
        form = self.forms[s["formId"]]

        def fn(answers: dict, meta: dict) -> dict:
            fields, node, chain = walk(sub_fields(self, s), answers, b.get("path") or [])
            g = {x["key"]: x for x in fields}.get(b.get("group"))
            if not g or g["type"] != "group":
                raise ApiError(404, "group not found")
            item = alias_item(form, chain + [g["key"]], b.get("item"))
            if not validate(g["fields"], item, item=True) or len(node.get(g["key"]) or []) + 1 > g.get("maxItems", 10**9):
                raise ApiError(400, "invalid item")
            item = copy.deepcopy(item)
            item.setdefault("id", uuid.uuid4().hex)
            assign_ids(item)
            node.setdefault(g["key"], []).append(item)
            for i in [item, *group_items(item)]:
                self.record(meta, i["id"], user, "created")
            self.bump(s, answers, meta, user)
            return item
        return self.mutate(s, fn)

    def patch_item(self, sid: str, iid: str, b: Any, user: str | None, if_rev: int | None) -> tuple[int, dict]:
        s = self.sub(sid)
        form = self.forms[s["formId"]]
        if not isinstance(b, dict):
            raise ApiError(400, "body must be an object")

        def fn(answers: dict, meta: dict) -> tuple[int, dict]:
            f, sibs, idx, path = self.locate(s, answers, iid)
            if if_rev is not None and meta["rev"].get(iid, 1) != if_rev:
                return 409, self.item_view(s, answers, meta, iid)
            changes = alias_item(form, [p["group"] for p in path] + [f["key"]], b)
            sub_defs = {x["key"]: x for x in f["fields"]}
            if "id" in changes or any(sub_defs.get(k, {}).get("type") == "group" for k in changes):
                raise ApiError(400, "nested groups can't be changed here")
            new = {**sibs[idx], **changes}
            new = {k: v for k, v in new.items() if v is not None}
            if not validate(f["fields"], new, item=True):
                raise ApiError(400, "invalid item")
            before = {k: sibs[idx].get(k) for k in changes}
            sibs[idx] = new
            meta["rev"][iid] = meta["rev"].get(iid, 1) + 1
            self.record(meta, iid, user, "updated", before=before, after={k: new.get(k) for k in changes})
            self.bump(s, answers, meta, user)
            return 200, self.item_view(s, answers, meta, iid)
        return self.mutate(s, fn)

    def delete_item(self, sid: str, iid: str, user: str | None) -> None:
        s = self.sub(sid)

        def fn(answers: dict, meta: dict) -> None:
            f, sibs, idx, _ = self.locate(s, answers, iid)
            if len(sibs) - 1 < f.get("minItems", 0):
                raise ApiError(409, "group would have too few items")
            item = sibs.pop(idx)
            for i in [item, *group_items(item)]:
                for key in META_ITEM_KEYS:
                    meta[key].pop(i["id"], None)
                self.record(meta, i["id"], user, "deleted")
            self.bump(s, answers, meta, user)
        self.mutate(s, fn)

    def move_item(self, sid: str, iid: str, b: dict, user: str | None) -> dict:
        s = self.sub(sid)

        def fn(answers: dict, meta: dict) -> dict:
            f, sibs, idx, path = self.locate(s, answers, iid)
            target_path = b.get("path") or []
            if any(isinstance(p, dict) and p.get("id") == iid for p in target_path):
                raise ApiError(400, "can't move an item into itself")
            fields, node, chain = walk(sub_fields(self, s), answers, target_path)
            if chain + [b.get("group")] != [p["group"] for p in path] + [f["key"]]:
                raise ApiError(400, "target is a different group")
            target = node.setdefault(f["key"], [])
            pos = b.get("position", len(target))
            if not isinstance(pos, int) or isinstance(pos, bool) or pos < 0:
                raise ApiError(400, "invalid position")
            if target is not sibs and (len(target) + 1 > f.get("maxItems", 10**9) or len(sibs) - 1 < f.get("minItems", 0)):
                raise ApiError(409, "group limits")
            item = sibs.pop(idx)
            target.insert(min(pos, len(target)), item)
            meta["rev"][iid] = meta["rev"].get(iid, 1) + 1
            new_path = self.locate(s, answers, iid)[3]
            self.record(meta, iid, user, "moved", fromPath=path, toPath=new_path)
            self.bump(s, answers, meta, user)
            return self.item_view(s, answers, meta, iid)
        return self.mutate(s, fn)

    def set_type(self, sid: str, iid: str, b: dict, user: str | None) -> dict:
        s = self.sub(sid)
        code = b.get("code")
        t = self.types.get((self.company(s), code)) if code is not None else None
        if code is not None and not t:
            raise ApiError(400, "unknown type")
        details = copy.deepcopy(b.get("details", {})) if t else {}
        if t and not validate(t["fields"], details):
            raise ApiError(400, "invalid details")
        assign_ids(details)

        def fn(answers: dict, meta: dict) -> dict:
            self.locate(s, answers, iid)
            if code is None:
                meta["type"].pop(iid, None)
                meta["details"].pop(iid, None)
            else:
                meta["type"][iid] = code
                meta["details"][iid] = details
            self.bump(s, answers, meta, user)
            return self.item_view(s, answers, meta, iid)
        return self.mutate(s, fn)

    def patch_details(self, sid: str, iid: str, b: Any, user: str | None) -> dict:
        s = self.sub(sid)
        if not isinstance(b, dict):
            raise ApiError(400, "body must be an object")

        def fn(answers: dict, meta: dict) -> dict:
            self.locate(s, answers, iid)
            t = self.types.get((self.company(s), meta["type"].get(iid)))
            if not t:
                raise ApiError(400, "item has no type")
            new = {k: v for k, v in {**meta["details"].get(iid, {}), **copy.deepcopy(b)}.items() if v is not None}
            if not validate(t["fields"], new):
                raise ApiError(400, "invalid details")
            assign_ids(new)
            meta["details"][iid] = new
            self.bump(s, answers, meta, user)
            return self.item_view(s, answers, meta, iid)
        return self.mutate(s, fn)

    def new_finding(self, b: dict) -> dict:
        created_at = now_iso()
        due = b.get("dueDate")
        if due is None and self.due_dates:
            due = due_from(created_at, b["severity"])
        status = b.get("status", "open")
        return {"id": uuid.uuid4().hex, "title": b["title"], "severity": b["severity"], "status": status,
                "assignee": b.get("assignee"), "dueDate": due, "createdAt": created_at,
                "resolvedAt": created_at if status == "resolved" else None, "comments": []}

    def add_finding(self, sid: str, iid: str, b: dict, user: str | None) -> dict:
        s = self.sub(sid)
        if (not isinstance(b.get("title"), str) or not b["title"] or b.get("severity") not in SEVERITY_DAYS
                or (b.get("assignee") is not None and not isinstance(b["assignee"], str))
                or (b.get("dueDate") is not None and not valid_date(b["dueDate"]))):
            raise ApiError(400, "invalid finding")

        def fn(answers: dict, meta: dict) -> dict:
            self.locate(s, answers, iid)
            finding = self.new_finding({k: b.get(k) for k in ("title", "severity", "assignee", "dueDate")})
            meta["findings"].setdefault(iid, []).append(finding)
            self.record(meta, iid, user, "finding_added", findingId=finding["id"])
            self.bump(s, answers, meta, user)
            return finding_view(finding)
        return self.mutate(s, fn)

    def find_finding(self, answers: dict, meta: dict, s: dict, iid: str, fid: str) -> dict:
        self.locate(s, answers, iid)
        finding = next((x for x in meta["findings"].get(iid, []) if x["id"] == fid), None)
        if not finding:
            raise ApiError(404, "finding not found")
        return finding

    def change_finding(self, meta: dict, iid: str, finding: dict, b: dict, user: str | None) -> None:
        before = {k: finding.get(k) for k in b}
        finding.update(b)
        if "status" in b:
            finding["resolvedAt"] = (finding["resolvedAt"] or now_iso()) if b["status"] == "resolved" else None
        self.record(meta, iid, user, "finding_updated", findingId=finding["id"], before=before,
                    after={k: finding.get(k) for k in b})

    def patch_finding(self, sid: str, iid: str, fid: str, b: dict, user: dict | None) -> dict:
        s = self.sub(sid)
        check_finding_changes(b, FINDING_KEYS)

        def fn(answers: dict, meta: dict) -> dict:
            finding = self.find_finding(answers, meta, s, iid, fid)
            if user and user["role"] == "inspector" and "status" in b and finding.get("assignee") != user["id"]:
                raise ApiError(403, "inspectors can only change the status of their own findings")
            self.change_finding(meta, iid, finding, b, user and user["id"])
            self.bump(s, answers, meta, user and user["id"])
            return finding_view(finding)
        return self.mutate(s, fn)

    def add_comment(self, sid: str, iid: str, fid: str, b: dict, user: str | None) -> dict:
        s = self.sub(sid)
        author = b.get("author") if b.get("author") is not None else user
        if not isinstance(author, str) or not author or not isinstance(b.get("text"), str) or not b["text"]:
            raise ApiError(400, "invalid comment")

        def fn(answers: dict, meta: dict) -> dict:
            finding = self.find_finding(answers, meta, s, iid, fid)
            comment = {"id": uuid.uuid4().hex, "author": author, "text": b["text"], "createdAt": now_iso()}
            finding["comments"].append(comment)
            self.record(meta, iid, user, "comment_added", findingId=fid, commentId=comment["id"])
            self.bump(s, answers, meta, user)
            return dict(comment)
        return self.mutate(s, fn)

    # ------------------------------------------------------------ whole documents (phases 21-24)

    def get_document(self, sid: str, q: dict) -> dict:
        with self.lock:
            s = self.sub(sid)
            if "docRev" not in q:
                return self.doc_view(s, s["answers"], s["meta"])
            n = q["docRev"]
            snap = next((x for x in s["meta"]["snapshots"] if n.isdigit() and x["docRev"] == int(n)), None)
            if not snap:
                raise ApiError(404, "version not kept")
            return copy.deepcopy(snap["doc"])

    def finding_plan(self, meta: dict, new_items: dict, entries: Any, user: dict | None) -> list:
        """Validate the findings of an upload: (item id, finding id or None, changes, new comments)."""
        if not isinstance(entries, dict):
            raise ApiError(400, "items must be an object")
        plan = []
        for iid, entry in entries.items():
            if not isinstance(entry, dict):
                raise ApiError(400, "invalid item entry")
            if entry.get("findings") is None:
                continue
            if iid not in new_items or not isinstance(entry["findings"], list):
                raise ApiError(400, "findings of an unknown item")
            existing = {x["id"]: x for x in meta["findings"].get(iid, [])}
            for x in entry["findings"]:
                if not isinstance(x, dict) or not isinstance(x.get("comments") or [], list):
                    raise ApiError(400, "invalid finding")
                changes = {k: x[k] for k in FINDING_KEYS if k in x}
                check_finding_changes(changes, FINDING_KEYS)
                comments = []
                for cm in x.get("comments") or []:
                    if not isinstance(cm, dict):
                        raise ApiError(400, "invalid comment")
                    if cm.get("id") is not None:
                        continue
                    author = cm.get("author") if cm.get("author") is not None else (user and user["id"])
                    if not is_text(author) or not is_text(cm.get("text")):
                        raise ApiError(400, "invalid comment")
                    comments.append({"author": author, "text": cm["text"]})
                fid = x.get("id")
                if fid is None:
                    if "title" not in changes or "severity" not in changes:
                        raise ApiError(400, "invalid finding")
                else:
                    cur = existing.get(fid)
                    if cur is None:
                        raise ApiError(400, "unknown finding")
                    changes = {k: v for k, v in changes.items() if cur.get(k) != v}
                    if user and user["role"] == "inspector" and "status" in changes and cur.get("assignee") != user["id"]:
                        raise ApiError(403, "inspectors can only change the status of their own findings")
                plan.append((iid, fid, changes, comments))
        return plan

    def put_document(self, sid: str, q: dict, b: Any, user: dict | None) -> tuple[int, dict]:
        s = self.sub(sid)
        form = self.forms[s["formId"]]
        if_rev = q.get("ifDocRev")
        if if_rev is None or not if_rev.isdigit():
            raise ApiError(400, "ifDocRev is required")
        if not isinstance(b, dict) or not isinstance(b.get("answers"), dict):
            raise ApiError(400, "answers are required")
        uid = user and user["id"]

        def fn(answers: dict, meta: dict) -> tuple[int, dict]:
            if meta["docRev"] != int(if_rev):
                return 409, self.doc_view(s, answers, meta)
            new = alias_answers(form, copy.deepcopy(b["answers"]))
            if not validate(sub_fields(self, s), new):
                raise ApiError(400, "invalid answers")
            given = [i["id"] for i in group_items(new) if "id" in i]
            if len(given) != len(set(given)):
                raise ApiError(400, "duplicate item ids")
            assign_ids(new)
            old_items = {i["id"]: i for i in group_items(answers)}
            new_items = {i["id"]: i for i in group_items(new)}
            plan = self.finding_plan(meta, new_items, b.get("items") or {}, user)
            old_at, new_at = item_paths(answers), item_paths(new)
            for iid in old_items.keys() - new_items.keys():
                for key in META_ITEM_KEYS:
                    meta[key].pop(iid, None)
                self.record(meta, iid, uid, "deleted")
            for iid, item in new_items.items():
                if iid not in old_items:
                    self.record(meta, iid, uid, "created")
                    continue
                before, after = own(old_items[iid]), own(item)
                changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
                if changed:
                    meta["rev"][iid] = meta["rev"].get(iid, 1) + 1
                    self.record(meta, iid, uid, "updated", before={k: before.get(k) for k in changed},
                                after={k: after.get(k) for k in changed})
                if old_at[iid] != new_at[iid]:
                    meta["rev"][iid] = meta["rev"].get(iid, 1) + 1
                    self.record(meta, iid, uid, "moved", fromPath=old_at[iid][1], toPath=new_at[iid][1])
            answers.clear()
            answers.update(new)
            for iid, fid, changes, comments in plan:
                if fid is None:
                    finding = self.new_finding(changes)
                    meta["findings"].setdefault(iid, []).append(finding)
                    self.record(meta, iid, uid, "finding_added", findingId=finding["id"])
                else:
                    finding = next(x for x in meta["findings"][iid] if x["id"] == fid)
                    if changes:
                        self.change_finding(meta, iid, finding, changes, uid)
                for cm in comments:
                    comment = {"id": uuid.uuid4().hex, **cm, "createdAt": now_iso()}
                    finding["comments"].append(comment)
                    self.record(meta, iid, uid, "comment_added", findingId=finding["id"], commentId=comment["id"])
            self.bump(s, answers, meta, uid)
            return 200, self.doc_view(s, answers, meta)
        return self.mutate(s, fn)

    def revisions(self, sid: str) -> list:
        with self.lock:
            return [{k: x[k] for k in ("docRev", "at", "by")} for x in self.sub(sid)["meta"]["snapshots"]]

    def restore(self, sid: str, b: Any, uid: str | None) -> dict:
        s = self.sub(sid)
        n = b.get("docRev") if isinstance(b, dict) else None
        if not isinstance(n, int) or isinstance(n, bool):
            raise ApiError(400, "docRev is required")

        def state(item: dict, at: tuple, extra: dict) -> str:
            return json.dumps([own(item), at, {k: extra.get(k) for k in ("typeCode", "details", "attachments")},
                               raw_findings(extra.get("findings"))], sort_keys=True)

        def fn(answers: dict, meta: dict) -> dict:
            snap = next((x for x in meta["snapshots"] if x["docRev"] == n), None)
            if not snap:
                raise ApiError(404, "version not kept")
            doc = copy.deepcopy(snap["doc"])
            now_at, then_at = item_paths(answers), item_paths(doc["answers"])
            now = {i["id"]: state(i, now_at[i["id"]], self.item_extra(meta, i["id"])) for i in group_items(answers)}
            then = {i["id"]: state(i, then_at[i["id"]], doc["items"].get(i["id"], {})) for i in group_items(doc["answers"])}
            for iid in now.keys() - then.keys():
                for key in META_ITEM_KEYS:
                    meta[key].pop(iid, None)
                self.record(meta, iid, uid, "restored", docRev=n)
            answers.clear()
            answers.update(doc["answers"])
            for iid in then:
                e = doc["items"].get(iid, {})
                values = {"findings": raw_findings(e.get("findings")), "type": e.get("typeCode"),
                          "details": e.get("details"), "attachments": e.get("attachments"), "prev": e.get("previousItemId")}
                for key, v in values.items():
                    if v:
                        meta[key][iid] = v
                    else:
                        meta[key].pop(iid, None)
                if now.get(iid) != then[iid]:
                    meta["rev"][iid] = meta["rev"].get(iid, 1) + 1
                    self.record(meta, iid, uid, "restored", docRev=n)
            self.bump(s, answers, meta, uid)
            return self.doc_view(s, answers, meta)
        return self.mutate(s, fn)

    def export_form(self, fid: str) -> dict:
        with self.lock:
            form = self.forms.get(fid)
            if not form:
                raise ApiError(404, "form not found")
            docs = [self.doc_view(s, s["answers"], s["meta"]) for s in self.subs.values() if s["formId"] == fid]
            codes = sorted({e["typeCode"] for d in docs for e in d["items"].values() if e["typeCode"]})
            return {"form": {"name": form["versions"][-1]["name"],
                             "versions": [{"name": v["name"], "fields": copy.deepcopy(v["fields"])} for v in form["versions"]]},
                    "itemTypes": [copy.deepcopy(self.types[(form["companyId"], c)]) for c in codes
                                  if (form["companyId"], c) in self.types],
                    "submissions": docs}

    def import_form(self, company: str, b: Any, uid: str | None) -> dict:
        def bad(msg: str) -> ApiError:
            return ApiError(400, msg)
        versions = (b.get("form") or {}).get("versions") if isinstance(b, dict) and isinstance(b.get("form"), dict) else None
        if not isinstance(versions, list) or not versions or not all(
                isinstance(v, dict) and is_text(v.get("name")) and isinstance(v.get("fields"), list) and depth(v["fields"]) <= 4
                for v in versions):
            raise bad("invalid form")
        types_in, subs_in = b.get("itemTypes") or [], b.get("submissions") or []
        if not isinstance(types_in, list) or not isinstance(subs_in, list):
            raise bad("invalid export")
        for t in types_in:
            if (not isinstance(t, dict) or not isinstance(t.get("code"), str) or not TYPE_CODE.match(t["code"])
                    or not is_text(t.get("name")) or not valid_interval(t.get("intervalDays"))
                    or not valid_fields(t.get("fields", []))):
                raise bad("invalid item type")
        with self.lock:
            known = {code for (co, code) in self.types if co == company} | {t["code"] for t in types_in}
            for d in subs_in:
                v = d.get("formVersion") if isinstance(d, dict) else None
                if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= len(versions):
                    raise bad("invalid formVersion")
                if not validate(versions[v - 1]["fields"], d.get("answers")) or not is_text(d.get("submittedBy")) \
                        or not is_instant(d.get("submittedAt")) or not isinstance(d.get("items") or {}, dict):
                    raise bad("invalid submission")
                ids = {i.get("id") for i in group_items(d["answers"])}
                for iid, e in (d.get("items") or {}).items():
                    if iid not in ids or not isinstance(e, dict) or (e.get("typeCode") is not None and e["typeCode"] not in known) \
                            or not isinstance(e.get("details") or {}, dict) or not isinstance(e.get("findings") or [], list) \
                            or not isinstance(e.get("attachments") or [], list):
                        raise bad("invalid item entry")
                    for x in e.get("findings") or []:
                        if not isinstance(x, dict) or not is_text(x.get("title")) or x.get("severity") not in SEVERITY_DAYS \
                                or x.get("status", "open") not in STATUSES or not isinstance(x.get("comments") or [], list) \
                                or not all(isinstance(cm, dict) and is_text(cm.get("author")) and is_text(cm.get("text"))
                                           for cm in x.get("comments") or []):
                            raise bad("invalid finding")
                    for a in e.get("attachments") or []:
                        check_attachment({k: v for k, v in a.items() if k not in ("id", "createdAt")} if isinstance(a, dict) else a)
            for t in types_in:
                self.types.setdefault((company, t["code"]), {"code": t["code"], "name": t["name"], "intervalDays": t["intervalDays"],
                                                             "fields": copy.deepcopy(t.get("fields", []))})
            fid = uuid.uuid4().hex
            self.forms[fid] = {"id": fid, "companyId": company,
                               "versions": [{"companyId": company, "name": v["name"], "fields": copy.deepcopy(v["fields"])}
                                            for v in versions]}
            mapping = {}
            for d in subs_in:
                ids: dict[str, str] = {}
                answers = renumber(d["answers"], ids)
                meta = new_meta()
                entries = d.get("items") or {}
                for old, new in ids.items():
                    e = entries.get(old) or {}
                    findings = [{"id": uuid.uuid4().hex, "title": x["title"], "severity": x["severity"],
                                 "status": x.get("status", "open"), "assignee": x.get("assignee"), "dueDate": x.get("dueDate"),
                                 "createdAt": x.get("createdAt") or now_iso(), "resolvedAt": x.get("resolvedAt"),
                                 "comments": [{"id": uuid.uuid4().hex, "author": cm["author"], "text": cm["text"],
                                               "createdAt": cm.get("createdAt") or now_iso()} for cm in x.get("comments") or []]}
                                for x in e.get("findings") or []]
                    values = {"findings": findings, "type": e.get("typeCode"), "details": copy.deepcopy(e.get("details")),
                              "attachments": [{**copy.deepcopy(a), "id": uuid.uuid4().hex} for a in e.get("attachments") or []]}
                    for key, v in values.items():
                        if v:
                            meta[key][new] = v
                    self.record(meta, new, uid, "created")
                sid = uuid.uuid4().hex
                self.subs[sid] = {"id": sid, "formId": fid, "formVersion": d["formVersion"], "submittedBy": d["submittedBy"],
                                  "submittedAt": d["submittedAt"], "answers": answers, "meta": meta}
                self.keep(self.subs[sid], answers, meta, uid)
                mapping[str(d.get("id"))] = sid
            return {"formId": fid, "submissions": mapping}

    def reinspect(self, sid: str, b: Any, uid: str | None) -> dict:
        if not isinstance(b, dict) or not is_text(b.get("submittedBy")) or not is_instant(b.get("submittedAt")):
            raise ApiError(400, "submittedBy and submittedAt are required")
        with self.lock:
            s = self.sub(sid)
            old = s["meta"]
            ids: dict[str, str] = {}
            answers = renumber(s["answers"], ids)
            meta = new_meta()
            for o, n in ids.items():
                meta["prev"][n] = o
                if o in old["type"]:
                    meta["type"][n] = old["type"][o]
                    meta["details"][n] = copy.deepcopy(old["details"].get(o, {}))
                self.record(meta, n, uid, "created")
                for x in old["findings"].get(o, []):
                    if x["status"] == "resolved":
                        continue
                    f = {**self.new_finding({k: x.get(k) for k in ("title", "severity", "assignee")}),
                         "dueDate": x.get("dueDate"), "carriedOverFrom": x["id"]}
                    meta["findings"].setdefault(n, []).append(f)
                    self.record(meta, n, uid, "finding_added", findingId=f["id"])
            nid = uuid.uuid4().hex
            self.subs[nid] = {"id": nid, "formId": s["formId"], "formVersion": s["formVersion"],
                              "submittedBy": b["submittedBy"], "submittedAt": b["submittedAt"], "answers": answers,
                              "previous": sid, "meta": meta}
            self.keep(self.subs[nid], answers, meta, uid)
            return self.doc_view(self.subs[nid], answers, meta)

    # ------------------------------------------------------------ item shapes (phases 25-26)

    def search_items(self, q: dict) -> dict:
        company, code = q.get("companyId"), q.get("typeCode")
        if not company or not code:
            raise ApiError(400, "companyId and typeCode are required")
        conds = []
        for k, v in q.items():
            m = DETAIL_KEY.match(k)
            if m:
                if (m.group(2) or "eq") not in OPS:
                    raise ApiError(400, "unknown operator")
                conds.append((m.group(1).split("."), m.group(2) or "eq", v))
        with self.lock:
            t = self.types.get((company, code))
            out = []
            for s in self.subs.values():
                if not t or self.company(s) != company:
                    continue
                for iid, c in s["meta"]["type"].items():
                    if c == code and find_item(sub_fields(self, s), s["answers"], iid) \
                            and match_node(t["fields"], s["meta"]["details"].get(iid, {}), conds):
                        out.append(self.item_view(s, s["answers"], s["meta"], iid))
            return {"items": out}

    def add_attachment(self, sid: str, iid: str, b: Any, uid: str | None) -> dict:
        s = self.sub(sid)
        check_attachment(b)

        def fn(answers: dict, meta: dict) -> dict:
            self.locate(s, answers, iid)
            att = {**copy.deepcopy(b), "id": uuid.uuid4().hex, "createdAt": now_iso()}
            meta["attachments"].setdefault(iid, []).append(att)
            self.bump(s, answers, meta, uid)
            return copy.deepcopy(att)
        return self.mutate(s, fn)

    def delete_attachment(self, sid: str, iid: str, aid: str, uid: str | None) -> None:
        s = self.sub(sid)

        def fn(answers: dict, meta: dict) -> None:
            self.locate(s, answers, iid)
            atts = meta["attachments"].get(iid, [])
            if not any(a["id"] == aid for a in atts):
                raise ApiError(404, "attachment not found")
            meta["attachments"][iid] = [a for a in atts if a["id"] != aid]
            self.bump(s, answers, meta, uid)
        self.mutate(s, fn)

    def search_attachments(self, q: dict) -> dict:
        if not q.get("companyId"):
            raise ApiError(400, "companyId is required")
        try:
            above = float(q["above"]) if "above" in q else None
        except ValueError:
            raise ApiError(400, "invalid above")
        out = []
        with self.lock:
            for s in self.subs.values():
                if self.company(s) != q["companyId"] or (q.get("submissionId") and s["id"] != q["submissionId"]):
                    continue
                for iid, atts in s["meta"]["attachments"].items():
                    if not find_item(sub_fields(self, s), s["answers"], iid):
                        continue
                    for a in atts:
                        if ((q.get("kind") and a["kind"] != q["kind"])
                                or ("mark" in q and not any(same_text(m["label"], q["mark"]) for m in a.get("marks") or []))
                                or ("sensor" in q and a.get("sensor") != q["sensor"])
                                or (above is not None and not any(v["value"] > above for v in a.get("values") or []))):
                            continue
                        out.append({**copy.deepcopy(a), "submissionId": s["id"], "itemId": iid})
        return {"items": out}

    def history(self, sid: str, iid: str) -> list:
        with self.lock:
            s = self.sub(sid)
            entries = s["meta"]["history"].get(iid)
            if entries is None and not find_item(sub_fields(self, s), s["answers"], iid):
                raise ApiError(404, "item not found")
            return copy.deepcopy(entries or [])

    def finding_rows(self, company: str) -> list[tuple[dict, dict, dict, list]]:
        """(submission, finding, item location info, path) for every finding of a company (caller holds the lock)."""
        rows = []
        for s in self.subs.values():
            if self.company(s) != company:
                continue
            for iid, findings in s["meta"]["findings"].items():
                found = find_item(sub_fields(self, s), s["answers"], iid)
                if not found:
                    continue
                f, _, _, path = found
                for x in findings:
                    rows.append((s, x, {"itemId": iid, "group": f["key"]}, path))
        return rows

    def finding_filter(self, rows: list, q: dict, user: dict | None) -> list:
        def values(key: str) -> list[str] | None:
            return q[key].split(",") if q.get(key) not in (None, "") else None
        assignee = q.get("assignee")
        if assignee == "me":
            if not user:
                raise ApiError(400, "assignee=me needs X-User-Id")
            assignee = user["id"]
        if q.get("dueBefore") is not None and not valid_date(q["dueBefore"]):
            raise ApiError(400, "invalid dueBefore")
        if q.get("overdue") not in (None, "true", "false"):
            raise ApiError(400, "invalid overdue")
        out = []
        for s, x, where, path in rows:
            code = s["meta"]["type"].get(where["itemId"])
            if ((values("status") and x["status"] not in values("status"))
                    or (values("severity") and x["severity"] not in values("severity"))
                    or (assignee is not None and x.get("assignee") != assignee)
                    or (q.get("formId") and s["formId"] != q["formId"])
                    or (q.get("submissionId") and s["id"] != q["submissionId"])
                    or (q.get("dueBefore") and not (x.get("dueDate") and x["dueDate"] < q["dueBefore"]))
                    or (q.get("typeCode") and code != q["typeCode"])
                    or (q.get("overdue") is not None and overdue(x) != (q["overdue"] == "true"))):
                continue
            out.append((s, x, where, path))
        return out

    def search_findings(self, q: dict, user: dict | None) -> dict:
        if not q.get("companyId"):
            raise ApiError(400, "companyId is required")
        try:
            limit, offset = min(int(q.get("limit", 50)), 200), int(q.get("cursor") or 0)
        except ValueError:
            raise ApiError(400, "invalid limit or cursor")
        with self.lock:
            rows = self.finding_filter(self.finding_rows(q["companyId"]), q, user)
            rows.sort(key=lambda r: (r[1]["dueDate"] is None, r[1]["dueDate"] or "", r[1]["createdAt"], r[1]["id"]))
            page = rows[offset: offset + limit]
            items = []
            for s, x, where, path in page:
                code = s["meta"]["type"].get(where["itemId"])
                t = self.types.get((q["companyId"], code)) if code else None
                view = {k: v for k, v in finding_view(x).items() if k != "comments"}
                items.append({**view, "submissionId": s["id"], "formId": s["formId"], **where, "path": copy.deepcopy(path),
                              "itemType": {"code": t["code"], "name": t["name"]} if t else None})
        return {"items": items, "nextCursor": str(offset + limit) if offset + limit < len(rows) else None}

    def bulk(self, b: dict, user: dict | None) -> dict:
        if not b.get("companyId") or not isinstance(b.get("filter", {}), dict) or not isinstance(b.get("set"), dict) \
                or not b["set"]:
            raise ApiError(400, "companyId and set are required")
        check_finding_changes(b["set"], {"status", "assignee", "dueDate"})
        q = {k: str(v) for k, v in (b.get("filter") or {}).items()}
        with self.lock:
            targets = {(s["id"], x["id"]) for s, x, _, _ in self.finding_filter(self.finding_rows(b["companyId"]), q, user)}
        for sid in {sid for sid, _ in targets}:
            def fn(answers: dict, meta: dict, sid: str = sid) -> None:
                for iid, findings in meta["findings"].items():
                    for x in findings:
                        if (sid, x["id"]) in targets:
                            self.change_finding(meta, iid, x, dict(b["set"]), user and user["id"])
                self.bump(self.subs[sid], answers, meta, user and user["id"])
            self.mutate(self.subs[sid], fn)
        return {"matched": len(targets)}

    def findings_report(self, q: dict, user: dict | None) -> dict:
        keys = [k for k in (q.get("groupBy") or "").split(",") if k]
        if not q.get("companyId") or not keys or any(k not in FINDING_REPORT_KEYS for k in keys):
            raise ApiError(400, "companyId and a valid groupBy are required")
        with self.lock:
            rows = self.finding_filter(self.finding_rows(q["companyId"]), q, user)
            groups: dict[str, dict] = {}
            for s, x, where, _ in rows:
                values = {"severity": x["severity"], "status": x["status"], "assignee": x.get("assignee"),
                          "formId": s["formId"], "typeCode": s["meta"]["type"].get(where["itemId"]),
                          "month": x["createdAt"][:7]}
                key = {k: values[k] for k in keys}
                g = groups.setdefault(json.dumps(key, sort_keys=True), {**key, "count": 0, "open": 0, "overdue": 0, "_d": []})
                g["count"] += 1
                g["open"] += x["status"] != "resolved"
                g["overdue"] += overdue(x)
                if x["status"] == "resolved" and x.get("resolvedAt"):
                    g["_d"].append((parse_time(x["resolvedAt"]) - parse_time(x["createdAt"])).total_seconds() / 86400)
        out = []
        for g in groups.values():
            d = g.pop("_d")
            out.append({**g, "avgDaysToResolve": round(sum(d) / len(d), 2) if d else None})
        return {"groups": out}

    def backfill_findings(self) -> None:
        with self.lock:
            for s in self.subs.values():
                form = self.forms[s["formId"]]
                if form["companyId"] != "tidewater-properties":
                    continue
                for floor in s["answers"].get("floors") or []:
                    for room in floor.get("rooms") or []:
                        for item in room.get("items") or []:
                            existing = s["meta"]["findings"].setdefault(item["id"], [])
                            title = f"{item.get('kind')} failed"
                            if item.get("status") != "fail" or any(x["title"] == title for x in existing):
                                continue
                            due = dt.date.fromisoformat(s["submittedAt"][:10]) + dt.timedelta(days=14)
                            existing.append({"id": uuid.uuid4().hex, "title": title,
                                             "severity": "high" if item.get("kind") == "alarm" else "medium",
                                             "status": "open", "assignee": None, "dueDate": due.isoformat(),
                                             "createdAt": now_iso(), "resolvedAt": None, "comments": []})

    def rename_notes(self) -> None:
        with self.lock:
            for form in self.forms.values():
                if form["companyId"] != "tidewater-properties" or form.get("aliases"):
                    continue
                for version in form["versions"]:
                    items = leaf_field(version["fields"], ["floors", "rooms", "items"])
                    if items:
                        items["fields"] = [{**f, "key": "remarks", "label": "Remarks"} if f["key"] == "notes" else f
                                           for f in items["fields"]]
                form["aliases"] = {"floors.rooms.items": {"notes": "remarks"}}
                for s in self.subs.values():
                    if s["formId"] == form["id"]:
                        s["answers"] = alias_answers(form, s["answers"])

    def start_due_dates(self) -> None:
        with self.lock:
            self.due_dates = True
            for s in self.subs.values():
                for findings in s["meta"]["findings"].values():
                    for x in findings:
                        if x.get("dueDate") is None:
                            x["dueDate"] = due_from(x["createdAt"], x["severity"])

    def add_user(self, company: str, b: dict) -> dict:
        email = b.get("email")
        if (not isinstance(b.get("name"), str) or not b["name"] or b.get("role") not in ("inspector", "manager")
                or not isinstance(email, str) or email.count("@") != 1 or not all(email.split("@"))):
            raise ApiError(400, "invalid user")
        with self.lock:
            if any(u["companyId"] == company and u["email"].lower() == email.lower() for u in self.users.values()):
                raise ApiError(409, "email already used")
            u = {"id": uuid.uuid4().hex, "companyId": company, "name": b["name"], "email": email, "role": b["role"]}
            self.users[u["id"]] = u
            return dict(u)

    def type_change(self, method: str, company: str, code: str | None, b: dict) -> tuple[int, Any]:
        with self.lock:
            if method == "POST":
                code = b.get("code")
                if (not isinstance(code, str) or not TYPE_CODE.match(code) or not isinstance(b.get("name"), str)
                        or not b["name"] or not valid_interval(b.get("intervalDays"))
                        or not valid_fields(b.get("fields", []))):
                    raise ApiError(400, "invalid type")
                if (company, code) in self.types:
                    raise ApiError(409, "code exists")
                self.types[(company, code)] = {"code": code, "name": b["name"], "intervalDays": b["intervalDays"],
                                               "fields": copy.deepcopy(b.get("fields", []))}
                return 201, copy.deepcopy(self.types[(company, code)])
            t = self.types.get((company, code))  # type: ignore[arg-type]
            if not t:
                raise ApiError(404, "type not found")
            if method == "PATCH":
                if ("name" in b and (not isinstance(b["name"], str) or not b["name"])) or \
                        ("intervalDays" in b and not valid_interval(b["intervalDays"])) or \
                        ("fields" in b and not valid_fields(b["fields"])):
                    raise ApiError(400, "invalid type")
                t.update({k: copy.deepcopy(b[k]) for k in ("name", "intervalDays", "fields") if k in b})
                return 200, copy.deepcopy(t)
            if any(self.company(s) == company and code in s["meta"]["type"].values() for s in self.subs.values()):
                raise ApiError(409, "type in use")
            del self.types[(company, code)]  # type: ignore[arg-type]
            return 204, None

    def company_of(self, method: str, parts: list[str], q: dict, b: Any) -> str | None:
        b = b if isinstance(b, dict) else {}
        with self.lock:
            head = parts[0] if parts else ""
            if head == "forms":
                if len(parts) == 1:
                    return b.get("companyId") if method == "POST" else q.get("companyId")
                form = self.forms.get(parts[1])
                return form["companyId"] if form else None
            if head == "submissions":
                if len(parts) == 1:
                    return q.get("companyId")
                s = self.subs.get(parts[1])
                return self.company(s) if s else None
            if head == "findings":
                return b.get("companyId") if parts == ["findings", "bulk"] else q.get("companyId")
            if head == "reports" or head in ("items", "attachments"):
                return q.get("companyId")
            if head == "companies" and len(parts) > 1:
                return parts[1]
        return None

    def auth(self, header: str | None, method: str, parts: list[str], q: dict, b: Any) -> dict | None:
        if header is None:
            return None
        with self.lock:
            user = self.users.get(header)
        if not user:
            raise ApiError(401, "unknown user")
        company = self.company_of(method, parts, q, b)
        if company is not None and company != user["companyId"]:
            raise ApiError(403, "another company's data")
        manager_only = ((method == "POST" and parts == ["forms"]) or (method == "PUT" and parts[:1] == ["forms"])
                        or (method == "POST" and len(parts) == 3 and parts[0] == "companies" and parts[2] in ("users", "import"))
                        or (method == "POST" and parts == ["findings", "bulk"]))
        if manager_only and user["role"] != "manager":
            raise ApiError(403, "managers only")
        return user

    def route(self, method: str, parts: list[str], q: dict, b: Any, user: dict | None) -> tuple[int, Any] | None:
        uid = user["id"] if user else None
        match method, parts:
            case "POST", ["__backfill_findings" | "__rename_notes" | "__due_dates"]:
                {"__backfill_findings": self.backfill_findings, "__rename_notes": self.rename_notes,
                 "__due_dates": self.start_due_dates}[parts[0]]()
                with self.lock:
                    self.refresh_all()
                return 200, {}
            case "POST", ["companies", c, "users"]:
                return 201, self.add_user(c, b)
            case "GET", ["companies", c, "users"]:
                with self.lock:
                    return 200, [dict(u) for u in self.users.values() if u["companyId"] == c]
            case "POST", ["companies", c, "item-types"]:
                return self.type_change("POST", c, None, b)
            case "GET", ["companies", c, "item-types"]:
                with self.lock:
                    return 200, [dict(t) for (co, _), t in self.types.items() if co == c]
            case ("PATCH" | "DELETE"), ["companies", c, "item-types", code]:
                return self.type_change(method, c, code, b)
            case "POST", ["submissions", sid, "items"]:
                return 201, self.append(sid, b, uid)
            case "POST", ["submissions", sid, "items", "sync"]:
                edits = b.get("edits")
                if not isinstance(edits, list) or len(edits) > 100:
                    raise ApiError(400, "edits must be a list of at most 100")
                results = []
                for e in edits:
                    try:
                        if not isinstance(e, dict) or not isinstance(e.get("ifRev"), int):
                            raise ApiError(400, "invalid edit")
                        code, item = self.patch_item(sid, str(e.get("itemId")), e.get("answers"), uid, e["ifRev"])
                        results.append({"itemId": e.get("itemId"), "status": code, "item": item})
                    except ApiError as err:
                        results.append({"itemId": e.get("itemId") if isinstance(e, dict) else None,
                                        "status": err.code, "error": err.msg})
                return 200, {"results": results}
            case "GET", ["submissions", sid, "items", iid]:
                with self.lock:
                    s = self.sub(sid)
                    return 200, self.item_view(s, s["answers"], s["meta"], iid)
            case "PATCH", ["submissions", sid, "items", iid]:
                if_rev = q.get("ifRev")
                if if_rev is not None and not if_rev.isdigit():
                    raise ApiError(400, "invalid ifRev")
                return self.patch_item(sid, iid, b, uid, int(if_rev) if if_rev is not None else None)
            case "DELETE", ["submissions", sid, "items", iid]:
                self.delete_item(sid, iid, uid)
                return 204, None
            case "POST", ["submissions", sid, "items", iid, "move"]:
                return 200, self.move_item(sid, iid, b, uid)
            case "PUT", ["submissions", sid, "items", iid, "type"]:
                return 200, self.set_type(sid, iid, b, uid)
            case "PATCH", ["submissions", sid, "items", iid, "details"]:
                return 200, self.patch_details(sid, iid, b, uid)
            case "POST", ["submissions", sid, "items", iid, "attachments"]:
                return 201, self.add_attachment(sid, iid, b, uid)
            case "DELETE", ["submissions", sid, "items", iid, "attachments", aid]:
                self.delete_attachment(sid, iid, aid, uid)
                return 204, None
            case "GET", ["submissions", sid, "document"]:
                return 200, self.get_document(sid, q)
            case "PUT", ["submissions", sid, "document"]:
                return self.put_document(sid, q, b, user)
            case "GET", ["submissions", sid, "revisions"]:
                return 200, self.revisions(sid)
            case "POST", ["submissions", sid, "restore"]:
                return 200, self.restore(sid, b, uid)
            case "POST", ["submissions", sid, "reinspect"]:
                return 201, self.reinspect(sid, b, uid)
            case "GET", ["forms", fid, "export"]:
                return 200, self.export_form(fid)
            case "POST", ["companies", c, "import"]:
                return 201, self.import_form(c, b, uid)
            case "GET", ["items"]:
                return 200, self.search_items(q)
            case "GET", ["attachments"]:
                return 200, self.search_attachments(q)
            case "GET", ["submissions", sid, "items", iid, "history"]:
                return 200, self.history(sid, iid)
            case "POST", ["submissions", sid, "items", iid, "findings"]:
                return 201, self.add_finding(sid, iid, b, uid)
            case "PATCH", ["submissions", sid, "items", iid, "findings", fid]:
                return 200, self.patch_finding(sid, iid, fid, b, user)
            case "POST", ["submissions", sid, "items", iid, "findings", fid, "comments"]:
                return 201, self.add_comment(sid, iid, fid, b, uid)
            case "GET", ["findings"]:
                return 200, self.search_findings(q, user)
            case "POST", ["findings", "bulk"]:
                return 200, self.bulk(b, user)
            case "GET", ["reports", "findings"]:
                return 200, self.findings_report(q, user)
        return None


class Handler(BaseHTTPRequestHandler):
    # Keep-alive: the checks make thousands of requests, and a new connection each exhausts Windows' ports.
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    @property
    def st(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def send(self, code: int, body: Any) -> None:
        data = b"" if code == 204 else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> Any:
        if not hasattr(self, "_body"):
            self._body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        return self._body

    def dispatch(self, method: str) -> None:
        self.__dict__.pop("_body", None)  # one handler serves every request of a keep-alive connection
        u = urlparse(self.path)
        parts, q = [unquote(p) for p in u.path.strip("/").split("/")], parse_qsl(u.query)
        try:
            b = self.body() if method in ("POST", "PUT", "PATCH") else {}
            self.user = self.st.auth(self.headers.get("X-User-Id"), method, parts, dict(q), b)
            out = self.st.route(method, parts, dict(q), b, self.user)
        except ApiError as e:
            return self.send(e.code, {"error": e.msg})
        except json.JSONDecodeError:
            return self.send(400, {"error": "bad JSON"})
        if out is not None:
            return self.send(*out)
        {"GET": self.old_get, "POST": self.old_post, "PUT": self.old_put}.get(method, lambda: self.send(404, {}))()

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def do_PUT(self) -> None:
        self.dispatch("PUT")

    def do_PATCH(self) -> None:
        self.dispatch("PATCH")

    def do_DELETE(self) -> None:
        self.dispatch("DELETE")

    def old_post(self) -> None:
        parts = urlparse(self.path).path.strip("/").split("/")
        st = self.st
        with st.lock:
            if parts == ["forms"]:
                b = self.body()
                if depth(b["fields"]) > 4:
                    return self.send(400, {"error": "groups nested too deep"})
                fid = uuid.uuid4().hex
                st.forms[fid] = {"id": fid, "companyId": b["companyId"], "versions": [b]}
                return self.send(201, form_view(st.forms[fid]))
            if len(parts) == 3 and parts[0] == "forms" and parts[2] == "submissions":
                return self.send(*self.create(parts[1], self.body()))
            if parts == ["submissions", "batch"]:
                results = []
                for entry in self.body().get("submissions") or []:
                    code, body = self.create(str(entry.get("formId")), entry)
                    results.append({"status": code, "submission": body} if code in (200, 201)
                                   else {"status": code, "error": body.get("error", "rejected")})
                return self.send(200, {"results": results})
            if parts == ["__migrate1"]:
                self.migrate_groups()
                st.refresh_all()
                return self.send(200, {})
            if parts == ["__migrate2"]:
                for s in st.subs.values():
                    assign_ids(s["answers"])
                st.refresh_all()
                return self.send(200, {})
        self.send(404, {})

    def create(self, fid: str, b: dict) -> tuple[int, dict]:
        """Create one submission (caller holds the lock); shared by the single and batch endpoints."""
        st = self.st
        form = st.forms.get(fid)
        if not form:
            return 404, {"error": "form not found"}
        if self.user and form["companyId"] != self.user["companyId"]:
            return 403, {"error": "another company's form"}
        content = json.dumps({k: v for k, v in b.items() if k != "formId"}, sort_keys=True)
        csid = b.get("clientSubmissionId")
        if csid is not None and (fid, csid) in st.client_ids:
            sid, stored_content = st.client_ids[(fid, csid)]
            return (200, public(st.subs[sid])) if stored_content == content else (409, {"error": "different content"})
        version = b.get("formVersion", len(form["versions"]))
        if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= len(form["versions"]):
            return 400, {"error": "unknown formVersion"}
        answers = alias_answers(form, b.get("answers"))
        if form.get("converted") and isinstance(answers, dict) and any(o in answers for o, _ in OLD):
            answers = convert(answers)
        if not validate(form["versions"][version - 1]["fields"], answers):
            return 400, {"error": "invalid"}
        assign_ids(answers)
        sid = uuid.uuid4().hex
        meta = new_meta()
        user = self.user and self.user["id"]
        for i in group_items(answers):
            st.record(meta, i["id"], user, "created")
        st.subs[sid] = {"id": sid, "formId": fid, "formVersion": version, "submittedBy": b["submittedBy"],
                        "submittedAt": b["submittedAt"], "answers": answers,
                        **({"clientSubmissionId": csid} if csid is not None else {}), "meta": meta}
        st.keep(st.subs[sid], answers, meta, user)
        if csid is not None:
            st.client_ids[(fid, csid)] = (sid, content)
        return 201, public(st.subs[sid])

    def old_put(self) -> None:
        parts = urlparse(self.path).path.strip("/").split("/")
        st = self.st
        with st.lock:
            form = st.forms.get(parts[1]) if len(parts) == 2 and parts[0] == "forms" else None
            if not form:
                return self.send(404, {})
            b = self.body()
            if b.get("companyId") != form["companyId"] or depth(b["fields"]) > 4:
                return self.send(400, {"error": "invalid edit"})
            form["versions"].append(b)
            return self.send(200, form_view(form))

    def migrate_groups(self) -> None:
        for form in self.st.forms.values():
            v1 = form["versions"][0]
            if form["companyId"] != "acme" or v1["name"] != "Fire Safety Check" or form.get("converted"):
                continue
            i = [f["key"] for f in v1["fields"]].index("extinguisher_location")
            rest = [f for f in v1["fields"] if f["key"] not in dict(OLD)]
            group = {"key": "extinguishers", "label": "Extinguishers", "type": "group", "minItems": 1, "maxItems": 50,
                     "fields": [{"key": "location", "label": "Location", "type": "text"},
                                {"key": "expiry", "label": "Expiry", "type": "date"},
                                {"key": "pressure_ok", "label": "Pressure OK", "type": "boolean"}]}
            form["versions"][0] = {**v1, "fields": rest[:i] + [group] + rest[i:]}
            form["converted"] = True
            for s in self.st.subs.values():
                if s["formId"] == form["id"]:
                    s["answers"] = convert(s["answers"])

    def old_get(self) -> None:
        u = urlparse(self.path)
        parts, q = u.path.strip("/").split("/"), parse_qsl(u.query)
        st = self.st
        with st.lock:
            if parts == ["forms"]:
                return self.send(200, [form_view(f) for f in st.forms.values()
                                       if f["companyId"] == dict(q).get("companyId")])
            if len(parts) == 2 and parts[0] == "forms":
                f = st.forms.get(parts[1])
                return self.send(200, form_view(f)) if f else self.send(404, {})
            if len(parts) == 4 and parts[0] == "forms" and parts[2] == "versions":
                f = st.forms.get(parts[1])
                v = int(parts[3]) if parts[3].isdigit() else 0
                if not f or not 1 <= v <= len(f["versions"]):
                    return self.send(404, {})
                return self.send(200, form_view(f, v))
            if len(parts) == 2 and parts[0] == "submissions":
                s = st.subs.get(parts[1])
                return self.send(200, public(s)) if s else self.send(404, {})
            if len(parts) == 3 and parts[0] == "submissions" and parts[2] == "rendered":
                s = st.subs.get(parts[1])
                if not s:
                    return self.send(404, {})
                version = st.forms[s["formId"]]["versions"][s["formVersion"] - 1]
                return self.send(200, {**{k: v for k, v in public(s).items() if k != "answers"},
                                       "formName": version["name"], "fields": render(version["fields"], s["answers"])})
            if len(parts) == 3 and parts[0] == "forms" and parts[2] == "submissions":
                return self.search(q, lambda s: s["formId"] == parts[1])
            if parts == ["submissions"]:
                company = dict(q).get("companyId")
                if not company:
                    return self.send(400, {"error": "companyId is required"})
                return self.search(q, lambda s: st.forms[s["formId"]]["companyId"] == company)
            if len(parts) == 3 and parts[0] == "forms" and parts[2] == "report":
                return self.report(q, lambda s: s["formId"] == parts[1])
            if parts == ["reports"]:
                company = dict(q).get("companyId")
                if not company:
                    return self.send(400, {"error": "companyId is required"})
                return self.report(q, lambda s: st.forms[s["formId"]]["companyId"] == company)
        self.send(404, {})

    def selected(self, q: list, scope: Any) -> list:
        """Submissions in scope that pass the search filters; ValueError for a bad filter."""
        st = self.st
        conds, times = parse_filters(q)
        out = []
        for s in st.subs.values():
            if not scope(s) or not time_matches(s["submittedAt"], times):
                continue
            form = st.forms[s["formId"]]
            fields = form["versions"][-1]["fields"] if st.latest_version_filters else sub_fields(st, s)
            if matches(form, fields, s["answers"], conds, st.naive):
                out.append(s)
        return out

    def report(self, q: list, scope: Any) -> None:
        params = dict(q)
        keys = [k for k in params.get("groupBy", "").split(",") if k]
        if not keys or any(k not in ("month", "formId") and not re.fullmatch(r"answers(\.\w+)+", k) for k in keys):
            return self.send(400, {"error": "bad groupBy"})
        rate = None
        if "rate" in params:
            m = re.fullmatch(r"answers((?:\.\w+)+):(.*)", params["rate"])
            if not m:
                return self.send(400, {"error": "bad rate"})
            rate = (m.group(1)[1:].split("."), m.group(2))
        paths = [k.split(".")[1:] for k in keys if k.startswith("answers.")] + ([rate[0]] if rate else [])
        chain = max((p[:-1] for p in paths), key=len) if paths else []
        if any(p[:-1] != chain[:len(p) - 1] for p in paths):
            return self.send(400, {"error": "group paths must lie along one chain"})
        try:
            tz = ZoneInfo(params["timezone"]) if "timezone" in params else dt.timezone.utc
        except (ZoneInfoNotFoundError, ValueError):
            return self.send(400, {"error": "unknown timezone"})
        try:
            subs = self.selected(q, scope)
        except ValueError as e:
            return self.send(400, {"error": str(e)})

        groups: dict[str, dict] = {}
        for s in subs:
            form = self.st.forms[s["formId"]]
            fields = sub_fields(self.st, s)
            rate_path = alias_path(form, rate[0]) if rate else None
            rate_field = leaf_field(fields, rate_path) if rate_path else None
            target = coerce(rate_field, rate[1]) if rate_field else NO_MATCH
            # Expand the submission into one row per counted unit: [answers, level-1 item, level-2 item, ...].
            rows = [[s["answers"]]]
            for key in chain:
                rows = [row + [item] for row in rows for item in row[-1].get(key) or [] if isinstance(item, dict)]
            for row in rows:
                values = {}
                for k in keys:
                    if k == "month":
                        at = dt.datetime.fromisoformat(s["submittedAt"].replace("Z", "+00:00"))
                        values[k] = at.astimezone(tz).strftime("%Y-%m")
                    elif k == "formId":
                        values[k] = s["formId"]
                    else:
                        path = alias_path(form, k.split(".")[1:])
                        values[k] = row[len(path) - 1].get(path[-1])
                g = groups.setdefault(json.dumps(values, sort_keys=True), {**values, "count": 0, "_hits": 0})
                g["count"] += 1
                stored = row[len(rate_path) - 1].get(rate_path[-1]) if rate_path else None
                if rate and target is not NO_MATCH and (
                        same_text(stored, target) if rate_field and rate_field["type"] == "text" else stored == target):
                    g["_hits"] += 1
        out = []
        for g in groups.values():
            hits = g.pop("_hits")
            if rate:
                g["rate"] = hits / g["count"]
            out.append(g)
        return self.send(200, {"groups": out})

    def search(self, q: list, scope: Any) -> None:
        try:
            items = self.selected(q, scope)
        except ValueError as e:
            return self.send(400, {"error": str(e)})
        limit = min(int(dict(q).get("limit", 50)), 500)
        items.sort(key=lambda s: s["submittedAt"], reverse=True)
        return self.send(200, {"items": [public(s) for s in items[:limit]]})


def public(s: dict) -> dict:
    return {k: v for k, v in s.items() if k not in ("meta", "previous")}


def serve(naive_filters: bool = False, latest_version_filters: bool = False,
          lost_updates: bool = False) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.state = State(naive_filters, latest_version_filters, lost_updates)  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
