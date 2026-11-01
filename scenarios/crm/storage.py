"""Storage-level verification: what actually happened to the seeded records in the database dumps."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MERGED = {"dup_case_dup", "dup_email_dup", "dup_phone_dup"}


def dump_text(run_dir: Path, phase: str) -> str | None:
    for suffix in (".sql", ".jsonl"):
        path = run_dir / "dumps" / f"after_{phase}{suffix}"
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    return None


def row_counts(db: dict[str, Any]) -> dict[str, int]:
    if "tables" in db:
        return {name: t.get("rows", 0) for name, t in db["tables"].items()}
    return {c["name"]: c.get("count", 0) for c in db.get("collections") or []}


def lines(run_dir: Path, first_row: dict[str, Any], row: dict[str, Any]) -> list[str]:
    seed_path, after = run_dir / "seed.json", dump_text(run_dir, row["phase"])
    if not (seed_path.exists() and after):
        return ["storage check skipped (no seed data or dumps)"]
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    # Merged-away duplicates may legitimately be deleted, so they don't count as lost.
    ids = [x["id"] for kind in ("companies", "contacts", "deals") for x in seed[kind] if x.get("key") not in MERGED]
    found = sum(1 for i in ids if i in after)
    before, now = row_counts(first_row.get("db") or {}), row_counts(row.get("db") or {})
    shrunk = {name: (n, now.get(name, 0)) for name, n in before.items() if now.get(name, 0) < n}
    ok = lambda cond: "OK  " if cond else "WARN"
    return [
        f"{ok(found == len(ids))} seeded records still in the database: {found}/{len(ids)}",
        f"{ok(not shrunk)} tables/collections that lost rows: "
        + (", ".join(f"{k} {a}->{b}" for k, (a, b) in shrunk.items()) or "none")
        + ("  (3 merged duplicates may be deleted from contact_merge on)" if shrunk else ""),
    ]
