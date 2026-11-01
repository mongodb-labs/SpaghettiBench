# Equipment details

Each kind of equipment needs its own details: a fire door has hinges and a closer, a lift has cars
with landing doors. Companies want to define these per equipment type.

## 1. Fields per type
Item types get `fields`, defined like form fields (types, `required`, `options`, `min`/`max`,
groups with `minItems`/`maxItems`), with groups at most two levels deep. `POST` and `PATCH` of item
types accept them; invalid → `400`. Types without fields have `fields: []`.
```json
{ "code": "FIRE-DOOR", "name": "Fire door", "intervalDays": 180, "fields": [
  { "key": "rating", "label": "Rating", "type": "select", "options": ["30", "60", "90"], "required": true },
  { "key": "hinges", "label": "Hinges", "type": "group", "maxItems": 6, "fields": [
    { "key": "position", "label": "Position", "type": "select", "options": ["top", "middle", "bottom"] },
    { "key": "ok", "label": "OK", "type": "boolean", "required": true } ] } ] }
```

## 2. Details on items
- Items show `details`: the answers to their type's fields (`{}` without a type or details). Group
  items inside get ids, as in submissions.
- `PUT /submissions/:id/items/:itemId/type` accepts `details` next to `code` and replaces them (`{}`
  if left out). `PATCH /submissions/:id/items/:itemId/details` changes some of them like an item
  `PATCH` (`null` removes a field, a group is replaced as a whole); `400` for an item without a type.
- Details are validated against the type's current fields → `400`, and nothing changes. Changing a
  type's fields affects later writes only; stored details stay as they are.
- Details are part of the item everywhere: the document's `items` entries (`details`; uploads keep
  them unchanged), `docRev` and kept versions, restores, exports and imports (with the types'
  fields), and re-inspections copy them.

## 3. Search
`GET /items?companyId=<id>&typeCode=FIRE-DOOR&details.hinges.ok=false` → `{ "items": [ … ] }`: every
item of the company with that type whose details match, each as in
`GET /submissions/:id/items/:itemId`. `companyId` and `typeCode` are required (`400`). Filters on
`details.<path>` work like submission filters on `answers.<path>`: the same operators, text
ignoring case, and conditions on the same group must hold on the same group item.
