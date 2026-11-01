"""Run several lifecycles in parallel from one terminal.

Usage:
  uv run -m harness.batch --scenario crm inspections --db postgres mongodb --repetition 1-3 --parallel 6 --fix-rounds 2
  uv run -m harness.batch ... -- --fix-rounds 1        (anything after -- is passed on to harness.run)

--repetition takes repetition numbers, like harness.run: 1-3 means runs rep1, rep2 and rep3.
The planned runs are listed and must be confirmed before anything starts (--yes skips the question).
The console shows each run's one-line phase results; the full output of every run is appended to
runs/<scenario>/<db>_rep<N>/console.log. Ctrl+C stops starting new runs; running ones clean up.
"""
from __future__ import annotations

import argparse
import itertools
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from harness.run import ARMS, ROOT, RUNS, SCENARIOS, load_scenario, phases_done, read_rows, resume_point

_print_lock = threading.Lock()
_stopping = threading.Event()


def say(line: str) -> None:
    with _print_lock:
        print(line, flush=True)


def repetitions(text: str) -> list[int]:
    """'1-3' -> [1, 2, 3]; '2,5' -> [2, 5]."""
    out: list[int] = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


def run_one(scenario: str, db: str, rep: int, extra: list[str]) -> tuple[str, int]:
    name = f"{scenario}/{db}_rep{rep}"
    if _stopping.is_set():
        return name, -1
    folder = RUNS / scenario / f"{db}_rep{rep}"
    folder.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "harness.run", "--scenario", scenario, "--db", db, "--repetition", str(rep), *extra]
    say(f"[{name}] started")
    tail: list[str] = []
    with open(folder / "console.log", "a", encoding="utf-8") as log:
        # stdin=DEVNULL: an inherited stdin makes docker compose hang after the container exits.
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                env={**os.environ, "PYTHONUNBUFFERED": "1"})
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
    argv = sys.argv[1:]
    extra = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[:argv.index("--")] if "--" in argv else argv
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", nargs="+", required=True, choices=SCENARIOS)
    p.add_argument("--db", nargs="+", required=True, choices=list(ARMS), help="one or both: postgres mongodb")
    p.add_argument("--repetition", required=True, type=repetitions,
                   help="which repetition numbers (not how many): e.g. 1-3, or 4,5")
    p.add_argument("--parallel", type=int, default=6,
                   help="runs at the same time (default 6); each needs a few GB of Docker memory")
    p.add_argument("--fix-rounds", type=int, required=True,
                   help="bug-report rounds after a phase with failing checks (0 = off)")
    p.add_argument("--yes", action="store_true", help="start without asking for confirmation")
    p.add_argument("--resume", action="store_true",
                   help="continue existing runs with the phases they haven't done yet (see harness.run --resume)")
    p.add_argument("--overwrite", action="store_true", help="delete existing runs' results and start them again")
    a = p.parse_args(argv)
    if a.resume and a.overwrite:
        sys.exit("use either --resume or --overwrite, not both")

    # Repetition first, so both databases and all scenarios progress evenly.
    jobs = [(s, d, r) for r, s, d in itertools.product(a.repetition, a.scenario, a.db)]
    total = {s: len(load_scenario(s).PHASES) for s in a.scenario}
    done = {job: len(phases_done(RUNS / job[0] / f"{job[1]}_rep{job[2]}")) for job in jobs}
    existing = [job for job in jobs if done[job]]
    if existing and not (a.resume or a.overwrite):
        say(f"{len(existing)} of the {len(jobs)} planned runs already have results:")
        for s, d, r in existing:
            say(f"  {s}/{d}_rep{r}: {done[(s, d, r)]}/{total[s]} phases done")
        choice = "" if a.yes else input("[r]esume them, [o]verwrite them, or [c]ancel? ").strip().lower()[:1]
        if choice not in ("r", "o"):
            sys.exit("Nothing started." + (" (--yes needs --resume or --overwrite for existing runs)" if a.yes else ""))
        a.resume, a.overwrite = choice == "r", choice == "o"
    if a.resume:
        complete = [job for job in existing if resume_point(read_rows(RUNS / job[0] / f"{job[1]}_rep{job[2]}"),
                                                            total[job[0]], a.fix_rounds) is None]
        for s, d, r in complete:
            say(f"  {s}/{d}_rep{r}: all {total[s]} phases done, skipped")
        jobs = [job for job in jobs if job not in complete]
    flag = ["--resume"] if a.resume else ["--overwrite"] if a.overwrite else []
    extra = ["--fix-rounds", str(a.fix_rounds), *extra]

    say(f"About to start {len(jobs)} runs, {a.parallel} at a time" + (f", passing on: {' '.join(extra)}" if extra else ""))
    for s, d, r in jobs:
        mode = ("resume" if a.resume else "overwrite") if done[(s, d, r)] else "new"
        say(f"  {s}/{d}_rep{r}  ({mode})")
    if not jobs:
        sys.exit("Nothing to run.")
    if not a.yes and input("Start? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("Nothing started.")
    say("Building the agent image")
    version = os.environ.get("CLAUDE_CODE_VERSION") or "latest"
    for attempt in range(3):  # Docker Desktop on Windows sometimes reports its context metadata as locked
        build = subprocess.run(["docker", "build", "-q", "-t", f"pilot-workspace:{version}", "--build-arg",
                                f"CLAUDE_CODE_VERSION={version}",
                                str(ROOT / "agent")], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if not build.returncode or "being used by another process" not in build.stderr:
            break
        time.sleep(3)
    if build.returncode:
        sys.exit(f"Building the agent image failed:\n{build.stderr[-2000:]}")
    os.environ["HARNESS_IMAGE_BUILT"] = "1"
    results: list[tuple[str, int]] = []
    pool = ThreadPoolExecutor(a.parallel)
    futures = [pool.submit(run_one, s, d, r, extra + (flag if done[(s, d, r)] else [])) for s, d, r in jobs]
    try:
        for f in futures:
            results.append(f.result())
    except KeyboardInterrupt:
        _stopping.set()
        say("Stopping: no new runs will start; waiting for running ones to clean up (Ctrl+C again to force)")
        pool.shutdown(wait=True, cancel_futures=True)
        results = [f.result() for f in futures if f.done() and not f.cancelled()]
    pool.shutdown()

    done = [n for n, code in results if code == 0]
    failed = [n for n, code in results if code > 0]
    skipped = len(jobs) - len(done) - len(failed)
    say(f"\nFinished: {len(done)} ok, {len(failed)} failed" + (f", {skipped} not started" if skipped else ""))
    for n in failed:
        say(f"  failed: {n}  (see {RUNS / n / 'console.log'})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
