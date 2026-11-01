# Offline edits

Inspectors edit items on tablets without connectivity and sync later. Two inspectors sometimes
change the same item while offline; the second sync must not silently overwrite the first.

## 1. Item revisions
- Every item gets `rev`, an integer, in `GET /submissions/:id/items/:itemId` and wherever items are
  returned by the item endpoints. It is 1 for a new item, and every `PATCH` or move of the item adds
  1. Existing items start at 1.
- `PATCH /submissions/:id/items/:itemId?ifRev=<n>` applies the change only if the item's `rev` is
  still `n`; otherwise → `409` with the current item (as in `GET`), and nothing changes. Without
  `ifRev`, `PATCH` works as before.
- When several requests with the same `ifRev` arrive at the same time, exactly one succeeds.

## 2. Sync
`POST /submissions/:id/items/sync`
```json
{ "edits": [
  { "itemId": "i-7", "ifRev": 3, "answers": { "status": "pass" } },
  { "itemId": "i-9", "ifRev": 1, "answers": { "notes": "Cover replaced" } }
] }
```
- Up to 100 edits; each is handled like a `PATCH` with `ifRev`, independently of the others.
- → `200` with `{ "results": [ … ] }`, one per edit in the same order:
  `{ "itemId": "i-7", "status": 200, "item": { … } }`, or `"status": 409` with the current `item`,
  or `"status": 400 | 404` with an `error`.
