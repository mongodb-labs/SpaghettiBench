"""Over-engineering metric: the share of a phase's added code lines that no check executes.

The app runs with Node's built-in coverage (NODE_V8_COVERAGE). A preloaded script writes the coverage
when the harness asks (after the checks), so nothing depends on how the app shuts down. Migration files
and the agent's own tests are left out: applied migrations never run again, and tests aren't the app.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse

COVERAGE_DIR = "/tmp/.cov"
PRELOAD = "/tmp/.cov.js"
FLUSH = "/tmp/.cov-flush"
# Loaded into every node process via NODE_OPTIONS; writes coverage when FLUSH changes.
PRELOAD_JS = f"""
const fs = require('fs'), v8 = require('v8');
let seen = 0;
setInterval(() => {{
  try {{
    const t = fs.statSync('{FLUSH}').mtimeMs;
    if (t > seen) {{ seen = t; v8.takeCoverage(); fs.writeFileSync('{COVERAGE_DIR}/flushed-' + process.pid, ''); }}
  }} catch (e) {{}}
}}, 200).unref();
"""
CODE_FILE = re.compile(r"\.(c|m)?js$")
NOT_APP = re.compile(r"(^|/)(test|tests|__tests__|spec|node_modules)/|\.(test|spec)\.[cm]?js$"
                     r"|migration|alembic|schema_version|knex_|drizzle|changelog", re.I)


def added_lines(diff: str) -> dict[str, set[int]]:
    """Line numbers added per file, from `git diff -U0` output."""
    out: dict[str, set[int]] = {}
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and path:
            m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                out.setdefault(path, set()).update(range(start, start + count))
    return {p: lines for p, lines in out.items() if CODE_FILE.search(p) and not NOT_APP.search(p)}


def executed_lines(coverage: list[dict[str, Any]], app_root: str, sources: dict[str, str]) -> dict[str, set[int]]:
    """Lines with code that ran at least once, per repo-relative path, merged over all coverage files."""
    out: dict[str, set[int]] = {}
    for cov in coverage:
        for script in cov.get("result") or []:
            url = script.get("url") or ""
            if not url.startswith("file://"):
                continue
            path = unquote(urlparse(url).path).removeprefix(app_root.rstrip("/") + "/")
            if path not in sources:
                continue
            # V8 offsets count UTF-16 code units: per unit, its line and whether it's code (not whitespace).
            line_of, is_code, n = [], [], 1
            for ch in sources[path]:
                for _ in range(2 if ord(ch) > 0xFFFF else 1):
                    line_of.append(n)
                    is_code.append(not ch.isspace())
                n += ch == "\n"
            counts = [0] * len(line_of)
            # Innermost ranges win: apply outer before inner.
            ranges = sorted((r for f in script.get("functions") or [] for r in f.get("ranges") or []),
                            key=lambda r: (r["startOffset"], -r["endOffset"]))
            for r in ranges:
                start, end = r["startOffset"], min(r["endOffset"], len(counts))
                if start < end:
                    counts[start:end] = [r["count"]] * (end - start)
            ran = out.setdefault(path, set())
            ran.update(line_of[u] for u in range(len(counts)) if counts[u] and is_code[u])
    return out


def summarize(added: dict[str, set[int]], ran: dict[str, set[int]], sources: dict[str, str]) -> dict[str, Any] | None:
    """Added code lines (blank lines left out) and how many of them never ran; None if coverage saw no app file."""
    if not ran:
        return None
    files = {}
    for path, lines in added.items():
        text = sources.get(path, "").split("\n")
        code = {n for n in lines if n <= len(text) and text[n - 1].strip()}
        if code:
            files[path] = [len(code), len(code - ran.get(path, set()))]
    total, unexecuted = sum(a for a, _ in files.values()), sum(u for _, u in files.values())
    return {"added_lines": total, "unexecuted_lines": unexecuted,
            "unexecuted_share": round(unexecuted / total, 3) if total else None, "files": files}
