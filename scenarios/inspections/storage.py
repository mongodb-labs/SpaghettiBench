"""Storage-level verification: what actually happened to the seeded records in the database.

The API checks only see what the app returns. These checks read the database dumps taken after each
phase, so they also catch data lost or silently rewritten in storage, and conversions that are only
done on read ("lazy") instead of migrated in the database.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

NEW_KEY = re.compile(r"\bextinguishers\b")
OLD_KEY = re.compile(r"\bextinguisher_location\b|\bextinguisher_expiry\b")
CONVERTED_FORM = "acme_fire"


def dump_text(run_dir: Path, phase: str) -> str | None:
    for suffix in (".sql", ".jsonl"):
        path = run_dir / "dumps" / f"after_{phase}{suffix}"
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    return None


def lines_by_id(text: str, ids: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {i: [] for i in ids}
    for line in text.splitlines():
        for i in ids:
            if i in line:
                found[i].append(line)
    return found


def row_counts(db: dict[str, Any]) -> dict[str, int]:
    if "tables" in db:
        return {name: t.get("rows", 0) for name, t in db["tables"].items()}
    return {c["name"]: c.get("count", 0) for c in db.get("collections") or []}


def analyze(run_dir: Path, before_row: dict[str, Any], after_row: dict[str, Any]) -> dict[str, Any] | None:
    """Compare storage after the initial phase with storage after a later phase."""
    seed_path = run_dir / "seed.json"
    before, after = dump_text(run_dir, before_row["phase"]), dump_text(run_dir, after_row["phase"])
    if not (seed_path.exists() and before and after):
        return None
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    subs = seed["submissions"]
    ids = [s["id"] for s in subs]
    b, a = lines_by_id(before, ids), lines_by_id(after, ids)
    converted = [s["id"] for s in subs if s["form"] == CONVERTED_FORM]
    others = [s["id"] for s in subs if s["form"] != CONVERTED_FORM]

    counts_before, counts_after = row_counts(before_row.get("db") or {}), row_counts(after_row.get("db") or {})
    shrunk = {name: [n, counts_after.get(name, 0)] for name, n in counts_before.items()
              if counts_after.get(name, 0) < n}
    return {
        "seeded_total": len(ids),
        "seeded_found_before": sum(1 for i in ids if b[i]),
        "seeded_found_after": sum(1 for i in ids if a[i]),
        "converted_total": len(converted),
        "converted_in_storage": sum(1 for i in converted if any(NEW_KEY.search(l) for l in a[i])),
        "converted_old_keys_still_stored": sum(1 for i in converted if any(OLD_KEY.search(l) for l in a[i])),
        "others_total": len(others),
        "others_changed_in_storage": sum(1 for i in others if sorted(a[i]) != sorted(b[i])),
        "tables_shrunk": shrunk,
    }


def summary(s: dict[str, Any]) -> list[str]:
    ok = lambda cond: "OK  " if cond else "WARN"
    lost = s["seeded_total"] - s["seeded_found_after"]
    return [
        f"{ok(lost == 0)} seeded records still in the database: {s['seeded_found_after']}/{s['seeded_total']}"
        f" (before the change: {s['seeded_found_before']})",
        f"{ok(s['converted_in_storage'] == s['converted_total'])} Acme submissions stored in the new shape: "
        f"{s['converted_in_storage']}/{s['converted_total']}"
        + ("  <- converted only on read, or not at all" if s["converted_in_storage"] < s["converted_total"] else ""),
        f"info old Acme keys still present in storage: {s['converted_old_keys_still_stored']}/{s['converted_total']}"
        " (fine if kept as a backup copy; check the schema)",
        f"info other seeded records rewritten in storage: {s['others_changed_in_storage']}/{s['others_total']}"
        " (fine when a migration adds a column or field to every row; lost data shows above and in the API checks)",
        f"{ok(not s['tables_shrunk'])} tables/collections that lost rows: "
        + (", ".join(f"{k} {v[0]}->{v[1]}" for k, v in s["tables_shrunk"].items()) or "none"),
    ]
