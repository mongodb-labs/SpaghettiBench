# Item history

Auditors ask who changed an inspection item, and when. We need a history of every change to an item
and its findings.

`GET /submissions/:id/items/:itemId/history` → the item's history, oldest first:
```json
[
  { "at": "2027-05-12T08:00:00Z", "by": "u-17", "action": "created" },
  { "at": "2027-05-12T09:10:00Z", "by": "u-17", "action": "updated",
    "before": { "status": "fail" }, "after": { "status": "pass" } },
  { "at": "2027-05-12T09:12:00Z", "by": null, "action": "finding_added", "findingId": "…" }
]
```
- `by` is the `X-User-Id` of the request, or `null` without one.
- Actions, recorded from now on:
  - `created`: the item was created, in a new submission or appended
  - `updated`: a `PATCH` or sync edit; `before` and `after` hold the values of the fields it changed
  - `moved`: with `fromPath` and `toPath`
  - `deleted`
  - `finding_added`, `finding_updated` (with `findingId`, and `before` and `after` of the changed
    finding fields; also for bulk actions), `comment_added` (with `findingId` and `commentId`)
- The history of a deleted item can still be read. Items that existed before this change start with
  an empty history.
- Every change is recorded exactly once, also when many changes happen at the same time.
