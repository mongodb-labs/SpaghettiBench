# Whole inspections

The new tablet app downloads a whole inspection, lets inspectors work on it offline, and uploads it
again in one go.

## 1. Downloading
`GET /submissions/:id/document` → the submission with everything attached to its items:
```json
{ "id": "…", "formId": "…", "formVersion": 2, "submittedBy": "…", "submittedAt": "…", "docRev": 7,
  "answers": { "…": "as in GET /submissions/:id, with item ids" },
  "items": {
    "i-7": { "rev": 3, "typeCode": "EXT-CO2-6", "findings": [ { "…": "finding with its comments" } ] }
  } }
```
- `items` has an entry for every group item at any depth: its `rev`, its type's `code` (or `null`),
  and its findings as in `GET /submissions/:id/items/:itemId`.
- `docRev` is an integer, 1 for existing and new submissions. Every request that changes the
  submission, its items or their findings adds 1 (each edit of a sync, and each submission a bulk
  action changes, counts once).

## 2. Uploading
`PUT /submissions/:id/document?ifDocRev=<n>` with `answers` and optionally `items`, usually the
downloaded document with changes (other keys are ignored).
- `answers` are the submission's complete new answers, validated like a new submission against its
  own form version (`400`). Items keep their ids: an item may change its answers, move to another
  parent, or be left out (it is deleted with its findings). Items without `id`, or with an id the
  submission doesn't have, are new. The same id twice → `400`.
- `items.<itemId>.findings` (optional): a finding with the `id` of one of that item's findings gets
  the given `title`, `severity`, `status`, `assignee` and `dueDate`, and the comments in its
  `comments` that have no `id` are added (`author`, `text`). A finding without `id` is new. Findings
  that are left out stay unchanged. An unknown finding id, or an item id that isn't in `answers` →
  `400`.
- Every change counts as if made with the single endpoints: `rev` (for items whose own fields
  change, or that move), history, due dates, `resolvedAt`, permissions. `docRev` increases by 1.
- All or nothing: if anything is invalid → `400`, and nothing changes.
- `ifDocRev` is required (`400`). If the submission's `docRev` is no longer `n` → `409` with the
  current document, and nothing changes. Of several uploads with the same `ifDocRev` at the same
  time, exactly one succeeds.
- → `200` with the new document.
