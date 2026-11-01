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

Needs Docker Desktop, [uv](https://docs.astral.sh/uv/) and a Grove (or Anthropic) API key.

```bat
uv sync
copy .env.example .env
:: put your key in .env (ANTHROPIC_FOUNDRY_API_KEY); CLAUDE_MODEL must be the same for runs you compare
:: another model: set CLAUDE_MODEL and RUNS_DIR (e.g. runs_sonnet) in .env; every command then uses that folder

:: several runs in parallel; lists them and asks before starting, then builds the agent image once
uv run -m harness.batch --scenario inspections --db postgres mongodb --repetition 1-3 --parallel 6 --fix-rounds 2
:: one run
uv run -m harness.run --scenario inspections --db postgres --repetition 1 --fix-rounds 2

uv run -m harness.watch inspections/postgres_rep1   :: follow the agent live
uv run -m harness.report                            :: results (--details for every run and failing check)
uv run -m harness.audit                             :: flagged agent commands (looking outside the repo, destructive, live DB)
```

| Option | Meaning |
|---|---|
| `--repetition` | Run number (1–99; batch: `1-3` or `4,5`); also sets the app port |
| `--fix-rounds` | After failing checks, a fresh agent gets a QA-style bug report, up to this many times |
| `--resume` | Continue existing runs with the phases they haven't done (after phases were added): restores code and database from the last attempt |
| `--overwrite` | Delete existing results and start again |
| `--keep` | Keep the containers to inspect the live database |
| `--max-turns`, `--timeout-min`, `--model` | Per-phase limits (200 turns, 180 min) and model override |

Without `--resume` or `--overwrite`, existing results make the run ask which you want.

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
- **Verification**: runs that need a look (app didn't start, data not in the forced database, checks
  still failing after the fix rounds, seeded data lost in storage).

In a run folder: `metrics.jsonl` (one row per attempt), `traces/` (full agent transcripts),
`dumps/` (data after each attempt), `db_<phase>.*` (schema), `repo.bundle` (the code:
`git clone repo.bundle code`), `app_<phase>.log`.

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
