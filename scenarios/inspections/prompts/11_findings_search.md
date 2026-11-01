# Finding search

Managers want one list of all findings across their company's inspections, to see what is still
open and what is due soon.

`GET /findings?companyId=<id>` → that company's findings across all its forms and submissions:
```json
{
  "items": [
    { "id": "…", "title": "Sounder dead", "severity": "high", "status": "open",
      "assignee": "worker-12", "dueDate": "2027-03-01", "createdAt": "2027-02-17T09:30:00Z",
      "submissionId": "…", "formId": "…", "itemId": "i-7", "group": "items",
      "path": [ { "group": "floors", "id": "f-1" }, { "group": "rooms", "id": "r-3" } ] }
  ],
  "nextCursor": "…"
}
```
- Each entry is the finding without its comments, plus where it is: `submissionId`, `formId`,
  `itemId`, and the item's `group` and `path` (as in `GET /submissions/:id/items/:itemId`).
- `companyId` is required (`400` without it).
- Filters, ANDed:
  - `status` and `severity`: one value or a comma-separated list
  - `assignee`, `formId`, `submissionId`
  - `dueBefore=<YYYY-MM-DD>`: due before that date (findings without a due date don't match)
- Order: by `dueDate`, earliest first, findings without a due date last; then by `createdAt`, then
  by `id`.
- Paging: `?limit=` (default 50, max 200). `nextCursor` is `null` on the last page; passing it as
  `?cursor=` (with the same filters) returns the next page. Walking all pages returns every
  matching finding exactly once.
