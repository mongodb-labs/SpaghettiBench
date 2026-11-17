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
MERGED_KEYS = {"name", "prompt", "deploy"}


def merged_steps(merged: list[dict[str, Any]], phases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The one-shot steps, run like phases. The first builds the app from the whole spec, with the turn and
    time budget of all phases together; its fix rounds only get a broken deploy running (so the app can be
    seeded), as failing checks may just need the data changes of later steps. Every step runs all phases'
    checks; the first also adds all production data that the phases' `seed_more` adds over time."""
    names = [p["name"] for p in phases]
    steps = []
    for i, m in enumerate(merged):
        first = i == 0
        steps.append({"kind": "merged", "checks": names[-1], "after_checks": names,
                      "budget": len(phases) if first else 1, "fix": "startup" if first else "checks",
                      "seed_more": names if first else [], **m})
    return steps


def probe_steps(probes: list[dict[str, Any]], phases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Change requests given to a finished app (harness.probe), run like a phase: one session with the usual
    budget and fix rounds, then all phases' checks plus the probe's own (the scenario's `probe_checks`)."""
    names = [p["name"] for p in phases]
    return [{"kind": "probe", "probe": p["name"], "checks": names[-1], "after_checks": names, "seed_more": [], **p}
            for p in probes]


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
    merged = data.get("merged", [])
    for m in merged:
        where = f"merged step {m.get('name')!r}"
        if MERGED_KEYS - set(m):
            problems.append(f"{where}: missing {sorted(MERGED_KEYS - set(m))}")
            continue
        problems += [f"{where}: no prompt {f}" for f in m["prompt"] if not (directory / "prompts" / f).exists()]
        problems += [f"{where}: seed_more names unknown phase {n!r}" for n in m.get("seed_more", []) if n not in names]
    probes = data.get("probes", [])
    for pr in probes:
        where = f"probe {pr.get('name')!r}"
        if MERGED_KEYS - set(pr):
            problems.append(f"{where}: missing {sorted(MERGED_KEYS - set(pr))}")
            continue
        problems += [f"{where}: no prompt {f}" for f in pr["prompt"] if not (directory / "prompts" / f).exists()]
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    return {"app_dir": app["app_dir"], "db_name": app["db_name"], "port": app["port"], "phases": phases,
            "merged": merged_steps(merged, phases) if merged else [], "probes": probe_steps(probes, phases)}
