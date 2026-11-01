"""Summarise all runs per scenario: effort, code and outcome per phase, medians and totals per database,
and verification.

Usage: uv run -m harness.report [--scenario NAME] [--details] [--runs DIR]
Effort is recomputed from the transcripts, so older runs pick up classifier improvements.
Also writes <runs>/<scenario>/summary.csv.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from types import ModuleType
from typing import Any

from harness.run import RUNS, SCENARIOS, load_scenario
from harness.traces import trace_metrics


def load(scenario_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(scenario_dir.glob("*/metrics.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = {**json.loads(line), "run": path.parent.name}
            trace = path.parent / "traces" / f"{row.get('trace') or row['phase']}.jsonl"
            if trace.exists():
                row.update(trace_metrics(trace))
            rows.append(row)
    return rows


def db_summary(r: dict[str, Any]) -> str:
    db = r.get("db") or {}
    if "tables" in db:
        text = f"{len(db['tables'])} tables, {db.get('jsonb_columns', 0)} json cols"
    else:
        colls = db.get("collections") or []
        shapes = max((c.get("shapes", 0) for c in colls), default=0)
        text = f"{len(colls)} colls, max {shapes} shapes"
    return text if r.get("used_forced_db") else text + " (NO DATA)"


def flatten(r: dict[str, Any]) -> dict[str, Any]:
    total = r.get("checks_total") or 0
    passed = r.get("checks_passed") or 0
    return {
        "run": r["run"], "arm": r["arm"], "phase": r["phase"], "kind": r.get("kind"), "try": r.get("attempt", 0),
        "status": r.get("status"),
        "app": "up" if r.get("app_started") else "DOWN",
        "cost_usd": round(r["cost_usd"], 2) if isinstance(r.get("cost_usd"), (int, float)) else None,
        "turns": r.get("turns"),
        "minutes": round((r.get("wall_s") or 0) / 60, 1),
        "tokens_m": round((r.get("tokens_total") or 0) / 1e6, 2),
        "output_k": round(((r.get("tokens") or {}).get("output") or 0) / 1e3, 1),
        "explore": r.get("explore_calls"), "edit": r.get("edit_calls"), "exec": r.get("exec_calls"),
        "install": r.get("install_calls"), "db_calls": r.get("db_calls"), "tool_err": r.get("tool_errors"),
        "files": r.get("files_touched"), "added": r.get("lines_added"), "removed": r.get("lines_removed"),
        "total_lines": r.get("total_lines"), "largest_file": r.get("largest_file_lines"),
        "unexecuted": (r.get("coverage") or {}).get("unexecuted_share"),
        "checks": f"{passed}/{total}", "pass_rate": round(passed / total, 2) if total else 0.0,
        "db_design": db_summary(r),
    }


def print_table(rows: list[dict[str, Any]], cols: list[str]) -> None:
    cells = [[("" if row.get(c) is None else str(row.get(c))) for c in cols] for row in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in cells:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))
    print()


def num(x: float) -> str:
    return ("%.2f" % x).rstrip("0").rstrip(".")


def median_spread(values: list[Any]) -> str | None:
    """Median, with ± half the range across runs when the runs differ."""
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    spread = (max(nums) - min(nums)) / 2
    return num(statistics.median(nums)) + (f" ±{num(spread)}" if round(spread, 2) else "")


def verification(raw: list[dict[str, Any]], sc: ModuleType, scenario_dir: Path, details: bool) -> dict[str, list[str]]:
    """Verification lines per run."""
    by_run: dict[str, list[dict[str, Any]]] = {}
    for r in raw:
        by_run.setdefault(r["run"], []).append(r)
    out: dict[str, list[str]] = {}
    for run, rows in by_run.items():
        lines = out.setdefault(run, [])
        last_try = {}
        for r in rows:
            last_try[r["phase"]] = max(last_try.get(r["phase"], 0), r.get("attempt", 0))
        for r in rows:
            prefix = f"  {r.get('trace') or r['phase']}:"
            seed = r.get("seed") or {}
            if seed:
                counts = ", ".join(f"{v} {k}" for k, v in seed.items() if k != "failures")
                lines.append(f"{prefix} {'OK  ' if not seed.get('failures') else 'WARN'} seeded "
                             f"{counts}; {seed.get('failures', 0)} rejected by the app")
            lines.append(f"{prefix} {'OK  ' if r.get('used_forced_db') else 'WARN'} data is in the forced database"
                         if r.get("app_started") else f"{prefix} WARN app did not start (see app_{r['phase']}.log)")
            failed = {k: v for k, v in (r.get("checks") or {}).items() if not v.get("pass")}
            if r.get("app_started"):
                # Only the phase's last attempt counts; earlier failures are what the fix rounds were for.
                status = "OK  " if not failed else "info" if r.get("attempt", 0) < last_try[r["phase"]] else "FAIL"
                lines.append(f"{prefix} {status} API checks passed: {r.get('checks_passed')}/{r.get('checks_total')}"
                             + (" (fixed in a later round)" if status == "info" else ""))
                lines += [f"{prefix}      {k}" + (f": {v.get('detail', '')}" if details else "") for k, v in failed.items()]
        for later in rows[1:]:
            label = later.get("trace") or later["phase"]
            lines += [f"  {label}: {line}" for line in sc.storage_lines(scenario_dir / run, rows[0], {**later, "phase": label})]
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", choices=SCENARIOS, help="only this scenario (default: all with results)")
    p.add_argument("--details", action="store_true",
                   help="also show every run's phases, tool calls by intent, and the full verification with failure messages")
    p.add_argument("--runs", type=Path, default=RUNS,
                   help="results folder to read (default: runs/; e.g. runs_standalone for archived results)")
    a = p.parse_args()

    found = False
    for scenario in [a.scenario] if a.scenario else SCENARIOS:
        raw = load(a.runs / scenario)
        if raw:
            found = True
            report(scenario, raw, a.details, a.runs)
    if not found:
        print(f"No metrics found under {a.runs}")


def report(scenario: str, raw: list[dict[str, Any]], details: bool, runs: Path) -> None:
    sc = load_scenario(scenario)
    phase_order = {ph["name"]: i for i, ph in enumerate(sc.PHASES)}
    kind_of = {ph["name"]: ph.get("kind", "feature") for ph in sc.PHASES}
    expected = {ph["name"]: (ph.get("favours"), ph.get("mechanism")) for ph in sc.PHASES}
    for r in raw:  # rows recorded before phases had a kind
        r.setdefault("kind", kind_of.get(r["phase"], "feature"))
    flat = [flatten(r) for r in raw]
    tag = scenario.upper()
    costs = ["total_cost", "feature_cost"] + (["improvement_cost"] if "improvement" in kind_of.values() else []) \
        + ["fix_cost"]
    effort = ["turns", "minutes", "tokens_m"] + (["explore", "edit", "exec"] if details else [])
    code = ["files", "added", "removed"]
    print("=" * 100 + f"\nSCENARIO: {scenario}  ({len({f['run'] for f in flat})} runs)\n" + "=" * 100 + "\n")

    if details:
        print(f"[{tag}] EFFORT PER PHASE  (try 0 = the change request, 1+ = fix rounds; explore/edit/exec/install/db = "
              "tool calls by intent, one call can count twice)\n")
        print_table(flat, ["run", "phase", "kind", "try", "status", "cost_usd", "turns", "minutes", "tokens_m",
                           "output_k", "explore", "edit", "exec", "install", "db_calls", "tool_err"])
        print(f"[{tag}] CODE AND OUTCOME PER PHASE\n")
        print_table(flat, ["run", "phase", "try", "app", "files", "added", "removed", "total_lines", "largest_file",
                           "unexecuted", "checks", "db_design"])

    # One row per phase attempted: the change request itself, plus what its fix rounds cost and achieved.
    pairs = list(zip(raw, flat))
    features = []
    for r, f in pairs:
        if f["try"]:
            continue
        fixes = [(r2, f2) for r2, f2 in pairs if f2["try"] and (f2["run"], f2["phase"]) == (f["run"], f["phase"])]
        last_r, last_f = max(fixes, key=lambda p: p[1]["try"]) if fixes else (r, f)
        cost, fix_cost = f["cost_usd"] or 0, round(sum(f2["cost_usd"] or 0 for _, f2 in fixes), 2)
        features.append((r, last_r, {**f, "fixes": len(fixes), "final_pass": last_f["pass_rate"], "fix_cost": fix_cost,
                                     "total_cost": round(cost + fix_cost, 2),
                                     "feature_cost": cost if f["kind"] == "feature" else None,
                                     "improvement_cost": cost if f["kind"] == "improvement" else None}))
    outcome = ["pass_rate", "fixes", "final_pass"]

    print(f"[{tag}] MEDIANS PER DATABASE AND PHASE  (± half the range across runs; total_cost = the change plus its "
          "fix rounds; pass_rate = first try, final_pass = after fix rounds)\n")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for _, _, f in features:
        groups.setdefault((f["arm"], f["phase"]), []).append(f)
    agg = []
    for (arm, phase), fs in sorted(groups.items(), key=lambda kv: (phase_order.get(kv[0][1], 99), kv[0][0])):
        favours, mechanism = expected.get(phase, (None, None))
        agg.append({"phase": phase, "kind": kind_of.get(phase), "favours": favours, "mechanism": mechanism,
                    "arm": arm, "n": len(fs),
                    **{k: median_spread([f[k] for f in fs])
                       for k in (*costs, *effort, *code, "total_lines", "largest_file", "unexecuted", *outcome)}})
    print_table(agg, ["phase", "kind", "favours", "mechanism", "arm", "n", *costs, *effort, *code, "total_lines",
                      "largest_file", "unexecuted", *outcome])

    print(f"[{tag}] TOTALS PER DATABASE  (complete runs only)\n")
    by_run: dict[str, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]] = {}
    for triple in features:
        by_run.setdefault(triple[2]["run"], []).append(triple)
    per_run = []
    for triples in by_run.values():
        if {f["phase"] for _, _, f in triples} != set(phase_order):
            continue
        last = max((f for _, _, f in triples), key=lambda f: phase_order[f["phase"]])
        ratio = lambda rows: (sum(r.get("checks_passed") or 0 for r in rows) /
                              (sum(r.get("checks_total") or 0 for r in rows) or 1))
        cov = [r["coverage"] for r, _, _ in triples if r.get("coverage")]
        added = sum(c["added_lines"] for c in cov)
        per_run.append({"arm": last["arm"],
                        **{k: sum(f[k] or 0 for _, _, f in triples) for k in (*costs, *effort, *code, "fixes")},
                        "total_lines": last["total_lines"], "largest_file": last["largest_file"],
                        "unexecuted": round(sum(c["unexecuted_lines"] for c in cov) / added, 3) if added else None,
                        "pass_rate": ratio([r for r, _, _ in triples]),
                        "final_pass": ratio([r for _, r, _ in triples])})
    totals = []
    for arm in sorted({t["arm"] for t in per_run}):
        ts = [t for t in per_run if t["arm"] == arm]
        totals.append({"arm": arm, "n": len(ts),
                       **{k: median_spread([t[k] for t in ts])
                          for k in (*costs, *effort, *code, "total_lines", "largest_file", "unexecuted", *outcome)}})
    if totals:
        print_table(totals, ["arm", "n", *costs, *effort, *code, "total_lines", "largest_file", "unexecuted",
                             *outcome])
    else:
        print("no complete runs yet\n")

    checked = verification(raw, sc, runs / scenario, details)
    flagged = [run for run, lines in checked.items() if any(" WARN " in l or " FAIL " in l for l in lines)]
    if details:
        print(f"[{tag}] VERIFICATION\n")
        for run, lines in checked.items():
            print(f"== {run}\n" + "\n".join(lines) + "\n")
    elif flagged:
        print(f"[{tag}] VERIFICATION: {len(flagged)}/{len(checked)} runs need a look ({', '.join(flagged)}); "
              f"see --details\n")
    else:
        print(f"[{tag}] VERIFICATION: all {len(checked)} runs OK\n")

    out = runs / scenario / "summary.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)
    if details:
        print(f"Wrote {out}")


if __name__ == "__main__":
    main()
