"""Parse agent transcripts: classify tool calls by intent and compute per-phase effort metrics.

Claude Code may do everything through Bash (reading with cat/grep, writing with `cat > file <<EOF`),
so Bash commands are classified by what they do, not by the tool name.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

CATEGORIES = ("explore", "edit", "exec", "install", "db")

_HEREDOC = re.compile(r"(<<-?\s*['\"]?(\w+)['\"]?)([^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", re.S)
_REDIRECT = re.compile(r"(?<![<>&0-9])>>?\s*([^\s&|;<>()]+)")
_DB_TOOL = re.compile(r"\b(psql|mongosh|pg_dump|pg_restore|mongodump|mongorestore)\b")
_DB_VAR = re.compile(r"\bDB_URL\b|\bTEST_DB_URL\b")
# Inline scripts (heredocs, node -e / python -c) that write files are edits, e.g. python3 - <<'EOF' ... write_text(...)
_SCRIPT_WRITE = re.compile(r"write_text\(|writeFileSync|writeFile\(|appendFileSync|open\([^)]*['\"][wa]\+?['\"]")
_NEUTRAL = {"cd", "export", "set", "for", "do", "done", "then", "else", "fi", "if", "while", "true", "false",
            "echo", "printf", "{", "}", "(", ")", "[", "test", "local", "return", "exit"}
_EXPLORE = {"cat", "ls", "grep", "egrep", "rg", "find", "head", "tail", "wc", "tree", "less", "more", "file",
            "stat", "which", "pwd", "awk", "diff", "jq", "env", "printenv", "du", "sort", "uniq", "cut", "tr",
            "readlink", "realpath", "whoami", "id", "uname", "sed", "perl"}
_EDIT = {"mkdir", "mv", "cp", "rm", "touch", "chmod", "patch", "ln", "tee"}
_RUN = {"node", "npx", "python", "python3", "go", "curl", "wget", "bash", "sh", "kill", "pkill", "killall",
        "sleep", "ps", "jest", "pytest", "make", "timeout", "nohup", "tsc", "ts-node", "tsx", "deno", "bun"}
_INSTALL_ARGS = {"i", "install", "ci", "add"}


def strip_heredocs(cmd: str) -> str:
    return _HEREDOC.sub(r"\1\3", cmd)


def heredoc_lines(cmd: str) -> int:
    return sum(m.group(0).count("\n") - 1 for m in _HEREDOC.finditer(cmd))


def _segment_categories(seg: str) -> set[str]:
    words = seg.split()
    while words and (re.fullmatch(r"\w+=\S*", words[0]) or words[0] in ("sudo", "time", "exec", "(", "{")):
        words = words[1:]
    if not words:
        return set()
    cats: set[str] = set()
    for target in _REDIRECT.findall(seg):
        if target != "/dev/null":
            cats.add("exec" if target.startswith("/tmp") else "edit")
    first = words[0].lstrip("(").split("/")[-1]
    rest = words[1:]
    if first in ("npm", "pnpm", "yarn", "pip", "pip3", "uv", "cargo", "gem", "apt", "apt-get") \
            and rest and rest[0] in _INSTALL_ARGS | {"sync"}:
        cats.add("install")
    elif first in ("npm", "pnpm", "yarn"):
        cats.add("exec")
    elif first == "go" and rest and rest[0] in ("get", "mod"):
        cats.add("install")
    elif first == "git":
        sub = rest[0] if rest else ""
        cats.add("edit" if sub in ("apply", "checkout", "restore", "mv", "rm") else "explore")
    elif first in ("sed", "perl") and any(w.startswith("-i") or w == "-pi" for w in rest):
        cats.add("edit")
    elif first in _EDIT:
        cats.add("edit")
    elif first in _RUN or first.endswith(".sh") or first.startswith("./"):
        cats.add("exec")
    elif first in _EXPLORE and "edit" not in cats:
        cats.add("explore")
    elif first not in _NEUTRAL and not cats:
        cats.add("exec")
    return cats


def classify_bash(cmd: str) -> set[str]:
    cats: set[str] = set()
    stripped = strip_heredocs(cmd)
    for seg in re.split(r"\n|&&|\|\||;|\|", _QUOTED.sub("''", stripped)):
        cats |= _segment_categories(seg.strip())
    scripts = "\n".join(m.group(0) for m in _HEREDOC.finditer(cmd)) + "\n".join(_QUOTED.findall(stripped))
    if "exec" in cats and _SCRIPT_WRITE.search(scripts):
        cats.add("edit")
    if _DB_TOOL.search(cmd) or ("exec" in cats and _DB_VAR.search(cmd)):
        cats.add("db")
    return cats


def classify_call(name: str, inp: dict[str, Any]) -> set[str]:
    if name == "Bash":
        return classify_bash(inp.get("command", "")) or {"exec"}
    if name in ("Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch"):
        return {"explore"}
    if name in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return {"edit"}
    return set()


def load_events(path: Path) -> list[dict[str, Any]]:
    events = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw.strip().startswith("{"):
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
    return events


def tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b for e in events if e.get("type") == "assistant"
            for b in (e.get("message") or {}).get("content") or []
            if isinstance(b, dict) and b.get("type") == "tool_use"]


def trace_metrics(path: Path) -> dict[str, Any]:
    events = load_events(path)
    result = next((e for e in reversed(events) if e.get("type") == "result"), None) or {}
    calls = tool_calls(events)
    tools = Counter(c.get("name") for c in calls)
    intents: Counter[str] = Counter()
    for c in calls:
        intents.update(classify_call(c.get("name", ""), c.get("input") or {}))
    tool_errors = sum(1 for e in events if e.get("type") == "user"
                      for b in (e.get("message") or {}).get("content") or []
                      if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"))
    usage = result.get("usage") or {}
    tokens = {
        "input": usage.get("input_tokens") or 0,
        "cache_write": usage.get("cache_creation_input_tokens") or 0,
        "cache_read": usage.get("cache_read_input_tokens") or 0,
        "output": usage.get("output_tokens") or 0,
    }
    return {
        "status": result.get("subtype", "no_result"),
        "cost_usd": result.get("total_cost_usd"),
        "turns": result.get("num_turns"),
        "agent_duration_s": round((result.get("duration_ms") or 0) / 1000),
        "tokens": tokens,
        "tokens_total": sum(tokens.values()),
        "tool_calls": dict(tools),
        "bash_calls": tools["Bash"],
        "tool_errors": tool_errors,
        **{f"{cat}_calls": intents[cat] for cat in CATEGORIES},
    }
