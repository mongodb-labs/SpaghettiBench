"""Run one pilot lifecycle for one scenario and database arm: initial build, seed, then each change phase.

Usage: uv run -m harness.run --scenario NAME --db {postgres,mongodb} --repetition N [--mode merged] [--model MODEL_ID]
Settings (API key, model, Claude Code version) are read from .env in the project root.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
from dotenv import load_dotenv

from harness import coverage
from harness.traces import trace_metrics

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
AI_GATEWAY_URL = "https://ai-gateway.corp.mongodb.com/anthropic"
if os.environ.get("AI_GATEWAY_API_KEY"):
    # The AI gateway speaks the plain Anthropic API (key as x-api-key) and has no ~4-minute request limit,
    # which long one-shot requests ran into on Grove/Foundry; it takes precedence over the Foundry settings.
    # Emptied rather than removed: docker compose would otherwise take them from .env again.
    for var in ("CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_SKIP_FOUNDRY_AUTH", "ANTHROPIC_FOUNDRY_API_KEY",
                "ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS"):
        os.environ[var] = ""
    os.environ["ANTHROPIC_BASE_URL"] = os.environ.get("AI_GATEWAY_BASE_URL") or AI_GATEWAY_URL
    os.environ["ANTHROPIC_API_KEY"] = os.environ["AI_GATEWAY_API_KEY"]
# With CLAUDE_CODE_SKIP_FOUNDRY_AUTH, Claude Code sends no key; the Grove gateway wants it as an api-key header.
elif os.environ.get("ANTHROPIC_FOUNDRY_API_KEY") and not os.environ.get("ANTHROPIC_CUSTOM_HEADERS"):
    os.environ["ANTHROPIC_CUSTOM_HEADERS"] = f"api-key: {os.environ['ANTHROPIC_FOUNDRY_API_KEY']}"
RUNS = ROOT / os.environ.get("RUNS_DIR", "runs")
SCENARIOS = sorted(p.name for p in (ROOT / "scenarios").iterdir() if (p / "__init__.py").exists())
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
LOCKFILES = ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "uv.lock",
             "poetry.lock", "Pipfile.lock", "go.sum", "Cargo.lock"]
PATHSPEC = "-- . " + " ".join(f"':(exclude,glob)**/{f}'" for f in LOCKFILES)
BOOKKEEPING = re.compile(r"migration|alembic|schema_version|knex_|drizzle|changelog", re.I)
# Saved with the code after every attempt: files git ignores but the app may need (see export_repo).
EXTRA = "workspace_extra.tgz"
DEPENDENCY_DIRS = r"(^|/)(node_modules|\.venv|venv|__pycache__)/?$"


def parse_numstat(text: str) -> dict[str, tuple[int, int]]:
    """`git diff --numstat` output as path -> (lines added, lines removed); binary files count 0."""
    out = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            out[parts[2]] = (0, 0) if parts[0] == "-" else (int(parts[0]), int(parts[1]))
    return out


def size_metrics(lines: dict[str, int]) -> dict[str, Any]:
    """How concentrated the code is: the largest file, and how many files exceed 1,000 lines."""
    path, most = max(lines.items(), key=lambda kv: kv[1], default=(None, 0))
    return {"largest_file": path, "largest_file_lines": most, "files_over_1000": sum(n > 1000 for n in lines.values())}

ARMS = {
    "postgres": {
        "service": "postgres",
        "db_url": "postgres://app:app@db:5432/{db}",
        "test_url": "postgres://app:app@db:5432/{db}_test",
        "line": "The database is PostgreSQL 16, at the `DB_URL` environment variable. "
                "A separate database for your own testing is at `TEST_DB_URL`.",
    },
    "mongodb": {
        "service": "mongo",
        "db_url": "mongodb://db:27017/{db}",
        "test_url": "mongodb://db:27017/{db}_test",
        "line": "The database is MongoDB 8, running as a single-node replica set, at the `DB_URL` environment "
                "variable. A separate database for your own testing is at `TEST_DB_URL`.",
    },
}


def load_scenario(name: str) -> ModuleType:
    return importlib.import_module(f"scenarios.{name}")


def read_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "metrics.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def phases_done(run_dir: Path) -> list[str]:
    """Phases a run has recorded, in order (empty if it has no results)."""
    return list(dict.fromkeys(r["phase"] for r in read_rows(run_dir)))


def run_label(arm: str, rep: int, mode: str = "incremental") -> str:
    """A run's folder name under runs/<scenario>/: postgres_rep1, or postgres_merged_rep1 for a one-shot run."""
    return f"{arm}_merged_rep{rep}" if mode == "merged" else f"{arm}_rep{rep}"


def resume_point(rows: list[dict[str, Any]], phases: list[dict[str, Any]], fix_rounds: int) -> tuple[int, int] | None:
    """(step, attempt) to continue with: the next fix round of a failing last phase, else the next phase; None if complete."""
    done = list(dict.fromkeys(r["phase"] for r in rows))
    last = rows[-1]
    fix_checks = next((ph.get("fix") for ph in phases if ph["name"] == last["phase"]), None) != "startup"
    failing = not last["app_started"] or (fix_checks and last["checks_passed"] != last["checks_total"])
    if failing and last["attempt"] < fix_rounds:
        return len(done), last["attempt"] + 1
    return (len(done) + 1, 0) if len(done) < len(phases) else None


def clear_results(run_dir: Path) -> None:
    """Delete a run's results; the batch's console.log stays (it may be open, and old output is still useful)."""
    for child in run_dir.iterdir():
        if child.name != "console.log":
            shutil.rmtree(child) if child.is_dir() else child.unlink()


class Run:
    def __init__(self, scenario: str, arm: str, rep: int, model: str, max_turns: int, timeout_s: int,
                 mode: str = "incremental", probe: str | None = None):
        self.scenario, self.arm, self.rep, self.model, self.mode = scenario, arm, rep, model, mode
        self.sc = load_scenario(scenario)
        # What the run steps through: the scenario's phases, or its one-shot steps (scenario.toml [[merged]]).
        self.phases = self.sc.MERGED if mode == "merged" else self.sc.PHASES
        self.max_turns, self.timeout_s = max_turns, timeout_s
        label = run_label(arm, rep, mode)
        self.name = f"{scenario}/{label}"
        # Results folders other than runs/ get their own Docker projects, so their leftovers never mix.
        prefix = "" if RUNS.name == "runs" else re.sub(r"[^a-z0-9]+", "-", RUNS.name.lower()).strip("-") + "_"
        self.project = f"{prefix}{scenario}_{label}"
        self.dir = RUNS / scenario / label
        if probe:
            # A change request given to the finished run (harness.probe): its own folder and containers.
            self.phases = [p for p in self.sc.PROBES if p["name"] == probe]
            self.name, self.project, self.dir = f"{self.name}+{probe}", f"{self.project}_{probe}", self.dir / "probes" / probe
        self.db = self.sc.DB_NAME
        # Merged runs use the upper half of the arm's port range (so repetitions 1-49 don't clash).
        port = self.sc.PORT + 100 * list(ARMS).index(arm) + (50 if mode == "merged" else 0) + rep
        self.base_url = f"http://localhost:{port}"
        self.env = {**os.environ, "APP_PORT": str(port), "APP_DIR": self.sc.APP_DIR, "DB_NAME": self.db,
                    "DB_URL": ARMS[arm]["db_url"].format(db=self.db),
                    "TEST_DB_URL": ARMS[arm]["test_url"].format(db=self.db)}
        self.compose = ["docker", "compose", "-p", self.project, "-f", str(ROOT / "docker-compose.yml"),
                        "--profile", arm]
        self.seed_data: dict | None = None
        self.seeded_phases: set[str] = set()
        self.prev = ""
        self.last_row: dict[str, Any] | None = None

    # ------------------------------------------------------------ docker helpers

    def dc(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(self.compose + list(args), env=self.env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")

    def dc_bytes(self, *args: str) -> bytes:
        return subprocess.run(self.compose + list(args), env=self.env, stdin=subprocess.DEVNULL,
                              capture_output=True).stdout

    def dc_input(self, data: bytes, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(self.compose + list(args), env=self.env, input=data, capture_output=True)

    def sh(self, cmd: str) -> str:
        return self.dc("exec", "-T", "workspace", "bash", "-lc", cmd).stdout.strip()

    def commit(self, msg: str) -> str:
        self.sh(f"git add -A && git commit -q --allow-empty -m '{msg}'")
        return self.sh("git rev-parse HEAD")

    # ------------------------------------------------------------ lifecycle

    def start_containers(self) -> None:
        volumes = subprocess.run(
            ["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={self.project}"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
        if volumes:
            if self.dc("exec", "-T", "workspace", "pgrep", "-f", "claude").returncode == 0:
                sys.exit(f"An agent is still running in {self.project}; stop that run first.")
            # Left over from an interrupted attempt of this run; a new run starts empty and a resume restores.
            print(f"{self.name}: removing containers and volumes left over from an interrupted run")
            self.dc("down", "-v")
        (self.dir / "traces").mkdir(parents=True, exist_ok=True)
        (self.dir / "dumps").mkdir(exist_ok=True)
        # A batch builds the agent image once up front; parallel builds of the same image are slow.
        build = [] if os.environ.get("HARNESS_IMAGE_BUILT") else ["--build"]
        r = self.dc("up", "-d", *build, "--wait")
        if r.returncode:
            sys.exit(r.stderr)
        wanted, got = os.environ.get("CLAUDE_CODE_VERSION") or "latest", self.sh("claude --version")
        if wanted != "latest" and not got.startswith(wanted + " "):
            self.dc("down", "-v")
            sys.exit(f"{self.name}: the agent image has Claude Code {got!r}, not {wanted}")
        # Docker creates the missing working directory as root on the fresh volume.
        self.dc("exec", "-T", "-u", "root", "workspace", "chown", "dev:dev", ".")

    def setup(self) -> None:
        self.start_containers()
        self.sh("git init -q && printf 'node_modules/\\n.venv/\\n__pycache__/\\ndist/\\nbuild/\\n' > .gitignore")
        self.prev = self.commit("Initial commit")
        meta = {"scenario": self.scenario, "arm": self.arm, "repetition": self.rep, "mode": self.mode,
                "model": self.model, "max_turns": self.max_turns,
                "claude_code_version": self.sh("claude --version"), "base_url": self.base_url,
                "started_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def resume(self, fix_rounds: int) -> tuple[int, int]:
        """Rebuild the live app from the run's saved code and last database dump; return the (step, attempt) to run next."""
        rows = read_rows(self.dir)
        recorded = json.loads((self.dir / "meta.json").read_text(encoding="utf-8")).get("model")
        if recorded != self.model:
            sys.exit(f"{self.name}: recorded with {recorded}, not {self.model}; use another RUNS_DIR for another model")
        done = list(dict.fromkeys(r["phase"] for r in rows))
        names = [ph["name"] for ph in self.phases]
        if done != names[:len(done)]:
            sys.exit(f"{self.name}: recorded phases {done} are not the start of the scenario's phases {names}")
        point = resume_point(rows, self.phases, fix_rounds)
        if point is None:
            sys.exit(f"{self.name}: all {len(names)} phases already done; nothing to resume")
        step, attempt = point
        upcoming = names[step - 1] + (f" fix {attempt}" if attempt else "")
        last = rows[-1]
        self.last_row = last
        restored = self.restore(self.dir, last)
        self.seeded_phases = set(done) | {p for ph in self.phases if ph["name"] in done for p in ph.get("seed_more", [])}
        meta = json.loads((self.dir / "meta.json").read_text(encoding="utf-8"))
        meta.setdefault("resumed", []).append({"at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                               "from": upcoming, "model": self.model,
                                               "claude_code_version": self.sh("claude --version")})
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"[{self.name}] resumed after {last['trace']} ({restored} records restored); "
              f"next: {upcoming}", flush=True)
        return step, attempt

    def restore(self, source: Path, last: dict[str, Any]) -> int:
        """Start containers with the live app as it was after `last`, an attempt recorded in the run folder `source`:
        its code (from repo.bundle), its database (from the dump) and its seed data. Returns the records restored."""
        dump = source / "dumps" / f"after_{last['trace']}.{'sql' if self.arm == 'postgres' else 'archive'}"
        if not (source / "repo.bundle").exists() or not dump.exists():
            sys.exit(f"{self.name}: repo.bundle or {dump.name} missing in {source}; can't restore")
        self.start_containers()

        def fail(message: str) -> None:
            self.dc("down", "-v")  # nothing in the fresh containers is worth keeping
            sys.exit(f"{self.name}: {message}")

        # The code exactly as deployed after the last attempt, with its git history but no trace of the bundle.
        self.dc("cp", str(source / "repo.bundle"), "workspace:/tmp/repo.bundle")
        self.dc("exec", "-T", "-u", "root", "workspace", "chown", "dev:dev", "/tmp/repo.bundle")
        self.sh(f"git clone -q /tmp/repo.bundle . && git reset -q --hard {last['commit']} "
                "&& git remote remove origin && rm /tmp/repo.bundle")
        if self.sh("git rev-parse HEAD") != last["commit"]:
            fail(f"could not restore commit {last['commit']}")
        if (source / EXTRA).exists():
            self.dc("cp", str(source / EXTRA), "workspace:/tmp/extra.tgz")
            self.dc("exec", "-T", "-u", "root", "workspace", "chown", "dev:dev", "/tmp/extra.tgz")
            self.sh("tar -xzf /tmp/extra.tgz && rm /tmp/extra.tgz")
        # Dependencies are not in git; the agent had them installed (including dev ones) before.
        self.sh("if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; "
                "elif [ -f package.json ]; then npm install --no-audit --no-fund; fi > /tmp/restore.log 2>&1")
        data = dump.read_bytes()
        r = (self.dc_input(data, "exec", "-T", "postgres", "psql", "-U", "app", "-d", self.db, "-q",
                           "-v", "ON_ERROR_STOP=1") if self.arm == "postgres"
             else self.dc_input(data, "exec", "-T", "mongo", "mongorestore", "--archive", "--drop", "--quiet"))
        if r.returncode:
            fail(f"database restore failed: {r.stderr.decode(errors='replace')[-500:]}")
        restored, recorded = self.db_snapshot("resume")["records"], last["db"]["records"]
        if restored != recorded:
            fail(f"restored {restored} records, but {recorded} were recorded after {last['trace']}")
        self.seed_data = json.loads((source / "seed.json").read_text(encoding="utf-8"))
        self.prev = last["commit"]
        return restored

    def run_agent(self, phase: str, prompt: str, budget: int = 1) -> tuple[bool, float]:
        """One fresh agent session; budget multiplies the per-phase turn and time limits."""
        self.dc("restart", "workspace")
        # A fresh maintainer each phase: no transcripts or todos from earlier sessions.
        self.sh("rm -rf ~/.claude/projects ~/.claude/todos ~/.claude/shell-snapshots ~/.claude/CLAUDE.md")
        trace = self.dir / "traces" / f"{phase}.jsonl"
        started, timed_out = time.time(), False
        with open(trace, "w", encoding="utf-8") as out, \
                open(self.dir / "traces" / f"{phase}.stderr.txt", "w", encoding="utf-8") as err:
            try:
                # The prompt goes in on stdin: a whole merged spec is too long for a Windows command line.
                subprocess.run(
                    self.compose + ["exec", "-T", "workspace", "claude", "-p",
                                    "--output-format", "stream-json", "--verbose",
                                    "--model", self.model, "--dangerously-skip-permissions",
                                    "--max-turns", str(self.max_turns * budget)],
                    env=self.env, input=prompt.encode("utf-8"), stdout=out, stderr=err,
                    timeout=self.timeout_s * budget)
            except subprocess.TimeoutExpired:
                timed_out = True
        # Kills the agent if it timed out, and any servers it left running.
        self.dc("restart", "workspace")
        return timed_out, time.time() - started

    def start_app(self) -> bool:
        # Node coverage for the over-engineering metric; the preload is removed again before the next agent session.
        self.sh(f"rm -rf {coverage.COVERAGE_DIR} {coverage.FLUSH} && mkdir -p {coverage.COVERAGE_DIR}")
        self.dc_input(coverage.PRELOAD_JS.encode(), "exec", "-T", "workspace", "bash", "-c", f"cat > {coverage.PRELOAD}")
        self.dc("exec", "-d", "-e", f"NODE_V8_COVERAGE={coverage.COVERAGE_DIR}",
                "-e", f"NODE_OPTIONS=--require {coverage.PRELOAD}",
                "workspace", "bash", "-lc", "bash ./start.sh > /tmp/app.log 2>&1")
        deadline = time.time() + 600
        while time.time() < deadline:
            try:
                httpx.get(f"{self.base_url}/", timeout=3)
                return True
            except httpx.HTTPError:
                time.sleep(3)
        return False

    # ------------------------------------------------------------ measurements

    def numstat(self, a: str, b: str) -> dict[str, tuple[int, int]]:
        return parse_numstat(self.sh(f"git diff --numstat {a} {b} {PATHSPEC}"))

    def code_metrics(self, a: str, b: str) -> dict[str, Any]:
        changed, tree = self.numstat(a, b), self.numstat(EMPTY_TREE, b)
        return {"lines_added": sum(x for x, _ in changed.values()), "lines_removed": sum(y for _, y in changed.values()),
                "files_touched": len(changed), "files": list(changed),
                "total_lines": sum(x for x, _ in tree.values()), "total_files": len(tree),
                **size_metrics({p: x for p, (x, _) in tree.items()})}

    def stack(self) -> dict[str, Any]:
        out: dict[str, Any] = {"start_sh": self.sh("cat start.sh 2>/dev/null")[:1000]}
        manifests = self.sh(r"git ls-files | grep -E '(^|/)(package\.json|requirements\.txt|pyproject\.toml|go\.mod|Cargo\.toml)$'")
        for path in manifests.splitlines():
            text = self.sh(f"cat '{path}'")
            if path.endswith("package.json"):
                try:
                    pkg = json.loads(text)
                    out[path] = sorted({**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})})
                    continue
                except json.JSONDecodeError:
                    pass
            out[path] = text[:2000]
        return out

    def psql(self, sql: str) -> str:
        return self.dc("exec", "-T", "postgres", "psql", "-U", "app", "-d", self.db, "-At", "-c", sql).stdout

    def db_snapshot(self, phase: str) -> dict[str, Any]:
        if self.arm == "postgres":
            schema = self.dc("exec", "-T", "postgres", "pg_dump", "-U", "app", "-s", self.db).stdout
            (self.dir / f"db_{phase}.sql").write_text(schema, encoding="utf-8")
            rows = self.psql(
                "select c.table_schema, c.table_name, c.column_name, c.data_type "
                "from information_schema.columns c join information_schema.tables t "
                "on t.table_schema = c.table_schema and t.table_name = c.table_name "
                "where t.table_type = 'BASE TABLE' and c.table_schema not in ('pg_catalog', 'information_schema') "
                "order by 1, 2, c.ordinal_position")
            tables: dict[str, dict[str, Any]] = {}
            for line in rows.splitlines():
                schema_name, table, col, typ = line.split("|")
                name = table if schema_name == "public" else f"{schema_name}.{table}"
                entry = tables.setdefault(name, {"schema": schema_name, "table": table, "columns": {}})
                entry["columns"][col] = typ
            for entry in tables.values():
                ident = '"{}"."{}"'.format(entry.pop("schema").replace('"', '""'), entry.pop("table").replace('"', '""'))
                entry["rows"] = int(self.psql(f"select count(*) from {ident}").strip() or 0)
            return {
                "tables": tables,
                "jsonb_columns": sum(t in ("json", "jsonb") for e in tables.values() for t in e["columns"].values()),
                "records": sum(e["rows"] for n, e in tables.items() if not BOOKKEEPING.search(n)),
            }
        js = ("EJSON.stringify(db.getCollectionNames().sort().map(n => { const c = db.getCollection(n); "
              "const shapes = new Set(c.find().limit(2000).toArray().map(d => Object.keys(d).sort().join(','))); "
              "return {name: n, count: c.countDocuments(), shapes: shapes.size, "
              "indexes: c.getIndexes().map(i => i.key), sample: c.findOne()}; }))")
        out = self.dc("exec", "-T", "mongo", "mongosh", self.db, "--quiet", "--eval", js).stdout
        (self.dir / f"db_{phase}.json").write_text(out, encoding="utf-8")
        try:
            colls = json.loads(out[out.index("["):])
        except ValueError:
            colls = []
        for coll in colls:
            coll.pop("sample", None)
        return {"collections": colls,
                "records": sum(c["count"] for c in colls if not BOOKKEEPING.search(c["name"]))}

    def dump_db(self, label: str) -> None:
        if self.arm == "postgres":
            data = self.dc_bytes("exec", "-T", "postgres", "pg_dump", "-U", "app", self.db)
            (self.dir / "dumps" / f"{label}.sql").write_bytes(data)
        else:
            data = self.dc_bytes("exec", "-T", "mongo", "mongodump", "--db", self.db, "--archive", "--quiet")
            (self.dir / "dumps" / f"{label}.archive").write_bytes(data)
            # Text copy (one document per line) for the storage checks and for reading
            text = self.dc("exec", "-T", "mongo", "mongosh", self.db, "--quiet", "--eval",
                           "db.getCollectionNames().sort().forEach(n => db.getCollection(n).find()"
                           ".forEach(d => print(n + '\\t' + EJSON.stringify(d))))").stdout
            (self.dir / "dumps" / f"{label}.jsonl").write_text(text, encoding="utf-8")

    def export_repo(self) -> None:
        self.sh("git bundle create /tmp/repo.bundle --all")
        self.dc("cp", "workspace:/tmp/repo.bundle", str(self.dir / "repo.bundle"))
        # Files git ignores that the app may still need (e.g. a vendored package's dist/, matched by the
        # .gitignore's dist/); dependency folders are left out, a restore reinstalls those.
        extra = self.dir / EXTRA
        count = self.sh("git ls-files -z --others --ignored --exclude-standard --directory "
                        f"| grep -zv -E '{DEPENDENCY_DIRS}' > /tmp/extra.lst; "
                        "if [ -s /tmp/extra.lst ]; then tar --null -czf /tmp/extra.tgz -T /tmp/extra.lst "
                        "&& tar -tzf /tmp/extra.tgz | wc -l; else echo 0; fi")
        if count.isdigit() and int(count):
            self.dc("cp", "workspace:/tmp/extra.tgz", str(extra))
        elif extra.exists():
            extra.unlink()

    def collect_coverage(self, a: str, b: str) -> dict[str, Any] | None:
        """Which of the lines added between commits a and b ran while the checks ran (call right after them)."""
        self.sh(f"touch {coverage.FLUSH}")
        for _ in range(25):
            time.sleep(0.2)
            if self.sh(f"ls {coverage.COVERAGE_DIR} | grep -c '^flushed-' || true") not in ("", "0"):
                break
        time.sleep(0.5)  # other node processes flush at the same moment
        with tempfile.TemporaryDirectory() as tmp:
            self.dc("cp", f"workspace:{coverage.COVERAGE_DIR}", tmp)
            files = [json.loads(p.read_text(encoding="utf-8")) for p in Path(tmp).rglob("coverage-*.json")]
        added = coverage.added_lines(self.dc("exec", "-T", "workspace", "bash", "-c",
                                             f"git diff --no-color -U0 {a} {b} {PATHSPEC}").stdout)
        # Raw bytes: line endings must stay as they are for V8's offsets to line up.
        sources = {p: self.dc_bytes("exec", "-T", "workspace", "git", "show", f"{b}:{p}").decode("utf-8", "replace")
                   for p in added}
        root = f"/workspace/{self.sc.APP_DIR}"
        return coverage.summarize(added, coverage.executed_lines(files, root, sources), sources)

    # ------------------------------------------------------------ one phase

    def phase(self, ph: dict[str, Any], step: int, fix_rounds: int, start: int = 0,
              row: dict[str, Any] | None = None) -> None:
        """The phase's feature session and its fix rounds; start > 0 continues with that fix round after `row`."""
        prompts = ROOT / "scenarios" / self.scenario / "prompts"
        if start == 0:
            prompt = "\n\n".join((prompts / f).read_text(encoding="utf-8") for f in ph["prompt"])
            row = self.attempt(ph, step, 0, prompt.replace("{database}", ARMS[self.arm]["line"]))
        assert row is not None
        constraints = prompts / "constraints.md"
        for k in range(max(start, 1), fix_rounds + 1):
            if row["app_started"] and (ph.get("fix") == "startup" or row["checks_passed"] == row["checks_total"]):
                break
            report = self.bug_report(row)
            if constraints.exists():
                report += "\n\n" + constraints.read_text(encoding="utf-8")
            row = self.attempt(ph, step, k, report)

    def bug_report(self, row: dict[str, Any]) -> str:
        """A QA-style ticket for what failed, without revealing the checks behind it."""
        if not row["app_started"]:
            log = (self.dir / f"app_{row['trace']}.log").read_text(encoding="utf-8", errors="replace")
            tail = "\n".join(log.splitlines()[-40:])
            return ("# Bug report\n\nAfter the last deploy the app doesn't come up: `./start.sh` never serves the API on "
                    f"`PORT`. The last lines of its output:\n\n```\n{tail}\n```\n\nPlease fix it.")
        plain = lambda text: re.sub(r"\bseeded\b", "existing", text)
        failed = {k: r for k, r in row["checks"].items() if not r["pass"]}
        # Failures that only cascade from an earlier one ("fixture ... unavailable") usually clear with it.
        roots = {k: r for k, r in failed.items() if not r.get("detail", "").startswith("AssertionError: fixture")} or failed
        items = []
        for check, r in roots.items():
            title = plain(re.sub(r"^[a-z]+\d+\.", "", check).replace("_", " "))
            item = f"- **{title}**: {plain(re.sub(r'^AssertionError: ', '', r.get('detail', '')))}"
            if r.get("request"):
                item += f"\n  Request: `{r['request']}`"
            items.append(item)
        more = f"\n\n…and {len(items) - 20} more problems." if len(items) > 20 else ""
        return ("# Bug report\n\nAfter the last deploy, QA found these problems. Please fix them.\n\n"
                + "\n".join(items[:20]) + more)

    def seed_more(self, phase: str, row: dict[str, Any]) -> None:
        """Production keeps growing: data the scenario adds once, after the phase's first successful deploy."""
        if phase in self.seeded_phases or not hasattr(self.sc, "seed_more"):
            return
        self.seeded_phases.add(phase)
        added = self.sc.seed_more(phase, self.base_url, self.seed_data)
        if added:
            self.seed_data.update(added)
            (self.dir / "seed.json").write_text(json.dumps(self.seed_data, indent=1, ensure_ascii=False),
                                                encoding="utf-8")
            row.setdefault("seed", {}).update({key: len(v) for v_all in added.values() for key, v in v_all.items()
                                               if isinstance(v, (list, dict))})

    def attempt(self, ph: dict[str, Any], step: int, k: int, prompt: str) -> dict[str, Any]:
        """One agent session (the feature itself, or fix round k), then deploy, check and record."""
        name = ph["name"]
        label = name if k == 0 else f"{name}_fix{k}"
        timed_out, wall = self.run_agent(label, prompt, ph.get("budget", 1) if k == 0 else 1)
        sha = self.commit(ph["deploy"] if k == 0 else f"{ph['deploy']} hotfix {k}")
        row: dict[str, Any] = {"scenario": self.scenario, "arm": self.arm, "repetition": self.rep, "mode": self.mode,
                               "model": self.model, "phase": name, "kind": ph.get("kind", "feature"),
                               **({"probe": ph["probe"]} if ph.get("probe") else {}),
                               "attempt": k, "trace": label,
                               "commit": sha, "timed_out": timed_out, "wall_s": round(wall),
                               **trace_metrics(self.dir / "traces" / f"{label}.jsonl"),
                               **self.code_metrics(self.prev, sha)}

        row["app_started"] = self.start_app()
        results: dict[str, Any] = {}
        if row["app_started"]:
            seeded_now = step == 1 and self.seed_data is None
            if seeded_now:
                self.seed_data = self.sc.seed_data(self.base_url)
                (self.dir / "seed.json").write_text(json.dumps(self.seed_data, indent=1, ensure_ascii=False),
                                                    encoding="utf-8")
                row["seed"] = {key: len(v) for key, v in self.seed_data.items() if isinstance(v, (list, dict))}
            # A merged run's first step adds, right after the seed, what the phases add over time.
            if self.seed_data is not None and (self.mode == "merged" or not seeded_now):
                for phase in ph.get("seed_more", [name]):
                    self.seed_more(phase, row)
            results = self.sc.run_checks(ph.get("checks", name), self.base_url, self.seed_data)
            if ph.get("probe"):
                results.update(self.sc.probe_checks(ph["probe"], self.base_url, self.seed_data))
            row["coverage"] = self.collect_coverage(self.prev, sha)
        (self.dir / f"app_{label}.log").write_text(self.sh("tail -c 200000 /tmp/app.log"), encoding="utf-8")
        if row["app_started"]:
            for phase in ph.get("after_checks", [name]):
                results.update(self.sc.after_checks(self, phase))
        self.sh(f"rm -rf {coverage.COVERAGE_DIR} {coverage.PRELOAD} {coverage.FLUSH}")
        row["checks"] = results
        row["checks_passed"] = sum(r["pass"] for r in results.values())
        row["checks_total"] = len(results)
        self.dc("restart", "workspace")

        row["db"] = self.db_snapshot(label)
        row["used_forced_db"] = row["db"]["records"] > 0
        row["stack"] = self.stack()
        self.dump_db(f"after_{label}")
        self.export_repo()
        with open(self.dir / "metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.prev = sha
        print(f"[{self.name}] {step}/{len(self.phases)} {name}{f' fix {k}' if k else ''}: status={row['status']} "
              f"cost=${round(row['cost_usd'] or 0, 2)} turns={row['turns']} files={row['files_touched']} "
              f"checks={row['checks_passed']}/{row['checks_total']}", flush=True)
        return row


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", required=True, choices=SCENARIOS, help="app the agent builds (scenarios/<name>)")
    p.add_argument("--db", required=True, choices=list(ARMS), help="database the agent is given")
    p.add_argument("--repetition", required=True, type=int,
                   help="which repeat of this setup (1-99); separates runs with identical settings")
    p.add_argument("--model", default=os.environ.get("CLAUDE_MODEL"),
                   help="pinned model id passed to claude --model (default: CLAUDE_MODEL from .env)")
    p.add_argument("--max-turns", type=int, default=200,
                   help="per-phase turn budget (a merged run's build session gets this times the number of phases)")
    p.add_argument("--timeout-min", type=int, default=180,
                   help="per-phase wall-clock limit (a merged run's build session: this times the number of phases)")
    p.add_argument("--mode", choices=["incremental", "merged"], default="incremental",
                   help="incremental: one change request per phase (default); merged: the whole spec at once, "
                        "then the data changes, then every check (scenario.toml [[merged]]; results in <db>_merged_rep<N>)")
    p.add_argument("--fix-rounds", type=int, default=2,
                   help="after a phase with failing checks, give a fresh agent a bug report up to this many times (0 = off)")
    p.add_argument("--keep", action="store_true",
                   help="keep the containers and volumes after the run (stopped) to inspect the live database")
    p.add_argument("--resume", action="store_true",
                   help="continue an existing run with the phases it hasn't done yet (e.g. after phases were added), "
                        "restoring its code and database from the last attempt")
    p.add_argument("--overwrite", action="store_true", help="delete the run's existing results and start again")
    a = p.parse_args()
    if not 1 <= a.repetition <= 99:
        sys.exit("--repetition must be between 1 and 99")
    if a.mode == "merged" and a.repetition > 49:
        sys.exit("merged runs take --repetition 1-49 (they use the upper half of the port range)")
    if a.mode == "merged" and not load_scenario(a.scenario).MERGED:
        sys.exit(f"scenario {a.scenario} has no [[merged]] steps in its scenario.toml")
    if not (os.environ.get("ANTHROPIC_FOUNDRY_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        sys.exit("No API key: set AI_GATEWAY_API_KEY (AI gateway), ANTHROPIC_FOUNDRY_API_KEY (Grove) "
                 "or ANTHROPIC_API_KEY in .env")
    if not a.model:
        sys.exit("No model: set CLAUDE_MODEL in .env or pass --model")

    run = Run(a.scenario, a.db, a.repetition, a.model, a.max_turns, a.timeout_min * 60, a.mode)
    if a.resume and a.overwrite:
        sys.exit("use either --resume or --overwrite, not both")
    done = phases_done(run.dir)
    if a.resume and not done:
        sys.exit(f"{run.dir} has no results to resume")
    if done and not (a.resume or a.overwrite):
        status = f"{run.dir} already has results ({len(done)}/{len(run.phases)} phases done)"
        if not sys.stdin.isatty():
            sys.exit(f"{status}; pass --resume or --overwrite")
        choice = input(f"{status}. [r]esume, [o]verwrite, or [c]ancel? ").strip().lower()[:1]
        if choice not in ("r", "o"):
            sys.exit("Nothing started.")
        a.resume, a.overwrite = choice == "r", choice == "o"
    if a.overwrite and done:
        clear_results(run.dir)
    first, first_attempt = run.resume(a.fix_rounds) if a.resume else (run.setup() or 1, 0)
    try:
        for step, ph in enumerate(run.phases, start=1):
            if step == first and first_attempt:
                run.phase(ph, step, a.fix_rounds, start=first_attempt, row=run.last_row)
            elif step >= first:
                run.phase(ph, step, a.fix_rounds)
    except KeyboardInterrupt:
        # Stopped on purpose: every finished attempt is saved, and --resume restores from that.
        run.dc("down", "-v")
        print(f"[{run.name}] stopped; containers removed (continue with --resume)", flush=True)
        raise
    except BaseException:
        run.dc("stop")
        print(f"[{run.name}] run failed; containers and volumes kept for debugging. Remove them with: "
              f"docker compose -p {run.project} down -v", flush=True)
        raise
    if a.keep:
        run.dc("stop")
        print(f"[{run.name}] containers and volumes kept (--keep). Remove them with: "
              f"docker compose -p {run.project} down -v")
    else:
        # Everything worth keeping (dumps, schemas, code bundle, traces) is already in the run folder.
        run.dc("down", "-v")
    print(f"[{run.name}] done: {run.dir}")


if __name__ == "__main__":
    main()
