# Equipment types

Companies want to classify inspected equipment with their own catalogue of types (e.g. "6 kg CO2
extinguisher", inspected every 180 days), and see when each item is due for its next inspection.

## 1. The catalogue
`POST /companies/:companyId/item-types`
```json
{ "code": "EXT-CO2-6", "name": "6 kg CO2 extinguisher", "intervalDays": 180 }
```
- `code`: capital letters, digits and `-`; unique within the company (`409`). `name` is required;
  `intervalDays` is an integer ≥ 1. Invalid → `400`.
- → `201` with the type.

`GET /companies/:companyId/item-types` → the company's types.
`PATCH /companies/:companyId/item-types/:code` with `name` and/or `intervalDays` → `200`.
`DELETE /companies/:companyId/item-types/:code` → `204`; `409` while any item has this type.

## 2. Typed items
- `PUT /submissions/:id/items/:itemId/type` with `{ "code": "EXT-CO2-6" }` gives an item a type of
  its company (`400` for an unknown code), `{ "code": null }` removes it → `200` with the item.
- Items show `type` (`{ "code", "name", "intervalDays" }`, or `null`) and `nextInspectionDue`: the
  date (UTC) of the submission's `submittedAt` plus the type's `intervalDays`, `null` without a type.
- Finding search entries show the item's type as `itemType` (`{ "code", "name" }` or `null`), and
  accept a `typeCode` filter.
- Type changes show everywhere at once: renaming a type, or changing its interval, changes every
  item and finding that refers to it.
