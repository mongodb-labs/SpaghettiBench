# MongoDB vs. Postgres for coding agents

Two questions: (1) what does it cost a coding agent to maintain an app with live data as its
requirements evolve, and how does that differ between MongoDB and Postgres; (2) where do LLMs handle
MongoDB poorly, and which metrics surface it. What we have learned so far, and what's next:
[docs/findings.md](docs/findings.md).

A coding agent (Claude Code, headless) builds a small backend from a brief. The app goes "into
production" with seeded data, then the agent gets a series of change requests against the live app.
Each run forces one database (Postgres or MongoDB); everything else is identical. Per phase we record
the cost and effort, the code it leaves behind, and whether hidden checks pass, including that existing
data survived. The full transcripts are kept, to find out where the effort went.

| Scenario | App | Stages |
|---|---|---|
| `inspections` | Form builder for field inspections | 27 |
| `crm` | CRM for small agencies | 18 |
| `shop` | Online shops: products, stock, orders | 18 |

Each scenario's phases, with the database each is expected to favour and why, are in its
`scenarios/<name>/scenario.toml`; its README describes the seed data and the checks.

## Running

Needs Docker Desktop, [uv](https://docs.astral.sh/uv/) and an AI gateway (or Grove, or Anthropic) API key.

```bat
uv sync
copy .env.example .env
:: put your key in .env (AI_GATEWAY_API_KEY; it takes precedence over the Grove/Foundry settings, whose
:: gateway cuts requests off after about 4 minutes); CLAUDE_MODEL must be the same for runs you compare
:: another model: set CLAUDE_MODEL and RUNS_DIR (e.g. runs_sonnet) in .env; every command then uses that folder

:: several runs in parallel; lists them and asks before starting, then builds the agent image once
uv run -m harness.batch --scenario inspections --db postgres mongodb --repetition 1-3 --parallel 6 --fix-rounds 2
:: one run
uv run -m harness.run --scenario inspections --db postgres --repetition 1 --fix-rounds 2

uv run -m harness.watch inspections/postgres_rep1   :: follow the agent live
uv run -m harness.report                            :: results (--details for every run and failing check;
                                                    :: always also writes <runs>/report.html with everything, --open opens it)
uv run -m harness.audit                             :: flagged agent commands (looking outside the repo, destructive, live DB)
```

| Option | Meaning |
|---|---|
| `--repetition` | Run number (1–99; batch: `1-3` or `4,5`); also sets the app port |
| `--mode merged` | The whole spec at once instead of phase by phase (see below) |
| `--fix-rounds` | After failing checks, a fresh agent gets a QA-style bug report, up to this many times |
| `--resume` | Continue existing runs with the phases they haven't done (after phases were added): restores code and database from the last attempt |
| `--overwrite` | Delete existing results and start again |
| `--keep` | Keep the containers to inspect the live database |
| `--max-turns`, `--timeout-min`, `--model` | Per-phase limits (200 turns, 180 min) and model override |

Without `--resume` or `--overwrite`, existing results make the run ask which you want.

### One-shot runs (`--mode merged`)

To compare with an agent that knows every requirement up front, a scenario can define `[[merged]]` steps
in its `scenario.toml` (so far: `shop`):

1. **build**: one session gets the whole spec at once (`prompts/merged/spec.md`, consolidated by hand from
   all phases, with later changes already applied). It gets the turn and time limits of all phases together
   (e.g. 18 × 200 turns). Then deploy, seed (plus everything `seed_more` adds over time) and run every
   phase's checks. Its fix rounds only fix a deploy that doesn't start.
2. **data_changes**: a fresh session gets the requests that change specific production data and so only make
   sense once it exists (e.g. backfilling Northloom's materials), with `constraints.md`. Then every check
   again, with the usual fix rounds.

```bat
uv run -m harness.batch --scenario shop --db postgres mongodb --repetition 1-3 --mode merged --fix-rounds 2
```

Results go to `<db>_merged_rep<N>` next to the step-by-step runs; `harness.report` then adds a
**one-shot vs step by step** section: effort, context compactions, code size, data model, and the full
check suite at the end, overall and by the phase each check came from. Merged runs take repetitions 1–49.
`scenarios/<name>/tests` replays the one-shot order against the reference app, so the checks are known to
be satisfiable in that order.

### Change-request probes (`harness.probe`)

To compare how easy the finished code is to change, a scenario's `[[probes]]` (so far: `shop`'s `vat`) is one
more unseen change request given to every complete run, step by step and one-shot alike. For each run, in its
own containers: restore the final code and database, one fresh agent session with the probe's prompt and
`constraints.md`, deploy, then every phase's checks (failures are regressions) plus the probe's own checks, with
the usual fix rounds. Results go to `<run>/probes/<probe>/`.

```bat
uv run -m harness.probe --scenario shop --probe vat --fix-rounds 2 --parallel 4
```

Don't probe a run while it is running: the probe uses its app port.

## What a run does

1. Starts the agent container and the forced database (MongoDB as a single-node replica set, so
   transactions work).
2. Per phase: a fresh agent session gets the prompt (change phases add `constraints.md`: the app is
   live, existing data must survive, old clients must keep working).
3. Deploys: commits ("Deploy <date>"), restarts, runs `./start.sh`; after the first phase seeds the
   same data for every run.
4. Runs this phase's checks and all earlier ones (an earlier one failing is a regression), then fix
   rounds if needed.
5. Records metrics, schema, a full data dump and the code to `runs/<scenario>/<db>_rep<N>/`.

The agent can't tell it's being evaluated: only the app's repo is in the container, git history shows
ordinary deploys, names are neutral, and every prompt goes to a fresh session.

## Reading results

`harness.report` prints per scenario:
- **Medians per database and phase**, `±` half the range across runs. `total_cost` = the change plus
  its fix rounds; `pass_rate` = first try, `final_pass` = after fix rounds; `favours` and `mechanism` =
  the prediction from `scenario.toml`; `total_lines` and `largest_file` = the code size after the phase;
  `unexecuted` = share of the phase's added code lines that no check executed (Node coverage while the
  checks run; tests and migration files left out), a sign of speculative code.
- **Totals per database** over complete runs.
- **Code quality at the end** of every complete run, from its saved code with
  [lizard](https://github.com/terryyin/lizard) (about 20 languages, nothing to set up per language): share of
  the code in the largest file, function length and cyclomatic complexity (90th percentile and maximum),
  share of the code in functions with complexity above 10, and duplicated code. Only the agent's own source
  counts (no dependencies, vendored copies, build output or tests); lower is cleaner. Cached per commit in
  `<run>/code_quality.json`.
- **Change-request probes** (if any were run): cost, first-try and final pass rate of the probe's checks,
  and regressions.
- **Verification**: runs that need a look (app didn't start, data not in the forced database, checks
  still failing after the fix rounds, seeded data lost in storage).

`<runs>/report.html` has the same tables, one scenario per tab, with every `--details` table in collapsed
sections and sortable columns.

In a run folder: `metrics.jsonl` (one row per attempt), `traces/` (full agent transcripts),
`dumps/` (data after each attempt), `db_<phase>.*` (schema), `repo.bundle` (the code:
`git clone repo.bundle code`), `workspace_extra.tgz` (files git ignores but the app may need, e.g. a vendored
package's `dist/`; restored with the code), `app_<phase>.log`.

Older result sets: `runs_standalone/` (MongoDB without a replica set) and `runs_7phases/`; read them
with `--runs <folder>`. Costs are list-price estimates, and runs are noisy: compare medians.

## Adding a scenario

Copy an existing one. A scenario folder has `scenario.toml` (app names, port and the phases with their
predictions, validated by `harness/scenario_file.py`), `prompts/`, `seed.py`, `checks/` (one module per
phase; shared helpers in `harness/suite.py`), `storage.py` (checks on the dumps), and `tests/` with an
in-memory reference app. `uv run pytest` must show the checks pass for the reference app and fail for
deliberately broken versions of it.

## Cleaning up

A finished run removes its containers. After a crash or `--keep`:
`docker compose -p inspections_postgres_rep1 down -v`.
