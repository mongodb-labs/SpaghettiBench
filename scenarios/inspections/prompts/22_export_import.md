# Moving inspections between accounts

Companies merge, and some customers run a separate account per region. They want to copy a form
with all its inspections into another account.

## 1. Export
`GET /forms/:id/export` → one JSON document:
```json
{ "form": { "name": "…", "versions": [ { "name": "…", "fields": [ "…" ] } ] },
  "itemTypes": [ { "code": "EXT-CO2-6", "name": "6 kg CO2 extinguisher", "intervalDays": 180 } ],
  "submissions": [ { "…": "as in GET /submissions/:id/document" } ] }
```
- `versions`: every version of the form, oldest first, as defined.
- `itemTypes`: the types the exported items have.
- `submissions`: every submission of the form.

## 2. Import
`POST /companies/:companyId/import` with an export → `201`:
```json
{ "formId": "…", "submissions": { "<id in the export>": "<new id>" } }
```
- Creates a new form with the same versions, and all the submissions, with new ids for the
  submissions, their items, findings and comments. Everything else is kept as exported: answers,
  `submittedBy`, `submittedAt`, form versions, finding fields and timestamps, comments. Item
  revisions and `docRev` start at 1; each item's history starts with `created`.
- Item types are matched by `code`; types the company doesn't have yet are created as exported.
- The company may be the exported one or another one. Exporting the imported form gives the same
  document, apart from ids.
- All or nothing: anything invalid (answers that don't fit their version, an unknown type code, a
  finding without `title`, …) → `400`, and nothing is created.
