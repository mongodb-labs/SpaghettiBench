# Attachments

Inspectors attach evidence to items: photos with marked spots, sensor readings, and documents.

`POST /submissions/:id/items/:itemId/attachments` with one of
```json
{ "kind": "photo", "url": "https://…", "takenAt": "2027-08-18T10:02:00Z",
  "marks": [ { "x": 0.4, "y": 0.7, "label": "rust" } ] }
{ "kind": "reading", "sensor": "co", "unit": "ppm", "values": [ { "at": "2027-08-18T10:05:00Z", "value": 12.5 } ] }
{ "kind": "document", "url": "https://…", "title": "Service report", "pages": 3 }
```
→ `201` with the attachment, including `id` and `createdAt`.
- `photo`: `url` and `takenAt` required; `marks` optional, each with `x` and `y` between 0 and 1 and
  a non-empty `label`.
- `reading`: `sensor`, `unit` and at least one value, each with `at` and a number `value`.
- `document`: `url`, `title` and `pages` (an integer ≥ 1).
- An unknown `kind`, missing or invalid fields, or fields that don't belong to the kind → `400`.

`DELETE /submissions/:id/items/:itemId/attachments/:attachmentId` → `204`.

- Items show `attachments`, oldest first, in the item endpoints and the document's `items` entries.
  They are part of the item like findings: `docRev` and kept versions, restores, exports and imports
  (with new ids); uploads keep them unchanged; re-inspections don't copy them.
- `GET /attachments?companyId=<id>` → `{ "items": [ … ] }`: the company's attachments, each with
  `submissionId` and `itemId`. Filters: `kind`, `submissionId`, `mark` (photos with a mark of that
  label, ignoring case), `sensor`, and `above` (readings with a value above that number).
