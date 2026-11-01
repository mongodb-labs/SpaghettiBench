# Bulk actions on findings

When an inspector leaves or a contractor finishes a batch of repairs, managers want to change many
findings at once instead of one by one.

`POST /findings/bulk`
```json
{ "companyId": "tidewater-properties",
  "filter": { "status": "open,in_progress", "assignee": "worker-12" },
  "set": { "assignee": "worker-31" } }
```
- `filter` takes the same filters as `GET /findings` (`status`, `severity`, `assignee`, `formId`,
  `submissionId`, `dueBefore`), written as a JSON object; an empty filter matches all of the
  company's findings.
- `set` may change `status`, `assignee` and `dueDate`, validated as in `PATCH` (`null` clears
  `assignee` or `dueDate`). An empty or invalid `set`, or a missing `companyId` → `400`.
- Every matching finding gets the new values → `200` with `{ "matched": <number of findings> }`.
- With `X-User-Id`, only managers may do this (`403` for inspectors).
- Bulk actions run while technicians keep working: a comment added or an item edited while a bulk
  action runs must not be lost, and every matched finding must end up with the new values.
