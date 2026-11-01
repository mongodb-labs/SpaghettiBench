"""Reads a scenario's `scenario.toml`: the app's neutral names and port, and its phases with what we expect of them."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

KINDS = {"feature", "improvement"}
FAVOURS = {"mongodb", "postgres", "neutral"}
# Why a phase is expected to favour one database; the same tag across scenarios lets results be pooled.
MECHANISMS = {
    "baseline": "the initial build",
    "schema-change": "existing data changes shape (conversion, backfill) while old clients keep working",
    "flexible-schema": "fields or structures defined by users at runtime",
    "nested-query": "filters and searches inside nested or variable data",
    "nested-update": "changes to single elements inside nested data, also concurrent",
    "raw-payload": "storing and querying arbitrary external JSON",
    "text-matching": "comparisons ignoring case or accents",
    "idempotency": "safe retries and de-duplication under concurrent requests",
    "transactions": "invariants across several records under concurrent requests",
    "counter": "gap-free sequences",
    "derived-total": "totals across records that must always be current",
    "shared-reference": "data many records refer to that must stay current (renames, many-to-many)",
    "cross-entity-report": "aggregations across many records and entities",
    "whole-document": "reading, replacing, copying or versioning an aggregate with everything nested in it",
    "polymorphic": "variants of one kind of record with different nested shapes",
    "stored-query": "filters saved as data and run later on current data",
    "search": "full-text search with word forms and ranking",
    "geo": "distances and areas on a map",
    "bulk-import": "large imports with matching against existing records",
    "change-capture": "reacting to changes reliably and in order (events, outboxes)",
    "expiry": "records that stop counting after a time",
    "time-handling": "dates and time zones",
    "cross-cutting": "a change that touches every endpoint",
}
PHASE_KEYS = {"name", "kind", "prompt", "deploy", "summary", "favours", "mechanism", "why"}


def load(directory: Path) -> dict[str, Any]:
    path = directory / "scenario.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    app, phases = data["app"], data["phases"]
    problems = []
    names = [p.get("name") for p in phases]
    if len(set(names)) != len(names):
        problems.append("duplicate phase names")
    for p in phases:
        where = f"phase {p.get('name')!r}"
        if PHASE_KEYS - set(p):
            problems.append(f"{where}: missing {sorted(PHASE_KEYS - set(p))}")
            continue
        if p["kind"] not in KINDS:
            problems.append(f"{where}: kind {p['kind']!r} not in {sorted(KINDS)}")
        if p["favours"] not in FAVOURS:
            problems.append(f"{where}: favours {p['favours']!r} not in {sorted(FAVOURS)}")
        if p["mechanism"] not in MECHANISMS:
            problems.append(f"{where}: mechanism {p['mechanism']!r} not in {sorted(MECHANISMS)}")
        problems += [f"{where}: no prompt {f}" for f in p["prompt"] if not (directory / "prompts" / f).exists()]
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    return {"app_dir": app["app_dir"], "db_name": app["db_name"], "port": app["port"], "phases": phases}
