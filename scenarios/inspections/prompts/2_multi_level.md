# Multi-level site inspections

Large-site customers organise inspections by floor and room, and several workers inspect the same
site at the same time.

## 1. Groups inside groups
Groups may contain groups, up to 4 levels deep. Validation applies at every level.
```json
{ "key": "floors", "label": "Floors", "type": "group", "fields": [
  { "key": "floor_name", "label": "Floor", "type": "text", "required": true },
  { "key": "rooms", "label": "Rooms", "type": "group", "fields": [
    { "key": "room_name", "label": "Room", "type": "text", "required": true },
    { "key": "items", "label": "Items", "type": "group", "fields": [
      { "key": "kind", "label": "Kind", "type": "select", "options": ["extinguisher", "alarm", "exit_sign"] },
      { "key": "status", "label": "Status", "type": "select", "options": ["pass", "fail"] },
      { "key": "notes", "label": "Notes", "type": "text" }
    ]}
  ]}
]}
```

## 2. Stable item ids
Every group item, at any level, has a string `id`.
- Clients may supply an `id`; otherwise the server generates one.
- Group items in existing submissions (including Acme's "Fire Safety Check" submissions) must be
  given ids. Once assigned, an id never changes.
- Responses include `id` on every group item. This is the only change to existing response shapes.

## 3. Filtering at any depth
- Dotted paths work at any depth: `?answers.floors.rooms.items.status=fail` matches submissions
  where some item in some room on some floor failed.
- Filters that share a group path must be satisfied by the same item of the deepest group they
  share. For example:
  - `?answers.floors.rooms.items.status=fail&answers.floors.rooms.items.kind=alarm` requires a
    single failed alarm.
  - `?answers.floors.floor_name=Ground&answers.floors.rooms.items.status=fail` requires a failed
    item on the Ground floor.

## 4. Adding items to an existing submission
`POST /submissions/:id/items`
```json
{
  "path": [ { "group": "floors", "id": "f-1" }, { "group": "rooms", "id": "r-3" } ],
  "group": "items",
  "item": { "kind": "alarm", "status": "fail" }
}
```
- Appends the item to the `items` group of room `r-3` on floor `f-1`. An empty `path` appends to a
  top-level group.
- The item is validated against the form (including `maxItems`). `404` if the path doesn't exist.
- Returns `201` with the stored item, including its `id`.
- Many workers call this at the same time on the same submission, including on the same room. No
  appended item may ever be lost or duplicated.
