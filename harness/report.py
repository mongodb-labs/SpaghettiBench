"""Summarise all runs per scenario: effort, code and outcome per phase, medians and totals per database,
and verification.

Usage: uv run -m harness.report [--scenario NAME] [--details] [--runs DIR] [--open]
Effort is recomputed from the transcripts, so older runs pick up classifier improvements.
Also writes <runs>/<scenario>/summary.csv, and <runs>/report.html: everything including the details, with
sortable tables (--open opens it in the browser).
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import webbrowser
from pathlib import Path
from types import ModuleType
from typing import Any

from harness import code_quality
from harness.report_html import Html
from harness.run import RUNS, SCENARIOS, load_scenario
from harness.traces import trace_metrics


def load(scenario_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(scenario_dir.glob("*/metrics.jsonl")):
        for row in load_rows(path):
            row["run"] = path.parent.name
            row.setdefault("mode", "merged" if "_merged_" in path.parent.name else "incremental")
            rows.append(row)
    return rows


def load_rows(path: Path) -> list[dict[str, Any]]:
    """A metrics.jsonl, with effort recomputed from the transcripts next to it."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
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
        "run": r["run"], "arm": r["arm"], "mode": r.get("mode", "incremental"), "phase": r["phase"],
        "kind": r.get("kind"), "try": r.get("attempt", 0),
        "status": r.get("status"),
        "app": "up" if r.get("app_started") else "DOWN",
        "cost_usd": round(r["cost_usd"], 2) if isinstance(r.get("cost_usd"), (int, float)) else None,
        "turns": r.get("turns"),
        "minutes": round((r.get("wall_s") or 0) / 60, 1),
        "tokens_m": round((r.get("tokens_total") or 0) / 1e6, 2),
        "output_k": round(((r.get("tokens") or {}).get("output") or 0) / 1e3, 1),
        "compactions": r.get("compactions"),
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


class Console:
    """Prints the report to the terminal; details-only parts were already left out by the caller."""

    def __init__(self, details: bool):
        self.details = details

    def scenario(self, name: str, runs: int) -> None:
        print("=" * 100 + f"\nSCENARIO: {name}  ({runs} runs)\n" + "=" * 100 + "\n")

    def heading(self, tag: str, title: str, note: str = "", detail: bool = False) -> None:
        print(f"[{tag}] {title}" + (f"  ({note})" if note else "") + "\n")

    def table(self, rows: list[dict[str, Any]], cols: list[str], detail: bool = False) -> None:
        print_table(rows, cols)

    def note(self, text: str) -> None:
        print(text + "\n")

    def verification(self, tag: str, checked: dict[str, list[str]], flagged: list[str]) -> None:
        if self.details:
            print(f"[{tag}] VERIFICATION\n")
            for run, lines in checked.items():
                print(f"== {run}\n" + "\n".join(lines) + "\n")
        elif flagged:
            print(f"[{tag}] VERIFICATION: {len(flagged)}/{len(checked)} runs need a look ({', '.join(flagged)}); "
                  f"see --details\n")
        else:
            print(f"[{tag}] VERIFICATION: all {len(checked)} runs OK\n")


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
    # One-shot build steps aren't fixed for failing checks: the later data-change steps do that part.
    startup_only = {m["name"] for m in getattr(sc, "MERGED", []) if m.get("fix") == "startup"}
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
                later = r.get("attempt", 0) < last_try[r["phase"]]
                before_data = r.get("mode") == "merged" and r["phase"] in startup_only
                status = "OK  " if not failed else "info" if later or before_data else "FAIL"
                lines.append(f"{prefix} {status} API checks passed: {r.get('checks_passed')}/{r.get('checks_total')}"
                             + (" (fixed in a later round)" if failed and later else
                                " (before the data changes)" if failed and before_data else ""))
                lines += [f"{prefix}      {k}" + (f": {v.get('detail', '')}" if details else "") for k, v in failed.items()]
        for later in rows[1:]:
            label = later.get("trace") or later["phase"]
            lines += [f"  {label}: {line}" for line in sc.storage_lines(scenario_dir / run, rows[0], {**later, "phase": label})]
    return out


def check_origin(sc: ModuleType) -> dict[str, str]:
    """Check-name prefix -> the phase that introduced it: features v0, v1, … and improvements i1, i2, … in order."""
    out, features, improvements = {}, 0, 1
    for ph in sc.PHASES:
        if ph.get("kind", "feature") == "improvement":
            out[f"i{improvements}"], improvements = ph["name"], improvements + 1
        else:
            out[f"v{features}"], features = ph["name"], features + 1
    return out


def complete_runs(sc: ModuleType, raw: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Rows of every run that went through all its phases (or one-shot steps), by run."""
    expected = {"incremental": [ph["name"] for ph in sc.PHASES], "merged": [m["name"] for m in sc.MERGED]}
    by_run: dict[str, list[dict[str, Any]]] = {}
    for r in raw:
        by_run.setdefault(r["run"], []).append(r)
    return {run: rows for run, rows in by_run.items()
            if list(dict.fromkeys(r["phase"] for r in rows)) == expected[rows[0]["mode"]]}


def code_quality_section(sc: ModuleType, raw: list[dict[str, Any]], scenario_dir: Path, tag: str, out: Any) -> None:
    """Static metrics of each complete run's final code (harness/code_quality.py), medians per database and mode."""
    per_run = []
    for run, rows in complete_runs(sc, raw).items():
        q = code_quality.for_run(scenario_dir / run, rows[-1]["commit"])
        if q and q.get("code_nloc"):
            per_run.append({"run": run, "arm": rows[0]["arm"], "mode": rows[0]["mode"], **q,
                            "languages": ", ".join(q.get("languages", []))})
    if not per_run:
        return
    cols = list(code_quality.COLUMNS)
    out.heading(tag, "CODE QUALITY AT THE END", "complete runs; the agent's own source code, without dependencies, "
                "vendored copies, build output or tests; lower is cleaner for every column but code_nloc")
    groups = sorted({(t["arm"], t["mode"]) for t in per_run})
    out.table([{"arm": arm, "mode": mode, "n": len(ts), **{k: median_spread([t[k] for t in ts]) for k in cols}}
               for arm, mode in groups for ts in [[t for t in per_run if (t["arm"], t["mode"]) == (arm, mode)]]],
              ["arm", "mode", "n", *cols])
    if out.details:
        out.heading(tag, "CODE QUALITY PER RUN", detail=True)
        out.table(sorted(per_run, key=lambda t: (t["arm"], t["mode"], t["run"])),
                  ["run", "arm", "mode", *cols, "code_files", "functions", "top_file", "languages"])


def probe_section(scenario_dir: Path, tag: str, out: Any) -> None:
    """Change requests given to finished runs (harness.probe): what each cost, and whether it worked without breaking
    anything. A probe's own checks are named after it (e.g. vat.*); every other failing check is a regression."""
    per_run = []
    for path in sorted(scenario_dir.glob("*/probes/*/metrics.jsonl")):
        rows = load_rows(path)
        if not rows:
            continue
        probe, first, last = path.parent.name, rows[0], rows[-1]
        own = lambda r: {k: v for k, v in (r.get("checks") or {}).items() if k.startswith(probe + ".")}
        other = lambda r: {k: v for k, v in (r.get("checks") or {}).items() if not k.startswith(probe + ".")}
        share = lambda checks: round(sum(bool(v.get("pass")) for v in checks.values()) / len(checks), 2) if checks else 0.0
        broken = lambda r: sum(not v.get("pass") for v in other(r).values()) if r.get("app_started") else None
        per_run.append({
            "probe": probe, "run": path.parents[2].name, "arm": first["arm"], "mode": first.get("mode", "incremental"),
            "total_cost": round(sum(r.get("cost_usd") or 0 for r in rows), 2),
            "fixes": len(rows) - 1, "turns": sum(r.get("turns") or 0 for r in rows),
            "minutes": round(sum(r.get("wall_s") or 0 for r in rows) / 60, 1),
            "files": first.get("files_touched"), "added": first.get("lines_added"),
            "removed": first.get("lines_removed"),
            "pass_rate": share(own(first)), "final_pass": share(own(last)),
            "regressions": broken(first), "final_regressions": broken(last),
        })
    if not per_run:
        return
    cols = ["total_cost", "fixes", "turns", "minutes", "files", "added", "removed", "pass_rate", "final_pass",
            "regressions", "final_regressions"]
    out.heading(tag, "CHANGE-REQUEST PROBES", "the same unseen change given to every finished run (harness.probe); "
                "files/added/removed = the first try; pass_rate/final_pass = the probe's own checks; regressions = "
                "earlier checks failing after the change")
    groups = sorted({(t["probe"], t["arm"], t["mode"]) for t in per_run})
    out.table([{"probe": pr, "arm": arm, "mode": mode, "n": len(ts), **{k: median_spread([t[k] for t in ts]) for k in cols}}
               for pr, arm, mode in groups
               for ts in [[t for t in per_run if (t["probe"], t["arm"], t["mode"]) == (pr, arm, mode)]]],
              ["probe", "arm", "mode", "n", *cols])
    if out.details:
        out.heading(tag, "PROBES PER RUN", detail=True)
        out.table(sorted(per_run, key=lambda t: (t["probe"], t["arm"], t["mode"], t["run"])),
                  ["probe", "run", "arm", "mode", *cols])


def one_shot_comparison(sc: ModuleType, raw: list[dict[str, Any]], tag: str, out: Any) -> None:
    """Complete one-shot runs against complete step-by-step runs: effort, the code and data model they end with,
    and the full check suite at the end, overall and by the phase each check came from."""
    origin = check_origin(sc)
    per_run = []
    for run, rows in complete_runs(sc, raw).items():
        mode = rows[0]["mode"]
        last = rows[-1]
        checks = last.get("checks") or {}
        by_phase: dict[str, list[bool]] = {}
        for name, result in checks.items():
            by_phase.setdefault(origin.get(name.split(".")[0], "?"), []).append(bool(result.get("pass")))
        cost = lambda rs: round(sum(r.get("cost_usd") or 0 for r in rs), 2)
        per_run.append({
            "run": run, "arm": last["arm"], "mode": mode, "sessions": len(rows),
            "fixes": sum(1 for r in rows if r.get("attempt")),
            "total_cost": cost(rows), "fix_cost": cost([r for r in rows if r.get("attempt")]),
            "turns": sum(r.get("turns") or 0 for r in rows),
            "minutes": round(sum(r.get("wall_s") or 0 for r in rows) / 60, 1),
            "tokens_m": round(sum(r.get("tokens_total") or 0 for r in rows) / 1e6, 2),
            "compactions": sum(r.get("compactions") or 0 for r in rows),
            "total_lines": last.get("total_lines"), "total_files": last.get("total_files"),
            "largest_file": last.get("largest_file_lines"), "files_over_1000": last.get("files_over_1000"),
            "end_pass": round(sum(r.get("pass") is True for r in checks.values()) / (len(checks) or 1), 3),
            "db_design": db_summary(last),
            "by_phase": {p: sum(v) / len(v) for p, v in by_phase.items()},
        })
    out.heading(tag, "ONE-SHOT VS STEP BY STEP", "complete runs; merged = the whole spec at once, then the data "
                "changes; end_pass = the full check suite after the last fix round")
    if not any(t["mode"] == "merged" for t in per_run):
        out.note("no complete one-shot runs yet")
        return
    cols = ["total_cost", "fix_cost", "sessions", "fixes", "turns", "minutes", "tokens_m", "compactions",
            "total_lines", "total_files", "largest_file", "files_over_1000", "end_pass"]
    groups = sorted({(t["arm"], t["mode"]) for t in per_run})
    summary = []
    for arm, mode in groups:
        ts = [t for t in per_run if (t["arm"], t["mode"]) == (arm, mode)]
        summary.append({"arm": arm, "mode": mode, "n": len(ts), **{k: median_spread([t[k] for t in ts]) for k in cols}})
    out.table(summary, ["arm", "mode", "n", *cols])

    out.heading(tag, "DATA MODEL AT THE END, PER RUN")
    out.table(sorted(per_run, key=lambda t: (t["arm"], t["mode"], t["run"])), ["run", "arm", "mode", "db_design"])

    out.heading(tag, "END PASS RATE BY THE PHASE THE CHECKS CAME FROM", "median over runs")
    heads = [f"{arm} {'one-shot' if mode == 'merged' else 'step'}" for arm, mode in groups]
    table = []
    for stage, ph in enumerate(sc.PHASES):
        row: dict[str, Any] = {"stage": stage, "phase": ph["name"], "favours": ph.get("favours"),
                               "mechanism": ph.get("mechanism")}
        for (arm, mode), head in zip(groups, heads):
            row[head] = median_spread([t["by_phase"].get(ph["name"]) for t in per_run
                                       if (t["arm"], t["mode"]) == (arm, mode)])
        table.append(row)
    out.table(table, ["stage", "phase", "favours", "mechanism", *heads])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", choices=SCENARIOS,
                   help="only this scenario in the console (the HTML report always has every scenario with results)")
    p.add_argument("--details", action="store_true",
                   help="also show every run's phases, tool calls by intent, and the full verification with failure messages")
    p.add_argument("--runs", type=Path, default=RUNS,
                   help="results folder to read (default: runs/; e.g. runs_standalone for archived results)")
    p.add_argument("--open", action="store_true", help="open the HTML report in the browser afterwards")
    a = p.parse_args()

    console, page = Console(a.details), Html(a.runs)
    found = False
    for scenario in SCENARIOS:
        raw = load(a.runs / scenario)
        if raw:
            found = True
            if a.scenario in (None, scenario):
                report(scenario, raw, console, a.runs)
            report(scenario, raw, page, a.runs)
            write_summary_csv(raw, a.runs / scenario / "summary.csv")
    if not found:
        print(f"No metrics found under {a.runs}")
        return
    path = page.write()
    print(f"HTML report (all details): {path}")
    if a.open:
        webbrowser.open(path.resolve().as_uri())


def report(scenario: str, raw: list[dict[str, Any]], out: Any, runs: Path) -> None:
    """Writes one scenario's report to `out` (Console or Html); out.details says whether to add the details."""
    details = out.details
    sc = load_scenario(scenario)
    everything = raw
    # One-shot runs have steps instead of phases; they only appear in their own comparison section.
    merged = [r for r in raw if r["mode"] == "merged"]
    raw = [r for r in raw if r["mode"] != "merged"]
    phase_order = {ph["name"]: i for i, ph in enumerate(sc.PHASES)}
    kind_of = {ph["name"]: ph.get("kind", "feature") for ph in sc.PHASES}
    expected = {ph["name"]: (ph.get("favours"), ph.get("mechanism")) for ph in sc.PHASES}
    for r in raw:  # rows recorded before phases had a kind
        r.setdefault("kind", kind_of.get(r["phase"], "feature"))
    flat = [{**flatten(r), "stage": phase_order.get(r["phase"])} for r in raw]
    tag = scenario.upper()
    costs = ["total_cost", "feature_cost"] + (["improvement_cost"] if "improvement" in kind_of.values() else []) \
        + ["fix_cost"]
    effort = ["turns", "minutes", "tokens_m"] + (["explore", "edit", "exec"] if details else [])
    code = ["files", "added", "removed"]
    out.scenario(scenario, len({f["run"] for f in flat}))

    if details:
        out.heading(tag, "EFFORT PER PHASE", "try 0 = the change request, 1+ = fix rounds; explore/edit/exec/install/db = "
                    "tool calls by intent, one call can count twice", detail=True)
        out.table(flat, ["run", "stage", "phase", "kind", "try", "status", "cost_usd", "turns", "minutes", "tokens_m",
                         "output_k", "compactions", "explore", "edit", "exec", "install", "db_calls", "tool_err"])
        out.heading(tag, "CODE AND OUTCOME PER PHASE", detail=True)
        out.table(flat, ["run", "stage", "phase", "try", "app", "files", "added", "removed", "total_lines",
                         "largest_file", "unexecuted", "checks", "db_design"])

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

    out.heading(tag, "MEDIANS PER DATABASE AND PHASE", "± half the range across runs; total_cost = the change plus its "
                "fix rounds; pass_rate = first try, final_pass = after fix rounds")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for _, _, f in features:
        groups.setdefault((f["arm"], f["phase"]), []).append(f)
    agg = []
    for (arm, phase), fs in sorted(groups.items(), key=lambda kv: (phase_order.get(kv[0][1], 99), kv[0][0])):
        favours, mechanism = expected.get(phase, (None, None))
        agg.append({"stage": phase_order.get(phase), "phase": phase, "kind": kind_of.get(phase), "favours": favours,
                    "mechanism": mechanism,
                    "arm": arm, "n": len(fs),
                    **{k: median_spread([f[k] for f in fs])
                       for k in (*costs, *effort, *code, "total_lines", "largest_file", "unexecuted", *outcome)}})
    out.table(agg, ["stage", "phase", "kind", "favours", "mechanism", "arm", "n", *costs, *effort, *code, "total_lines",
                    "largest_file", "unexecuted", *outcome])

    out.heading(tag, "TOTALS PER DATABASE", "complete runs only")
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
        out.table(totals, ["arm", "n", *costs, *effort, *code, "total_lines", "largest_file", "unexecuted", *outcome])
    else:
        out.note("no complete runs yet")

    if merged:
        one_shot_comparison(sc, raw + merged, tag, out)

    code_quality_section(sc, everything, runs / scenario, tag, out)
    probe_section(runs / scenario, tag, out)

    checked = verification(everything, sc, runs / scenario, details)
    flagged = [run for run, lines in checked.items() if any(" WARN " in l or " FAIL " in l for l in lines)]
    out.verification(tag, checked, flagged)


def write_summary_csv(raw: list[dict[str, Any]], path: Path) -> None:
    flat = [flatten(r) for r in raw]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)


if __name__ == "__main__":
    main()
