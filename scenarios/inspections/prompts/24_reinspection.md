# Re-inspections

Most sites are inspected every few months, and inspectors start from the last inspection: the same
floors, rooms and equipment, with the problems that are still open.

`POST /submissions/:id/reinspect` with `{ "submittedBy": "k.mensah", "submittedAt": "2027-08-04T09:00:00Z" }`
→ `201` with the new submission's document (as in `GET /submissions/:id/document`). Both fields are
required (`400`).
- Same form and form version, and a copy of all the answers with new ids for every item. Each item
  keeps its type.
- Every finding that isn't `resolved` is copied to the matching new item: same `title`, `severity`,
  `assignee` and `dueDate`; `status` `open`; a new `id` and `createdAt`; no comments; and
  `carriedOverFrom` with the original finding's id. Other findings show `carriedOverFrom: null`.
- New items show `previousItemId` (the item they were copied from) in the item endpoints and the
  document's `items`, and the new document shows `previousSubmissionId`; both are `null` otherwise.
- Item revisions and `docRev` start at 1. Each new item's history starts with `created`, followed by
  `finding_added` for each carried-over finding.
- The original submission doesn't change.
- Exports and imports don't keep these links (`null` after an import).
