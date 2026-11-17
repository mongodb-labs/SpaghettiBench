"""Give finished apps the same unseen change request (a probe), to compare how easy each one's code is to change.

Usage:
  uv run -m harness.probe --scenario shop --probe vat --fix-rounds 2 [--runs postgres_rep1 mongodb_merged_rep1]
                          [--parallel 4] [--yes] [--overwrite]

For each complete run of the scenario (step by step and one-shot; --runs picks some), in its own Docker project:
restore the run's final code and database (like --resume), give a fresh agent the probe's prompt
(scenario.toml [[probes]]) with constraints.md, deploy, then run every phase's checks (failures are regressions)
and the probe's own checks, with the usual fix rounds. Results go to <run>/probes/<probe>/ and appear in
harness.report. A run already probed is skipped unless --overwrite. Run probes while the run itself isn't running:
they use its app port.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness.batch import build_image, say
from harness.run import RUNS, SCENARIOS, Run, clear_results, load_scenario, read_rows


def complete_runs(scenario: str) -> list[str]:
    """Run folders that went through all their phases (or one-shot steps)."""
    sc = load_scenario(scenario)
    out = []
    for folder in sorted((RUNS / scenario).iterdir()) if (RUNS / scenario).exists() else []:
        rows = read_rows(folder)
        if not rows:
            continue
        steps = sc.MERGED if rows[0].get("mode") == "merged" else sc.PHASES
        if list(dict.fromkeys(r["phase"] for r in rows)) == [s["name"] for s in steps]:
            out.append(folder.name)
    return out


def probe_one(scenario: str, run_name: str, probe: str, a: argparse.Namespace) -> None:
    source = RUNS / scenario / run_name
    rows = read_rows(source)
    recorded = json.loads((source / "meta.json").read_text(encoding="utf-8")).get("model")
    if recorded != a.model:
        sys.exit(f"{run_name} was recorded with {recorded}, not {a.model}")
    first, last = rows[0], rows[-1]
    run = Run(scenario, first["arm"], first["repetition"], a.model, a.max_turns, a.timeout_min * 60,
              first.get("mode", "incremental"), probe=probe)
    if run.dir.exists():
        clear_results(run.dir)
    run.dir.mkdir(parents=True, exist_ok=True)
    restored = run.restore(source, last)
    meta = {"probe": probe, "source": run_name, "source_commit": last["commit"], "scenario": scenario, "arm": run.arm,
            "repetition": run.rep, "mode": run.mode, "model": a.model, "max_turns": a.max_turns,
            "claude_code_version": run.sh("claude --version"), "base_url": run.base_url,
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    (run.dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"[{run.name}] restored {run_name} after {last['trace']} ({restored} records)", flush=True)
    try:
        run.phase(run.phases[0], 1, a.fix_rounds)
    except BaseException:
        run.dc("stop")
        print(f"[{run.name}] failed; containers kept. Remove them with: docker compose -p {run.project} down -v",
              flush=True)
        raise
    run.dc("down", "-v")
    print(f"[{run.name}] done: {run.dir}", flush=True)


def spawn(scenario: str, run_name: str, probe: str, a: argparse.Namespace) -> tuple[str, int]:
    """One probe in its own process (like harness.batch), its output appended to the probe folder's console.log."""
    name = f"{scenario}/{run_name}+{probe}"
    folder = RUNS / scenario / run_name / "probes" / probe
    folder.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "harness.probe", "--scenario", scenario, "--probe", probe, "--one", run_name,
           "--fix-rounds", str(a.fix_rounds), "--max-turns", str(a.max_turns), "--timeout-min", str(a.timeout_min),
           "--model", a.model]
    say(f"[{name}] started")
    tail: list[str] = []
    with open(folder.parent / f"{probe}.console.log", "a", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUNBUFFERED": "1"})
        for line in proc.stdout:  # type: ignore[union-attr]
            log.write(line)
            log.flush()
            tail = (tail + [line.rstrip()])[-8:]
            if line.startswith("["):
                say(line.rstrip())
        code = proc.wait()
    if code:
        say(f"[{name}] FAILED (exit {code}); last output:\n    " + "\n    ".join(tail))
    return name, code


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", required=True, choices=SCENARIOS)
    p.add_argument("--probe", required=True, help="a [[probes]] name from the scenario.toml, e.g. vat")
    p.add_argument("--runs", nargs="+", help="run folders to probe (default: every complete run)")
    p.add_argument("--fix-rounds", type=int, required=True, help="bug-report rounds after failing checks (0 = off)")
    p.add_argument("--parallel", type=int, default=4, help="probes at the same time (default 4)")
    p.add_argument("--max-turns", type=int, default=200, help="turn budget of the probe session (as one phase)")
    p.add_argument("--timeout-min", type=int, default=180, help="wall-clock limit of the probe session")
    p.add_argument("--model", default=os.environ.get("CLAUDE_MODEL"), help="default: CLAUDE_MODEL from .env")
    p.add_argument("--overwrite", action="store_true", help="probe runs again that were already probed")
    p.add_argument("--yes", action="store_true", help="start without asking")
    p.add_argument("--one", help=argparse.SUPPRESS)  # internal: probe this one run in this process
    a = p.parse_args()
    sc = load_scenario(a.scenario)
    if a.probe not in [pr["name"] for pr in sc.PROBES]:
        sys.exit(f"scenario {a.scenario} has no probe {a.probe!r} (its scenario.toml [[probes]])")
    if not a.model:
        sys.exit("No model: set CLAUDE_MODEL in .env or pass --model")
    if a.one:
        probe_one(a.scenario, a.one, a.probe, a)
        return

    runs = a.runs or complete_runs(a.scenario)
    incomplete = sorted(set(runs) - set(complete_runs(a.scenario)))
    if incomplete:
        sys.exit(f"not complete runs: {', '.join(incomplete)}")
    done = [r for r in runs if read_rows(RUNS / a.scenario / r / "probes" / a.probe)]
    if done and not a.overwrite:
        say(f"already probed, skipped (--overwrite to redo): {', '.join(done)}")
        runs = [r for r in runs if r not in done]
    if not runs:
        sys.exit("Nothing to probe.")
    say(f"About to probe {len(runs)} runs of {a.scenario} with {a.probe!r}, {a.parallel} at a time:")
    for r in runs:
        say(f"  {r}")
    if not a.yes and input("Start? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("Nothing started.")
    build_image()
    with ThreadPoolExecutor(a.parallel) as pool:
        results = list(pool.map(lambda r: spawn(a.scenario, r, a.probe, a), runs))
    failed = [n for n, code in results if code]
    say(f"\nFinished: {len(results) - len(failed)} ok, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
