# Findings

When an inspection finds a problem, managers need to follow it up until it's fixed: who is
responsible, by when, and what has been done.

## 1. Findings on items
Any group item, at any depth, can have findings.

`POST /submissions/:id/items/:itemId/findings`
```json
{ "title": "Sounder dead", "severity": "high", "assignee": "worker-12", "dueDate": "2027-03-01" }
```
- `title` (non-empty) and `severity` (`low`, `medium`, `high`, `critical`) are required; `assignee`
  (a string) and `dueDate` (`YYYY-MM-DD`) are optional. Invalid → `400`; unknown submission or item
  → `404`.
- → `201` with the finding:
  ```json
  { "id": "…", "title": "Sounder dead", "severity": "high", "status": "open", "assignee": "worker-12",
    "dueDate": "2027-03-01", "createdAt": "2027-02-17T09:30:00Z", "comments": [] }
  ```

`PATCH /submissions/:id/items/:itemId/findings/:findingId` with any of `title`, `severity`,
`status` (`open`, `in_progress`, `resolved`), `assignee` and `dueDate` (`null` clears `assignee` or
`dueDate`) → `200` with the finding. Same validation.

`POST /submissions/:id/items/:itemId/findings/:findingId/comments`
```json
{ "author": "worker-12", "text": "Replaced the sounder, needs retest" }
```
→ `201` with the comment, including `id` and `createdAt`. A finding's `comments` are listed oldest
first. Technicians often add comments from several devices at the same moment; none may be lost.

## 2. Where findings appear
- `GET /submissions/:id/items/:itemId` includes `findings` (each with its comments; `[]` if none).
- Findings are not answers: `GET /submissions/:id` and its `answers` don't change.
- Removing an item removes its findings.

## 3. Tidewater's open issues
Tidewater Properties (companyId `tidewater-properties`) wants every failed item in their existing
"Building Safety Walk" inspections turned into a finding. Every item with `status` `fail` gets one
open finding:
- `title`: the item's `kind` followed by ` failed`, e.g. `alarm failed`
- `severity`: `high` for kind `alarm`, otherwise `medium`
- no `assignee`
- `dueDate`: 14 days after the date (UTC) of the submission's `submittedAt`
