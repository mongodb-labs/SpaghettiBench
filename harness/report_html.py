"""The report as one self-contained HTML page: the same tables as the console, with the --details parts in
collapsed sections, sortable columns, pass rates coloured and the verification per run."""
from __future__ import annotations

import datetime as dt
import html
import re
from pathlib import Path
from typing import Any

from harness import code_quality

# Columns holding a share from 0 to 1 where higher is better; coloured from red to green.
SHARE_COLS = re.compile(r"^(pass_rate|final_pass|end_pass)$|^(mongodb|postgres) ")
ARMS = {"mongodb": "#13aa52", "postgres": "#336791"}
HELP = {
    "total_cost": "the change request plus its fix rounds, list-price USD",
    "fix_cost": "the fix rounds only",
    "pass_rate": "share of checks passed on the first try",
    "final_pass": "share of checks passed after the fix rounds",
    "end_pass": "the full check suite after the run's last session",
    "unexecuted": "share of the added code lines that no check executed",
    "largest_file": "lines of the largest file",
    "compactions": "times the agent's context filled up and was summarised",
    "favours": "the database this phase is expected to favour",
    "stage": "the phase's position in the scenario, as numbered in its prompts (0 = the initial build)",
    "n": "number of runs",
    "regressions": "earlier checks failing after the probe's first try",
    "final_regressions": "earlier checks still failing after the probe's fix rounds",
    **code_quality.COLUMNS,
}
CSS = """
body { font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; margin: 0; color: #1d2328; background: #f6f7f9; }
header { background: #1d2328; color: #fff; padding: 14px 28px; }
header h1 { margin: 0; font-size: 18px; }
header p { margin: 4px 0 0; color: #aab4be; font-size: 12px; }
nav { padding: 10px 28px; background: #fff; border-bottom: 1px solid #e2e6ea; position: sticky; top: 0; z-index: 2; }
nav a { margin-right: 6px; color: #336791; text-decoration: none; font-weight: 600; padding: 5px 14px;
        border-radius: 6px; }
nav a.active { background: #336791; color: #fff; }
nav a:hover:not(.active) { background: #eef2f6; }
main { padding: 8px 28px 40px; }
h2 { margin: 32px 0 4px; font-size: 20px; border-bottom: 2px solid #1d2328; padding-bottom: 4px; }
h3 { margin: 22px 0 2px; font-size: 15px; }
.note { color: #5b6670; font-size: 12px; margin: 0 0 8px; }
.wrap { overflow-x: auto; background: #fff; border: 1px solid #e2e6ea; border-radius: 6px; max-height: 70vh; }
table { border-collapse: collapse; font-size: 12.5px; white-space: nowrap; }
th, td { padding: 4px 10px; border-bottom: 1px solid #eef0f2; text-align: left; }
th { background: #f0f2f4; position: sticky; top: 0; cursor: pointer; user-select: none; font-weight: 600; }
th[title] { text-decoration: underline dotted #9aa5b0; }
tr:hover td { background: #f7fafc; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.spread { color: #8a959f; font-size: 11px; margin-left: 3px; }
.arm { display: inline-block; padding: 0 7px; border-radius: 9px; color: #fff; font-size: 11.5px; font-weight: 600; }
details { margin: 18px 0 0; }
details > summary { cursor: pointer; font-weight: 600; font-size: 15px; }
details h3 { margin-top: 10px; }
.runs { display: grid; grid-template-columns: repeat(auto-fill, minmax(560px, 1fr)); gap: 10px; margin-top: 8px; }
.run { background: #fff; border: 1px solid #e2e6ea; border-radius: 6px; padding: 8px 12px; }
.run h4 { margin: 0 0 4px; font-size: 13px; }
.run pre { margin: 0; font-size: 11.5px; white-space: pre-wrap; }
.OK { color: #1e7d3c; } .WARN { color: #b06a00; font-weight: 600; } .FAIL { color: #c0262d; font-weight: 600; }
.info { color: #5b6670; } .flag { border-left: 4px solid #c0262d; }
.ok-badge { color: #1e7d3c; font-weight: 600; } .bad-badge { color: #c0262d; font-weight: 600; }
"""
SORT_JS = """
document.querySelectorAll('th').forEach(th => th.addEventListener('click', () => {
  const table = th.closest('table'), i = [...th.parentNode.children].indexOf(th);
  const asc = th.dataset.asc !== '1'; th.parentNode.querySelectorAll('th').forEach(h => delete h.dataset.asc);
  th.dataset.asc = asc ? '1' : '0';
  const key = td => { const t = td.dataset.v ?? td.textContent.trim(); const n = parseFloat(t); return isNaN(n) ? t : n; };
  const rows = [...table.tBodies[0].rows];
  rows.sort((a, b) => { const x = key(a.cells[i]), y = key(b.cells[i]);
    return (typeof x === typeof y ? (x > y) - (x < y) : (typeof x === 'number' ? -1 : 1)) * (asc ? 1 : -1); });
  rows.forEach(r => table.tBodies[0].appendChild(r));
}));
"""
# One scenario at a time: the tab in the URL (#shop), else the first one.
TABS_JS = """
function showTab() {
  const sections = [...document.querySelectorAll('section.scenario')];
  const want = location.hash.slice(1), current = sections.find(s => s.id === want) || sections[0];
  sections.forEach(s => s.hidden = s !== current);
  document.querySelectorAll('nav a').forEach(a => a.classList.toggle('active', current && a.hash === '#' + current.id));
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', showTab); showTab();
"""


def share_colour(value: float) -> str:
    """0 -> red, 1 -> green, as a light background."""
    value = max(0.0, min(1.0, value))
    return f"hsl({round(120 * value ** 3)}, 70%, {round(92 - 8 * (1 - value))}%)"


def cell(col: str, value: Any) -> str:
    if value is None or value == "":
        return "<td></td>"
    text = str(value)
    if col == "arm" and text in ARMS:
        return f'<td data-v="{text}"><span class="arm" style="background:{ARMS[text]}">{text}</span></td>'
    if col == "favours" and text in ARMS:
        return f'<td style="color:{ARMS[text]};font-weight:600">{text}</td>'
    m = re.fullmatch(r"(-?[\d.]+)(?: ±([\d.]+))?", text)
    if not m:
        return f"<td>{html.escape(text)}</td>"
    number = float(m.group(1))
    style = f' style="background:{share_colour(number)}"' if SHARE_COLS.search(col) and number <= 1 else ""
    spread = f'<span class="spread">±{m.group(2)}</span>' if m.group(2) else ""
    return f'<td class="num" data-v="{number}"{style}>{m.group(1)}{spread}</td>'


class Html:
    """Collects the report and writes it as one HTML file."""

    details = True

    def __init__(self, runs: Path):
        self.runs = runs
        self.parts: list[str] = []
        self.scenarios: list[str] = []
        self.open_detail = False

    def _close_detail(self) -> None:
        if self.open_detail:
            self.parts.append("</details>")
            self.open_detail = False

    def scenario(self, name: str, runs: int) -> None:
        self._close_detail()
        if self.scenarios:
            self.parts.append("</section>")
        self.scenarios.append(name)
        self.parts.append(f'<section class="scenario" id="{name}">'
                          f'<h2>{html.escape(name)} <span class="note">{runs} runs</span></h2>')

    def heading(self, tag: str, title: str, note: str = "", detail: bool = False) -> None:
        self._close_detail()
        if detail:
            self.parts.append(f"<details><summary>{html.escape(title.capitalize())}</summary>")
            self.open_detail = True
        else:
            self.parts.append(f"<h3>{html.escape(title.capitalize())}</h3>")
        if note:
            self.parts.append(f'<p class="note">{html.escape(note)}</p>')

    def table(self, rows: list[dict[str, Any]], cols: list[str], detail: bool = False) -> None:
        head = "".join(f'<th title="{html.escape(HELP[c])}">{c}</th>' if c in HELP else f"<th>{c}</th>" for c in cols)
        body = "".join("<tr>" + "".join(cell(c, r.get(c)) for c in cols) + "</tr>" for r in rows)
        self.parts.append(f'<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
        self._close_detail()

    def note(self, text: str) -> None:
        self.parts.append(f'<p class="note">{html.escape(text)}</p>')

    def verification(self, tag: str, checked: dict[str, list[str]], flagged: list[str]) -> None:
        self._close_detail()
        badge = (f'<span class="bad-badge">{len(flagged)}/{len(checked)} runs need a look</span>' if flagged
                 else f'<span class="ok-badge">all {len(checked)} runs OK</span>')
        self.parts.append(f"<details><summary>Verification: {badge}</summary><div class='runs'>")
        for run, lines in checked.items():
            text = "\n".join(re.sub(r" (OK  |WARN|FAIL|info) ", lambda m: f' <span class="{m.group(1).strip()}">'
                                    f'{m.group(1).strip()}</span> ', html.escape(l)) for l in lines)
            flag = " flag" if run in flagged else ""
            self.parts.append(f'<div class="run{flag}"><h4>{html.escape(run)}</h4><pre>{text}</pre></div>')
        self.parts.append("</div></details>")

    def write(self) -> Path:
        self._close_detail()
        if self.scenarios:
            self.parts.append("</section>")
        path = self.runs / "report.html"
        nav = "".join(f'<a href="#{s}">{s}</a>' for s in self.scenarios)
        when = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        page = (f'<!doctype html><html><head><meta charset="utf-8"><title>Report: {self.runs.name}</title>'
                f"<style>{CSS}</style></head><body><header><h1>Results: {html.escape(self.runs.name)}</h1>"
                f"<p>Generated {when} by harness.report. Click a column to sort; hover underlined headers for "
                f"their meaning. ± is half the range across runs.</p></header><nav>{nav}</nav><main>"
                + "\n".join(self.parts) + f"</main><script>{SORT_JS}{TABS_JS}</script></body></html>")
        path.write_text(page, encoding="utf-8")
        return path
