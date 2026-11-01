# Findings

Two questions:
1. **Maintenance cost and its drivers:** what does it cost a coding agent to keep changing an app with
   live data as the requirements evolve, and which properties of an app or a change make that much
   cheaper on MongoDB, or on Postgres?
2. **Where LLMs handle MongoDB poorly:** which mistakes agents make with MongoDB (and not with Postgres),
   what they cost, and which metrics surface them.

Agent: Claude Code with `claude-opus-5-5` (results in `runs/`) and `claude-sonnet-5-5` (`runs_sonnet-5-5/`);
run names (e.g. mongodb_rep2) refer to the Opus runs unless stated; costs are list-price estimates; n = 3
runs per database unless stated. Scenarios are steered towards MongoDB on purpose (predictions per phase
in `scenario.toml`). Update this file after each batch.

## 1. Maintenance cost

- Whether MongoDB's early advantage lasts depends on the model. With Opus it faded in inspections (phases
  0–8 −18%, 9–20 +18%, 21–26 −4%; all 27 a tie). With Sonnet MongoDB stayed cheaper throughout
  inspections and CRM, and only shop narrowed to a tie in its later stages.
- Late on, the agent's own earlier code decides cost more than the database: a central write path is
  cheap to extend, scattered writes and data are not (`snapshots` on MongoDB $1.60 vs. $3.03 and $3.28;
  Postgres $2.01–2.23).
- The model matters more than the database. Sonnet passes the same checks for about a fifth of Opus's
  cost with a third of the code (inspections 3.1k vs. 9.7k lines on MongoDB), and with Sonnet MongoDB is
  cheaper or level in every scenario: inspections $8.54 vs. $9.65 (−12%), CRM $4.38 vs. $4.99 (−12%),
  shop a tie ($5.13 vs. $5.12), MongoDB cheaper on the flexible-schema phases, Postgres on `bestsellers`
  and `sales_reports`, as predicted. Opus's
  MongoDB penalty in phases 9–20 is gone: Sonnet builds none of what cost Opus there (no MongoDB
  transactions, no rollout sweeps or triggers; Postgres runs still lock rows), and `due_dates`,
  `offline_edits`, `item_history` and `rename_field` flip to MongoDB. Postgres is cheaper on
  `compliance_reports`, `snapshots`, `submission_documents` and `multi_level` (with Opus only on
  `snapshots`).
- CRM and shop stages 9–17 (Sonnet): MongoDB 15% cheaper on CRM, a tie on shop. In the two shop stages
  where MongoDB was clearly dearer it was mostly not the database: the container has no `mongosh`, so some
  agents wrote driver scripts to look at the data (reasonably, rather than installing tools). The
  exception is a change that must hook into every write path (a stock ledger), which a Postgres trigger
  makes cheap.
- Sonnet puts almost the whole app in one file, on both databases: by stage 17 the largest file holds
  77–100% of the code in 11 of 12 CRM and shop runs, and only 2 runs have any tests. Every change starts
  by reading thousands of lines of one file.

### Drivers

| Driver | Cheaper on | Why | Status and evidence |
|---|---|---|---|
| Shape changes on live data | MongoDB | no DDL, migration file or backfill per change | Supported: initial builds 22–33% cheaper with Opus (inspections $0.65 vs. $0.97, CRM $0.50 vs. $0.69, shop $0.59 vs. $0.76, one run each), 6–17% with Sonnet; inspections 0–8 −18%; every phase of the Opus CRM runs |
| Payloads of varying shape, queried by path | MongoDB | stored and queried as they come | Supported: `inbound_leads` $0.88 vs. $1.40 |
| Queries built from user input | MongoDB | a MongoDB query is a JSON object; SQL needs a query builder | Not confirmed: reading `advanced_search` ($1.24 vs. $1.70) found no database-specific cause |
| Returning client data exactly as sent (key order, original timestamp text) | MongoDB | JSONB reorders keys and `timestamptz` reformats, so Postgres agents store each value twice | Seen: all 3 Postgres `initial` runs stored `json` + `jsonb` and `timestamptz` + text, and all hit the same parameter-typing error. Partly our wording ("exactly as submitted"); the checks only compare content |
| A version counter on the aggregate, bumped by every change | MongoDB | an `$inc` in the update that already changes the document; Postgres must update the parent row from every child-table write | Opus: `submission_documents` $1.91–2.34 vs. $2.50–2.78. Not with Sonnet, see the next row |
| The first need for a multi-document all-or-nothing write | Postgres | Postgres code already runs in transactions with row locks; MongoDB code without transactions must thread sessions through its write paths | Sonnet: all 3 MongoDB apps added their first transaction in `submission_documents` ($0.85 vs. $0.73); the Opus apps already had them |
| A JSON API shaped like the stored data | MongoDB | no row mapping or type conversion | Hint: MongoDB code 13–27% smaller in inspections and shop, the same size in CRM |
| The whole document as the API contract | MongoDB (expected) | one read and one write instead of reassembling tables | Weak when added late (inspections 21–24); untested as a day-one design |
| User-defined types with different nested shapes | MongoDB (expected) | stored as is | Weak (inspections 25–26) |
| Built-in features: TTL, geo, change streams, search | MongoDB (expected) | stock Postgres lacks them | Not supported: agents don't use them (section 2), and MongoDB was cheaper on geo and webhooks anyway. We keep the stock images (no PostGIS, no Atlas Search), so a Postgres cost from missing features is fair |
| Old records allowed to keep their shape | MongoDB (expected) | no backfill; readers handle both shapes | Untested |
| Queries and reports across many records or entities | Postgres (expected) | joins and `GROUP BY` vs. `$unwind`/`$lookup` pipelines, or restructuring embedded data | Mixed (first try): `findings_dashboard` Opus $0.97 vs. $0.77, Sonnet $0.22 vs. $0.23; `compliance_reports` Opus $1.30 vs. $1.42, Sonnet $0.28 vs. $0.23 |
| Concurrent writes to a busy record | Postgres | agents lock rows and wait; on MongoDB they hand-roll retries and transactions abort on conflict (section 2) | Opus only, twice in one run's code: `item_history` mongodb_rep2 $2.99 (others about $1.5) and `snapshots` mongodb_rep2 $3.28. Sonnet's `snapshots` had no such trouble (MongoDB +12%) |
| Defaults that must also cover writes by old app instances during a deploy | Postgres | a trigger covers every writer; MongoDB agents add a "rollout sweep" in app code | Seen with Opus: `due_dates` $1.25–1.27 vs. $0.83–0.96; every Postgres run used a trigger. Not with Sonnet (MongoDB cheaper) |
| Metadata on nested elements kept out of the client's data (revisions, types) | Postgres | a side table keyed by item id; MongoDB agents keep parallel arrays keyed by item path, which moves and deletes must keep in sync | Seen with Opus: `offline_edits` $1.18–1.80 vs. $1.01–1.10. Not with Sonnet (MongoDB cheaper) |
| Access patterns changing after the schema is designed | Postgres | normalized tables serve any pattern; documents must be reshaped (section 2) | Interpretation |

No measurable difference: giant files (2–4.5k-line files in every run by phase 26) on both databases,
runtime-only MongoDB advantages such as atomic nested updates (`item_api` Opus $1.06 vs. $0.84, no gain), counters, time zones,
idempotency, and how concurrent appends are written (`multi_level`: 4 of 6 MongoDB runs used atomic
`$push` with `arrayFilters`, 2 a version-checked rewrite, with no cost pattern). Reports and filters
(`compliance_reports`, `advanced_search`, `type_details`) varied with over-engineering and wrong test
expectations, not the database (but see section 2 for how MongoDB reports were computed). Postgres agents
put anything document-shaped in JSONB, so the comparison is MongoDB vs. tables + JSONB.

How we know: besides the totals, we read all runs of 9 sampled phases with Opus (3 cheaper on MongoDB, 3
cheaper on Postgres, 2 close, 1 random) and 4 with Sonnet (where it disagreed with Opus or favoured
Postgres), and checked the claims above against the transcripts and the code. `rename_field` (MongoDB
dearer with Opus) showed no clear database-specific cause.

## 2. Where LLMs handle MongoDB poorly

General pattern: the extra Postgres costs are mostly inherent (migrations, strict types, rebuilding
aggregates from tables, every write path touching the parent row), and agents use Postgres idiomatically
(triggers, row locks, side tables). The extra MongoDB costs are mostly the agent's choices: relational
habits (backfills, side structures, read-modify-write with retries) instead of MongoDB idioms (a missing
field vs. `null`, data inside the element, single-document atomic updates). So the MongoDB costs measured
here are an upper bound; better practice could shrink the Postgres-favouring drivers below.

Not only SQL habits: agents also leave MongoDB's own tools unused. Most MongoDB apps compute reports by
fetching every matching document and counting in JavaScript instead of using the aggregation pipeline,
while Postgres apps aggregate in SQL. That is cheaper to write (likely why Opus MongoDB came out cheaper on
`compliance_reports`), but every report loads every matching document: invisible at our data size, a
problem at real volume. A lower cost can hide a worse app, which our cost and check metrics don't see.

| Problem | With Postgres the agent… | With MongoDB the agent… | Better MongoDB practice | Evidence |
|---|---|---|---|---|
| Concurrent writes | locks rows and waits (`SELECT … FOR UPDATE`, 8–14 sites per run); extending a transaction stays safe | reads, changes and writes back with a version check, in hand-made retry loops; MongoDB transactions abort on conflict, so each added write raises retries | change one document with update operators (atomic, no retry code); `withTransaction` for multi-document writes; outbox or change streams for derived records | mongodb_rep2 twice: `item_history` (history insert in the transaction: retries up to 19, redesigned as an outbox, $2.99) and `snapshots` (retries 5 → 21, 503s). The cheap `snapshots` run used `withTransaction` |
| Metadata on nested elements | keeps it in a side table keyed by item id | keeps it outside the items, in parallel arrays keyed by item path; moves must carry the entries, and the `arrayFilters` get complex | keep it inside the element and leave it out of responses, or in its own collection keyed by item id | `offline_edits` mongodb_rep2 ($1.80): a second PATCH returned 500 from an unused array filter |
| Derived defaults on existing records | adds a trigger, which also covers old app instances during the deploy | backfills, then adds a "rollout sweep" and a hidden flag so the sweep doesn't undo deliberate clears | treat a missing field as "use the default" and store `null` only when a user clears it; an idempotent `updateMany({dueDate: {$exists: false}})` on start then needs no flag (rolling deploys weren't asked for anyway) | `due_dates` (Opus): every MongoDB run $1.25–1.27, every Postgres run $0.83–0.96. Sonnet didn't do this and was cheaper on MongoDB |
| Reports over nested data | aggregates in SQL (`GROUP BY` with `jsonb_array_elements`: all 3 Opus runs) | fetches the matching documents and counts in application code instead of an aggregation pipeline (2 of 3 Opus runs, 2 of 3 Sonnet runs) | `$unwind` + `$group` in the pipeline, so the database counts and only the groups come back | `compliance_reports`. Cheap to write (Opus MongoDB $1.30 vs. $1.42), but every report loads every matching document; doesn't show at our data size. Sonnet's Postgres runs also mostly counted in code |
| Schema drift | keeps a normalized schema that serves new access patterns through joins | designs documents for today's access patterns, then bolts features on instead of reshaping them when the patterns change | revisit the document design when access patterns change ("data accessed together is stored together"), and migrate | Findings embedded (2 of 3 runs), then searched across submissions: phases 9–20 +18% on MongoDB. Findings in their own collection (mongodb_rep3), then whole-document phases: `snapshots` $3.03 |
| Missing transactions | — | on a standalone server (no replica set) builds workarounds instead of saying transactions need one | run a replica set | CRM `contact_merge` (Opus): MongoDB 41% dearer than Postgres on a standalone server (`runs_standalone/`), 17% cheaper with a replica set |
| Multi-document writes without transactions (replica set available) | uses transactions throughout, with events queued in the same transaction | in the CRM (2 of 3 runs) uses none: hand-made locks or an in-process queue; writes webhook events outside the change, so a crash loses them | `withTransaction` around the change and its outbox insert | Sonnet CRM. The shop apps use transactions, needed for stock from the first stage |
| Built-in features left unused | uses built-in full-text search | hand-writes search in JavaScript over every loaded document; no `$text`, `2dsphere`, TTL or change streams | use them | All 6 Sonnet MongoDB apps. No cost at our data size, a problem at scale |

**Metrics to surface these** (per attempt):

| Metric | Surfaces | Status |
|---|---|---|
| Largest file, files over 1,000 lines | Code concentration | Recorded (`largest_file` in the report) |
| Waste signals from the traces: failing then fixed test runs, the same test rerun (flakiness), reverted changes, temporary instrumentation, files re-read, the agent's own words ("regression", "flaky") | Self-inflicted detours, whatever the cause | Planned |
| Concurrency code: lock sites, transactions, hand-made retry loops, read-modify-write sites | Concurrent-write problem, writes without transactions | Counted by hand; automate |
| Collections or tables a request reads and writes; `$lookup`s | Schema drift | Planned |
| Database features left unused: reports, search, geo, expiry and change capture done in application code instead of the database | MongoDB used as plain storage | Read by hand (`compliance_reports`, CRM and shop 9–17). A larger data set would show it as slow responses |
| Over-engineering: share of a phase's added lines that no check executes (Node's built-in coverage via `NODE_V8_COVERAGE` while the checks run), and lines added relative to the same phase's median | Measures code no checked requirement needs (unrequested options, dead branches, refactor leftovers; also our check gaps). Misses code that runs needlessly (e.g. a rollout sweep at every start); overcounts legitimate defensive code like error handling, contention-only retry paths and applied migrations. Compare runs of the same phase | Unexecuted share recorded from now on (`unexecuted` in the report; migration files and tests left out); not in earlier runs |

An untested remedy for both: a short guide to MongoDB practices in the agent's instructions (with an
equally short Postgres guide as the control).

## Next

1. Read more phases the same way (sampled, all runs per phase), and the shop and CRM runs; turn the
   signals that keep recurring into report metrics.
2. Probes on small, young apps (initial build plus 3–4 phases, 2 runs per database): rapid shape changes,
   whole document from day one, queries from user input, built-in features, a practices guide.
3. More repetitions before trusting $0.1–0.3 differences.
4. Run Opus on CRM and shop stages 9–17, to see whether the Sonnet patterns hold.

Compare first-try costs for `export_import`, `findings_dashboard`, shop `initial`, `subscriptions` and
`craftly_import`: their fix rounds came from our checks or prompts (since fixed), not the agent.

## Scenario ideas

| Idea | Driver | Sketch |
|---|---|---|
| Rapid MVP churn | Migrations | 15–20 small shape changes on live data: optional field, value to list, nest into a sub-object, split a name, rename with the old name still accepted, change the id format |
| Page builder | Whole document, nested block types | Sections, nested columns of blocks, user-defined block types, drafts and publish, page history and restore, per-language overrides, A/B variants, duplicate a page, site export/import; reusable global blocks as the SQL control |
| Game player state | Shape changes, whole document | A new seasonal event structure every few weeks, whole-profile load and save; leaderboards as the SQL control |
| LLM backend | Payload drift, nested parts | Message parts of many types, a second provider format, conversation branching, agent traces |

Done: form builder (`inspections`), CRM (`crm`), catalog (`shop`). Dropped: IoT device twins (their driver,
concurrent nested updates, doesn't show). Webhook event store: calibration only (JSONB is fine there).
