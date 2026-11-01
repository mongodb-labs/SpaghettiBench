# Working with single items

Inspectors want to correct single entries on site: fix a room name, mark an item as passed once it
has been repaired, remove an entry made by mistake. Today they have to resubmit the whole
inspection.

## 1. Reading an item
`GET /submissions/:id/items/:itemId` returns any group item of a submission, at any depth:
```json
{
  "id": "i-7", "submissionId": "…", "group": "items",
  "path": [ { "group": "floors", "id": "f-1" }, { "group": "rooms", "id": "r-3" } ],
  "answers": { "kind": "alarm", "status": "fail", "notes": "Sounder dead" }
}
```
- `group` is the item's own group; `path` lists its parent items from the top, like the `path` of
  `POST /submissions/:id/items` (empty for an item of a top-level group).
- `answers` holds the item's answers, including its nested groups with their items' ids, but not
  its own `id`.
- `404` if the submission or the item doesn't exist.

## 2. Changing an item
`PATCH /submissions/:id/items/:itemId` with some of the item's answers:
```json
{ "status": "pass", "notes": null }
```
- Sets the given fields; `null` removes an optional field; fields that aren't mentioned stay as they
  are.
- Nested groups can't be changed this way (`400`); use the item endpoints for their items.
- The item must still be valid for the submission's own form version (types, options, min/max,
  required fields, no unknown keys) → `400` otherwise, and nothing changes.
- → `200` with the item, as in `GET`.

## 3. Removing an item
`DELETE /submissions/:id/items/:itemId` → `204`. Removes the item with everything nested in it.
`409` if that would leave its group with fewer items than the group's `minItems`.

## 4. Consistency
- Every other endpoint sees the change: the submission, its rendered view, filters and reports.
- Several people often edit the same submission at the same time, also items in the same room:
  changes to different items, and items appended meanwhile, must never overwrite or lose each
  other.
