"""Static code-quality metrics of a run's final code, with lizard (about 20 languages, no setup per language).

What the agent left behind, independent of the app: how concentrated the code is, how long and branchy its
functions are, and how much of it is duplicated. Third-party code copied into the repo, build output, tests
and dependencies are left out. Results are cached per commit in <run>/code_quality.json.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import lizard
from lizard_languages import get_reader_for

# Not the agent's own application code: dependencies, vendored copies, build output, tests.
SKIP = re.compile(r"(^|/)(node_modules|vendor|third_party|\.venv|venv|__pycache__|dist|build|coverage|tests?|"
                  r"__tests__|spec)/|\.min\.js$|(^|/)test_[^/]*$|[._-](test|spec)\.[a-z]+$", re.I)
# McCabe's own threshold for a function that is hard to test and change.
COMPLEX = 10
COLUMNS = {
    "code_nloc": "lines of code (no blanks or comments) in the agent's own source files",
    "top_file_pct": "share of the code in the largest file",
    "fn_len_p90": "90th percentile of function length, lines of code",
    "fn_len_max": "the longest function, lines of code",
    "ccn_p90": "90th percentile of cyclomatic complexity per function",
    "ccn_max": "the highest cyclomatic complexity of any function",
    "complex_pct": f"share of the code in functions with cyclomatic complexity above {COMPLEX}",
    "dup_pct": "share of the code that is duplicated (lizard's duplicate detector)",
}
VERSION = 2  # bump when the metrics change, so cached results are recomputed


def percentile(values: list[int], q: float) -> int:
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else 0


def source_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and not rel.startswith(".git/") and not SKIP.search(rel) and get_reader_for(path.name):
            files.append(path)
    return sorted(files)


def duplicate_rate(files: list[Path], workdir: Path) -> float | None:
    """lizard's duplicate detector reports a total rate only on the command line."""
    listing = workdir / "files.txt"
    listing.write_text("\n".join(str(f) for f in files), encoding="utf-8")
    out = subprocess.run([sys.executable, "-m", "lizard", "-Eduplicate", "-f", str(listing)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    m = re.search(r"Total duplicate rate:\s*([\d.]+)%", out)
    return round(float(m.group(1)), 1) if m else None


def analyze(root: Path, workdir: Path) -> dict[str, Any]:
    files = source_files(root)
    infos = [lizard.analyze_file(str(f)) for f in files]
    nloc = sum(i.nloc for i in infos)
    if not nloc:
        return {"code_nloc": 0, "code_files": 0}
    fns = [fn for i in infos for fn in i.function_list]
    biggest = max(infos, key=lambda i: i.nloc)
    return {
        "code_nloc": nloc,
        "code_files": len([i for i in infos if i.nloc]),
        "top_file": Path(biggest.filename).relative_to(root).as_posix(),
        "top_file_pct": round(100 * biggest.nloc / nloc),
        "functions": len(fns),
        "fn_len_p90": percentile([f.nloc for f in fns], 0.9),
        "fn_len_max": max((f.nloc for f in fns), default=0),
        "ccn_p90": percentile([f.cyclomatic_complexity for f in fns], 0.9),
        "ccn_max": max((f.cyclomatic_complexity for f in fns), default=0),
        "complex_pct": round(100 * sum(f.nloc for f in fns if f.cyclomatic_complexity > COMPLEX) / nloc),
        "dup_pct": duplicate_rate(files, workdir),
        "languages": sorted({get_reader_for(Path(i.filename).name).__name__.replace("Reader", "")
                             for i in infos if i.nloc}),
    }


def for_run(run_dir: Path, commit: str) -> dict[str, Any] | None:
    """Metrics of the run's code at `commit`, from its repo.bundle; cached per commit."""
    cache = run_dir / "code_quality.json"
    cached = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    hit = cached.get(commit)
    if hit and hit.get("version") == VERSION:
        return hit
    bundle = run_dir / "repo.bundle"
    if not bundle.exists():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        code = Path(tmp) / "code"
        clone = subprocess.run(["git", "clone", "-q", "--no-checkout", str(bundle), str(code)], capture_output=True)
        checkout = clone.returncode or subprocess.run(["git", "-C", str(code), "checkout", "-q", commit],
                                                      capture_output=True).returncode
        if checkout:
            return None
        result = {"version": VERSION, **analyze(code, Path(tmp))}
    cached[commit] = result
    cache.write_text(json.dumps(cached, indent=1), encoding="utf-8")
    return result
