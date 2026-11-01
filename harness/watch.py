"""Follow a run's agent conversation live, in readable form.

Usage: uv run -m harness.watch SCENARIO/RUN [--phase PHASE] [--results N] [--no-color]
Follows the newest phase trace and switches automatically when the next phase starts. Ctrl+C to stop.

Each tool call is labelled by intent: explore (reading code), edit (changing files), exec (running
code, tests, servers), install (dependencies), db (touching a database). File contents written by the
agent are summarised as a line count instead of printed.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

from colorama import Fore, Style, just_fix_windows_console

from harness.run import RUNS
from harness.traces import classify_call, heredoc_lines, strip_heredocs

PRIORITY = ("db", "edit", "exec", "install", "explore")
COLOR = {"db": Fore.MAGENTA + Style.BRIGHT, "edit": Fore.YELLOW + Style.BRIGHT, "exec": Fore.GREEN,
         "install": Fore.CYAN, "explore": Fore.BLUE + Style.BRIGHT, "": Fore.WHITE}
PAD = " " * 16


class Printer:
    def __init__(self, width: int, result_lines: int, color: bool):
        self.width, self.result_lines, self.color = width, result_lines, color

    def c(self, style: str, text: str) -> str:
        return f"{style}{text}{Style.RESET_ALL}" if self.color else text

    def cut(self, text: str, indent: int = 0) -> str:
        room = max(20, self.width - indent)
        return text if len(text) <= room else text[:room - 3] + "..."

    def call(self, name: str, inp: dict[str, Any]) -> str:
        cats = classify_call(name, inp)
        # "explore" alongside other intents is usually a pipe like `npm test | grep`; leave it out of the label
        shown = [p for p in PRIORITY if p in cats and (p != "explore" or cats == {"explore"})]
        label = shown[0] if shown else ""
        if name == "Bash":
            cmd = inp.get("command", "")
            lines = strip_heredocs(cmd).strip().splitlines() or [""]
            text = re.sub(r"^cd /workspace/[^\s;&]+\s*(;|&&)\s*", "", lines[0].strip())
            if len(lines) > 1:
                text += f"   (+{len(lines) - 1} more lines)"
            if heredoc_lines(cmd):
                text += f"   [writes {heredoc_lines(cmd)} lines of file content]"
            if inp.get("description"):
                text = f"{inp['description']}: {text}"
        elif name == "Write":
            text = f"write {inp.get('file_path')} ({len(str(inp.get('content', '')).splitlines())} lines)"
        elif name in ("Edit", "MultiEdit"):
            text = f"edit {inp.get('file_path')}"
        elif name == "Read":
            text = f"read {inp.get('file_path')}"
        else:
            text = f"{name} {json.dumps(inp, ensure_ascii=False)}"
        tag = f"[{'+'.join(shown) or name.lower()}]".ljust(13)
        return "  " + self.c(COLOR[label], tag) + " " + self.cut(text, 16)

    def result(self, block: dict[str, Any]) -> list[str]:
        content = block.get("content")
        if isinstance(content, list):
            content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        lines = [l.rstrip() for l in str(content or "").splitlines() if l.strip()]
        if block.get("is_error"):
            return [PAD + self.c(Fore.RED + Style.BRIGHT, self.cut("ERROR " + (lines[0] if lines else ""), 16))] + \
                   [PAD + self.c(Fore.RED, self.cut(l, 16)) for l in lines[1:self.result_lines]]
        if self.result_lines == 0:
            return []
        shown = [PAD + self.c(Style.DIM, self.cut(l, 16)) for l in lines[:self.result_lines]]
        if len(lines) > self.result_lines:
            shown.append(PAD + self.c(Style.DIM, f"... {len(lines) - self.result_lines} more lines"))
        return shown or [PAD + self.c(Style.DIM, "(no output)")]

    def event(self, event: dict[str, Any]) -> list[str]:
        kind, sub = event.get("type"), event.get("subtype")
        if kind == "system" and sub == "init":
            return [self.c(Style.DIM, f"session started: model {event.get('model')}, "
                                      f"Claude Code {event.get('claude_code_version')}")]
        if kind == "system" and sub == "api_retry":
            return [self.c(Fore.RED + Style.BRIGHT, f"API retry {event.get('attempt')}/{event.get('max_retries')}: "
                                                    f"{event.get('error_status')} {event.get('error')}")]
        if kind == "result":
            style = Fore.GREEN + Style.BRIGHT if sub == "success" else Fore.RED + Style.BRIGHT
            return ["", self.c(style, f"finished: {sub}, {event.get('num_turns')} turns, "
                                      f"${round(event.get('total_cost_usd') or 0, 2)}, "
                                      f"{round((event.get('duration_ms') or 0) / 60000, 1)} min")]
        out = []
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if kind == "assistant" and btype == "text" and block.get("text", "").strip():
                wrapped = textwrap.wrap(" ".join(block["text"].split()), max(20, self.width - 7))
                out += ["", self.c(Fore.CYAN + Style.BRIGHT, "agent  ") + self.c(Style.BRIGHT, wrapped[0])]
                out += ["       " + self.c(Style.BRIGHT, l) for l in wrapped[1:]]
            elif kind == "assistant" and btype == "thinking" and block.get("thinking", "").strip():
                out.append(self.c(Style.DIM, "thinking  " + self.cut(" ".join(block["thinking"].split()), 10)))
            elif kind == "assistant" and btype == "tool_use":
                out.append(self.call(block.get("name", "?"), block.get("input") or {}))
            elif kind == "user" and btype == "tool_result":
                out += self.result(block)
        return out


def newest_trace(traces: Path) -> Path | None:
    files = sorted(traces.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run", help="run folder under runs/, e.g. inspections/postgres_rep1")
    p.add_argument("--phase", help="follow only this phase (e.g. initial, repeating_groups)")
    p.add_argument("--results", type=int, default=2, help="lines of each tool result to show (0 = none)")
    p.add_argument("--width", type=int, default=shutil.get_terminal_size().columns - 1)
    p.add_argument("--no-color", action="store_true")
    a = p.parse_args()
    sys.stdout.reconfigure(errors="replace")
    just_fix_windows_console()
    out = Printer(a.width, a.results, not a.no_color)

    traces = RUNS / a.run / "traces"
    current, handle, buffer = None, None, ""
    try:
        while True:
            target = traces / f"{a.phase}.jsonl" if a.phase else newest_trace(traces) if traces.is_dir() else None
            if target and target.exists() and target != current:
                if handle:
                    handle.close()
                current, handle, buffer = target, open(target, encoding="utf-8", errors="replace"), ""
                print(out.c(Style.BRIGHT, f"\n===== {a.run} / {target.stem} " + "=" * 20), flush=True)
            chunk = handle.read() if handle else ""
            if not chunk:
                time.sleep(1)
                continue
            buffer += chunk
            *lines, buffer = buffer.split("\n")
            for line in lines:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for text in out.event(event):
                    print(text, flush=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
