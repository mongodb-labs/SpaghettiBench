# Findings dashboard

Managers want numbers on findings: how many are open or overdue, per person, severity and month, and
how long it takes to resolve them.

## 1. When findings are resolved
Findings get `resolvedAt`, shown wherever findings are returned: set when the status changes to
`resolved` (also by bulk actions), and cleared (`null`) when it changes back. Findings resolved before
this change keep `resolvedAt` `null`.

## 2. Report
`GET /reports/findings?companyId=<id>&groupBy=<keys>`, plus any of the `GET /findings` filters.
- `companyId` and `groupBy` are required (`400`). Keys: `severity`, `status`, `assignee`, `formId`,
  `typeCode` (the item's type), and `month` (of `createdAt`, UTC, `YYYY-MM`). Unknown key → `400`.
- One entry per combination of values with at least one finding; findings without a value for a key
  are grouped under `null`:
  ```json
  { "groups": [
    { "severity": "high", "assignee": "worker-12", "count": 7, "open": 4, "overdue": 2,
      "avgDaysToResolve": 3.5 }
  ] }
  ```
- `count`: findings; `open`: those not `resolved`; `overdue`: those not `resolved` with a `dueDate`
  before today (UTC); `avgDaysToResolve`: the average of `resolvedAt` − `createdAt` in days, over
  resolved findings that have a `resolvedAt`, rounded to 2 decimals; `null` if there are none.
