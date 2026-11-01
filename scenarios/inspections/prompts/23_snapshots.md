# Undo for inspections

Inspections are sometimes ruined by a bad upload or a wrong bulk action. Managers want to see an
inspection as it was at any earlier point, and restore it.

## 1. Versions
- Every change that increases a submission's `docRev` keeps the document as it was after the
  change. For existing submissions, the first kept version is their current document.
- `GET /submissions/:id/revisions` → `[ { "docRev": 7, "at": "…", "by": "u-17" } ]`, oldest first;
  `by` is the `X-User-Id` of the change, or `null`.
- `GET /submissions/:id/document?docRev=<n>` → the document as it was at that `docRev` (`404` if it
  wasn't kept).

## 2. Restore
`POST /submissions/:id/restore` with `{ "docRev": 5 }`:
- Makes the submission's answers, items (with their ids), types and findings (with comments) the
  same as in that version: later items and findings are removed, removed ones come back, changed
  ones change back.
- This is a new change: `docRev` increases by 1, and the older versions stay available.
- Each item it changes, brings back or removes gets a history entry
  `{ "action": "restored", "docRev": 5 }` (with `at` and `by`). Items it changes or brings back get a
  `rev` higher than any they had, so stale `ifRev` edits are rejected.
- → `200` with the new document; `404` for a version that wasn't kept.
