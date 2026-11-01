# Scenario: field inspections

A form-builder backend for companies in very different industries: each company defines its own
inspection checklists, workers submit completed inspections from tablets, and managers filter and
report on them. The shape of the data is defined by users at runtime and keeps changing, so this is
the document-favouring flagship; `compliance_reports` is the relational-favouring "flip". Phases
9–20 then work on single items deep inside submissions (edit, findings, search, bulk changes, moves,
revisions, history), where JSONB (read, change and write back the whole document) and documents
(atomic updates at a path) differ most, with two flips (`item_catalog`, `findings_dashboard`) and two
cross-cutting phases (`users_and_roles`, `item_history`). Phases 21–26 treat a submission as one
unit (download and upload, export and import, kept versions and restore, re-inspection copies) and
add per-type nested shapes inside items (type details, attachments). Improvements (kind
`improvement`) change existing behaviour instead of adding features.

Repo folder in the container: `/workspace/field-inspections`, database `inspections`, app ports
3000–3199.

## Phases

The phases, with what each change asks for, which database it is expected to favour and why, are in
[scenario.toml](scenario.toml). After `multi_level`, the harness also redeploys the app and checks
that item ids of existing submissions didn't change.

Every change prompt is followed by [prompts/constraints.md](prompts/constraints.md) (the app is live,
existing data must survive).

## Seed data

[seed.py](seed.py) creates the same data for every run (fixed random seed) through the API: 5 forms
and 280 submissions from 4 companies. Acme's fire-safety form (80 submissions) is the one phase 1
converts; Northwind's kitchen audit is the one the phase-3 checks edit.

Production keeps growing (the scenario's `seed_more`, called once after a phase's first successful
deploy):
- after `report_timezone`: Tidewater Properties' "Building Safety Walk" (floors → rooms → items) with
  40 nested submissions, about 20% of items failed. The `findings` phase turns their failed items
  into findings; `rename_field` renames their `notes` to `remarks`.
- after `findings`: five findings recorded without a due date, which `due_dates` must backfill.

Both are stored in `seed.json` under `late` and `late_findings`.

## Checks

Check names are prefixed `v0.` … `v16.` by the feature phase that introduced them, and `i1.` … `i4.` by
the improvements (in phase order: `v7.` is `item_api`, `i3.` is `rename_field`). Each later phase
re-runs all earlier checks, so a failing earlier check there is a **regression**.

| Group | Checks | What a failure means |
|---|---|---|
| Basic API | `v0.create_and_get_form`, `list_forms_by_company`, `create_submissions`, `read_back_exact`, `list_newest_first`, `limit_param` | Core contract broken |
| Validation | `v0.rejects_*`, `v0.invalid_not_stored` | Invalid input accepted or stored |
| Filtering | `v0.filter_*` | Filtering on answers returns the wrong set (exact-set comparison) |
| Existing data | `v0.seeded_forms_intact`, `v0.seeded_submissions_intact`, `v0.seeded_filter_*` | Seeded production data lost, altered, or not queryable. After the change this is the main data-preservation signal |
| Groups | `v1.create_group_form`, `group_submissions_read_back`, `v1.rejects_*`, `group_invalid_not_stored` | New field type broken |
| Group filters | `v1.group_filter_any_item`, `group_filter_same_item(_2)`, `group_filter_date`, `group_filter_mixed`, `group_form_top_level_filter` | `same_item` checks catch filters that match conditions on *different* extinguishers |
| Conversion | `v1.acme_form_converted`, `acme_submissions_converted` | Acme's existing form and its 80 submissions were not migrated correctly |
| Backward compatibility | `v1.acme_old_key_filter_*`, `acme_new_path_filter`, `acme_accepts_old_shape(_partial)`, `acme_accepts_new_shape` | Old tablets or dashboards broken by the change |
| Nested groups | `v2.create_nested_form`, `accepts_four_levels`, `rejects_five_levels`, `nested_submissions_read_back`, `v2.rejects_nested_*`, `nested_invalid_not_stored` | Groups inside groups broken, or validation not applied at every level |
| Item ids | `v2.item_ids_present_and_unique`, `client_item_ids_kept`, `item_ids_stable_between_reads` | Group items lack ids, ids collide, client ids are overwritten, or ids change |
| Backfill | `v2.acme_items_backfilled_ids`, `acme_item_ids_stable_between_reads`, `item_ids_stable_across_redeploy` | Existing items didn't get ids, or ids change on read or on redeploy |
| Deep filters | `v2.deep_filter_any_item`, `deep_filter_same_item`, `deep_filter_same_floor(_2)`, `deep_filter_same_room`, `deep_filter_with_top_level`, `deep_filter_leaf_only` | `same_*` checks catch conditions matched on different items, rooms or floors |
| Appends | `v2.append_nested_item`, `append_top_level_item`, `append_respects_max_items`, `append_rejects_invalid_item`, `append_unknown_path_404` | The new endpoint doesn't work, validate, or find paths |
| Concurrency | `v2.append_concurrent_no_lost_items` | 50 parallel appends (30 to one room, 20 to another): any item lost or duplicated fails |
| Existing data as version 1 | `v3.existing_forms_are_version_1`, `existing_submissions_are_version_1`, `acme_renders_converted_version_1` | Existing forms or submissions didn't become version 1, or version 1 isn't the form as it was |
| Editing | `v3.create_form_is_version_1`, `edit_creates_version_2`, `versions_are_retrievable`, `listed_forms_show_latest_version`, `rejects_company_change`, `rejects_invalid_edit` | Editing doesn't create versions, loses old ones, or accepts invalid edits |
| Submissions per version | `v3.submissions_record_version`, `old_answers_unchanged`, `v3.rejects_new_*` | Submissions don't record their version, old answers changed, or new submissions aren't validated against the latest version |
| Rendering | `v3.renders_old_version`, `renders_new_version` | A submission isn't shown with its own version's fields, labels, types and order |
| Filters across versions | `v3.version_filter_*` | Filters don't use each submission's own version, e.g. `score=6` must match only submissions where `score` was still a number |
| Editing a seeded form | `v3.edit_seeded_form`, `seeded_submissions_unchanged_after_edit`, `seeded_renders_old_version`, `seeded_new_submission_uses_new_version`, `seeded_filter_on_*` | Editing a live form (the kitchen audit) broke its existing submissions or their filters |
| Operators | `v4.range_*`, `in_list`, `contains_case_insensitive`, `explicit_eq`, `submitted_at_range`, `rejects_unknown_operator` | Range, list, substring or submission-time filters return the wrong set; unknown operators accepted |
| Operators on the same item | `v4.range_same_item`, `range_same_item_2`, `range_same_item_none` | A range condition and another condition were satisfied by *different* group items |
| Company-wide search | `v4.company_*` | Wrong set or order, ignores a form's own fields, leaks another company's data, or accepts a missing `companyId` |
| Search on existing data | `v4.seeded_*` | The new operators don't work on the production data (incl. Acme's converted items and fields removed in later versions) |
| Uploads against a version | `v5.late_upload_old_version`, `late_upload_validated_against_its_version`, `latest_version_by_default`, `explicit_latest_version`, `rejects_unknown_version`, `late_upload_filter_*` | A late upload isn't validated, stored, rendered or filtered with the version it names, or unknown versions are accepted |
| Safe retries | `v5.retry_returns_stored`, `retry_with_different_content_409`, `client_id_scoped_per_form`, `concurrent_retries_single_submission` | Retries create duplicates (also when 10 arrive at once), conflicting retries overwrite data, or ids leak across forms |
| Batch upload | `v5.batch_mixed_results`, `batch_dedupes_within_batch` | Wrong per-entry statuses, one bad entry affects the others, or duplicates are stored |
| Late uploads on existing forms | `v5.seeded_*` | Uploads against the kitchen audit's original version, or old-shape Acme uploads, are not handled |
| Form reports | `v6.report_*` (counts, items, matrix, levels, filters, versions, site and month, `null` groups) | Wrong groups, counts or rates: e.g. counting submissions instead of items, ignoring filters or versions, dropping `null` groups |
| Report validation | `v6.report_rejects_*`, `company_report_requires_company` | Invalid groupings (paths on two branches, unknown keys, malformed rate) are accepted |
| Company reports | `v6.company_report_*` | Reports across a company's forms are wrong where forms differ in their fields |
| Reports on existing data | `v6.seeded_*` | Reports disagree with the app's own records (the checks aggregate the app's data independently) |
| Case-insensitive text filters | `i1.text_filter_*`, `select_filter_*`, `i1.seeded_*` | Text filters still depend on case (also in groups, with the same-item rule, and for Acme's old keys), match partial values, or `select` filters started ignoring case |
| Reports in local time | `i2.report_*`, `company_report_in_timezone`, `i2.seeded_*` | Months not computed in the given timezone (month ends, daylight saving), default no longer UTC, or an unknown timezone accepted |
| Single items | `v7.get_*`, `patch_*`, `delete_*`, `concurrent_*`, `v7.seeded_*` | Items not found at depth, changes not validated against the submission's own version, deletes ignore `minItems`, or concurrent changes to different items (and appends) overwrite each other |
| Findings | `v8.*`, `v8.seeded_failed_items_have_findings` | Findings or comments wrong, findings leak into `answers`, concurrent comments lost, or Tidewater's failed items not backfilled exactly once |
| Finding search | `v9.*` | Wrong set, order or location (path) of findings, broken cursor paging, or another company's findings |
| Users | `v10.*` | Missing `401`/`403`s on any endpoint, inspectors allowed to change forms, users or others' finding status |
| Bulk actions | `v11.*` | Wrong findings changed, or concurrent comments and item edits lost during a bulk change |
| Moves | `v12.*` | Items, nested items or findings lost or duplicated in moves (also concurrent), paths not updated, limits ignored |
| Revisions | `v13.*` | `rev` not counted, stale `ifRev` accepted, sync results wrong, or more than one concurrent winner |
| History | `v14.*` | Changes missing, in the wrong order, without before/after or user, or recorded more or less than once under concurrency |
| Rename | `i3.*` | Tidewater's form or answers not renamed in storage, the old key not accepted in writes, filters and reports, or other forms affected |
| Equipment types | `v15.*` | Type renames or interval changes not visible everywhere, types in use deletable |
| Findings dashboard | `v16.*` | Wrong counts, open, overdue or average time to resolve; `resolvedAt` not following the status |
| Due dates | `i4.*` | Due dates not derived from severity (new and existing findings), or `overdue` wrong |

Leniencies, so the checks measure behaviour rather than formatting: `null` counts as an omitted key;
creation may return 200 or 201 and rejection any 4xx except 404; timestamps are compared as instants;
lists may be a bare array or an envelope; group-item `id`s are ignored when comparing answers (the
`v2.` id checks test them); filters over live data accept records the agent added itself.

## Storage lines in the verification

From the database dumps after each change phase, compared with the dump after `initial`:

| Line | What it checks |
|---|---|
| `seeded records still in the database` | Every seeded submission's id still appears in the dump |
| `Acme submissions stored in the new shape` | The 80 converted submissions contain `extinguishers` *in the database*; fewer means converting on every read ("lazy") or not at all |
| `old Acme keys still present` (info) | Old flat keys still stored; fine if kept as a backup copy |
| `other seeded records changed in storage` | Records that should be untouched are byte-for-byte unchanged; non-zero is expected only if a column was added to every row |
| `tables/collections that lost rows` | A shrinking table almost always means deleted data |

## Database design

- **PostgreSQL** (`db_<phase>.sql`): an `answers(submission_id, field_key, value…)`-style table is EAV
  (entity–attribute–value); `jsonb` columns holding answers are documents inside Postgres; or a mix.
  Note when this changes, e.g. a JSONB column appearing only in a change phase.
- **MongoDB** (`db_<phase>.json`): `shapes` is the number of distinct top-level key sets per
  collection; a jump after a change hints at half-migrated documents.

## Testing the checks

[tests/reference_app.py](tests/reference_app.py) implements the spec for all phases in memory (the
data migrations are test-only endpoints). [tests/test_checks.py](tests/test_checks.py) verifies:

- a correct app passes every check in every phase, with no seed records rejected
- existing item ids survive a redeploy
- an app whose filters let each condition match a *different* group item fails exactly the
  "same item" checks, and nothing else
- an app that filters every submission with the *latest* form version, instead of its own, fails
  exactly the version-aware filter checks, and nothing else
- an app that changes items by reading a copy of the submission and writing it back without a lock
  (read-modify-write of a whole document) fails exactly the concurrent-change checks
