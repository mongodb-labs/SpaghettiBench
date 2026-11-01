# Moving items

Inspectors sometimes record a room on the wrong floor, or an item in the wrong room, and want to
move it instead of deleting and re-entering everything.

`POST /submissions/:id/items/:itemId/move`
```json
{ "path": [ { "group": "floors", "id": "f-2" } ], "group": "rooms", "position": 0 }
```
- Moves the item, with everything nested in it and all its findings, under another parent in the
  same submission. `path` and `group` give the target group, like in `POST /submissions/:id/items`.
- The target group must be the same field as the item's current group (a room can only go into
  another floor's `rooms`) → `400` otherwise. Moving an item into itself or into one of its own
  nested items → `400`. An unknown path → `404`.
- `position` (optional): the index in the target group; without it, the item goes last.
- The target group's `maxItems` and the source group's `minItems` apply (`409`).
- Ids don't change. The item's `path` changes everywhere it appears, including finding search.
- → `200` with the moved item, as in `GET /submissions/:id/items/:itemId`.
- Moves happen while others edit the same submission: no item, answer or finding may be lost or
  duplicated.
