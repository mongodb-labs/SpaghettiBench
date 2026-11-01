# Scenario: agency CRM

A CRM for small agencies in very different industries (real-estate brokers, recruiters, event
planners): contacts, the companies they work for, and deals. Agents tend to start from a normalised
design (`contacts`, `companies`, `deals`), not JSONB. The first nine stages split their feature changes evenly:
three are expected to favour documents, three relations. That makes this the scenario that shows whether
the benchmark can detect a Postgres advantage at all. Stages 9–17 add breadth: polymorphic activities,
saved queries, search, currencies, geo, bulk import, trash and outbound webhooks. Three improvements (kind
`improvement`) change existing behaviour instead of adding features.

Repo folder in the container: `/workspace/agency-crm`, database `crm`, app ports 3200–3399.

## Stages

The stages, with what each change asks for, which database it is expected to favour and why, are in
[scenario.toml](scenario.toml). Every change prompt is followed by
[prompts/constraints.md](prompts/constraints.md).

## Seed data

[seed.py](seed.py) creates the same data for every run through the API: 3 agencies, 24 companies,
177 contacts and about 140 deals. Harbor Realty has three planted pairs of duplicate contacts
(same address in different case; different addresses; one with only a phone), which the
`contact_merge` checks merge, and a contact that a phase-3 lead must be matched to. Talentbridge's job
titles include the edge cases of the seniority rule ("Leadership Coach", "Sr. Recruiter",
"Senior Team Lead").

## Checks

Prefixed `v0.` … `v14.` by the feature stage that introduced them (`v2.` is `custom_fields`, and so on)
and `i1.` … `i3.` by the improvements; each later stage re-runs all earlier ones,
so a failing earlier check is a **regression**. Fixture checks run in fresh agencies (e.g.
`lisbon-homes`, then `lisbon-homes-2` on the next run) and compare exact sets.

| Phase | What the checks cover |
|---|---|
| `v0.` | Create/read/list companies, contacts and deals; validation and nothing stored on rejection; filters (case-insensitive email, unicode names, company, AND); PATCH semantics (partial, `null` clears); deals showing the *current* contact name and company; agency isolation; seeded data intact and filterable |
| `v1.` | Lists read back in order with labels; old `email`/`phone` requests and responses; empty lists; validation; filters on any position; PATCH replacing lists vs. setting the primary entry; seeded contacts converted |
| `v2.` | Definitions (list, duplicates, invalid ones); typed values and validation (incl. another agency's or entity's field); PATCH merging and `null` removal; typed filters on contacts and deals; Talentbridge's definition, backfilled values and filter |
| `v3.` | Leads from FormFlow, LeadPipe v1 and v2; payload stored exactly; contact names, lists and company from the lead; matching existing contacts case-insensitively and appending new addresses; redelivery and 8 concurrent deliveries give one lead; ignored and malformed events; list and payload-path filters (nested, arrays, numbers, booleans); a lead matched to a seeded contact |
| `v4.` | Reports by every key type, several keys, with filters, `null` groups and `winRate`; reports following changed deals and contacts; rejections; seeded reports compared with an aggregation of the app's own data |
| `v5.` | Merged fields, lists and custom values; deals and leads moved; old id returns the kept contact; duplicate gone from lists; history with snapshot (also through chains); rejections (`409` for merged contacts); concurrent merges of the same duplicate and overlapping chains; the seeded duplicates merged |
| `v6.` | `companies` with roles, old `companyId` requests, add/remove, validation; a company's contacts with roles; filter on any position; deals and reports use the primary company; merge combines companies; renames shown in contacts, deals and lists, incl. seeded data |
| `i1.` (`name_search`) | `lastName` ignoring case and accents, the accented query, `ñ`, different letters staying different (`muller` ≠ Mueller), whole-name matching, combined with the company filter; seeded names |
| `i2.` (`open_pipeline`) | `openPipeline` on single contacts and in lists (open stages only), sorting with ties by name, with filter and limit, following deal changes; seeded values match the app's own deals (also after merges), seeded sort order |
| `v7.` | Each activity type with its fields; rejections (missing, foreign or unknown fields, other agency, meeting ending early); timelines newest first incl. meetings attended; `lastActivityAt` following changes; PATCH rules; filters; merging moves activities and de-duplicates attendees |
| `v8.` | Tags in lower case, replaced by PATCH, filter, merged; segments with nested `all`/`any`/`not` over every field type, order and count; results following data changes and PATCHed filters; invalid filters rejected |
| `i3.` (`follow_ups`) | Defaults by stage on create and stage change, explicit dates winning, cleared when won; overdue flag and filters; `nextFollowUpOn`; seeded open deals backfilled |
| `v9.` | Contacts, companies and deals in one result list; names ranked first; every word must match; case, accents and plurals; related text (company, notes, email subjects, domain, whole email address); renames and merges reflected at once; isolation; seeded contacts found by name |
| `v10.` | Rates by date and validation; deal currencies (only with a rate); `valueEur` at the close-date rate (earliest rate before the first, today without a close date); reports, `openPipeline` and segments in euros; rate changes applied everywhere; seeded deals in EUR |
| `v11.` | Locations and validation; nearby nearest first with distances, radius, limit and filters; contacts falling back to their company's location; polygon territories (open or closed ring); territory summary following moves |
| `v12.` | A CSV with odd headers, quoting, a multi-line field and bad rows: counts, rejected line numbers, filling in without overwriting, companies by name, repeated emails; re-import changes nothing; 3,000 rows within 60 s |
| `v13.` | Deleting hides the contact, its deals, activities and leads everywhere (GETs, lists, reports, segments, search, attendees); trash list; a new lead creates a new contact; restore brings everything back with the same ids; purge |
| `v14.` | Subscriptions and validation; every event type delivered with content and HMAC signature; order per contact; a failed delivery retried with the same id and later events held back; the API not slowed by a slow receiver; deleted subscriptions get nothing |

Leniencies: `null` counts as omitted; creation may return 200 or 201; rejection may be any 4xx except
404; email addresses compare case-insensitively and phone numbers by their digits (the app may
normalise them).

## Storage lines in the verification

| Line | What it checks |
|---|---|
| `seeded records still in the database` | Every seeded company, contact and deal id still appears in the dump (merged-away duplicates may be deleted) |
| `tables/collections that lost rows` | Row counts compared with the dump after `initial`; the 3 merged duplicates may explain a drop in contacts from `contact_merge` on |

## Testing the checks

[tests/reference_app.py](tests/reference_app.py) implements the spec for all phases in memory.
[tests/test_checks.py](tests/test_checks.py) verifies:

- a correct app passes every check in every phase, with no seed records rejected
- an app that copies contact and company names into records when they're written (and never updates
  them) fails exactly the "current name" checks
- an app that matches email addresses case-sensitively fails exactly the case checks
- a search that accepts any word of the query (MongoDB's `$text` default) fails exactly the word check
- euro values computed when a deal is written (not following rate changes) fail exactly the rate check

`v14.` runs a receiver on the host; the app reaches it at `CHECKS_CALLBACK_HOST` (default
`host.docker.internal`). On Linux, also set `CHECKS_CALLBACK_BIND=0.0.0.0`.
