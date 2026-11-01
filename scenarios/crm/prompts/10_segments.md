# Tags and saved segments

Agencies want to group contacts their own way ("VIP", "golf day 2027") and save lists like "hot
buyers in Lisbon with a big open pipeline" that always show the current contacts.

## 1. Tags
- Contacts get `tags`, a list of distinct, non-empty strings of at most 40 characters. Tags ignore
  case: they are returned in lower case.
- `POST /contacts` and `PATCH /contacts/:id` accept `tags` (a `PATCH` replaces the list). Responses
  always include `tags` (`[]` if none).
- `GET /contacts?tag=vip` → contacts with that tag (ignoring case); combines with the other filters.
- Merging contacts: the kept contact's tags, followed by the duplicate's it doesn't have.

## 2. Segments
`POST /agencies/:agencyId/segments`
```json
{ "name": "Hot buyers",
  "filter": { "all": [
    { "field": "tags", "op": "has", "value": "buyer" },
    { "any": [ { "field": "openPipeline", "op": "gte", "value": 250000 },
               { "field": "custom.budget", "op": "gt", "value": 400000 } ] },
    { "not": { "field": "lastName", "op": "eq", "value": "Smith" } } ] } }
```
A filter is a condition: `{ "field", "op", "value" }`, or `{ "all": [conditions] }` or
`{ "any": [conditions] }` (non-empty lists), or `{ "not": condition }`, nested to any depth.

| Field | Operators |
|---|---|
| `tags` | `has` (ignoring case) |
| `firstName`, `lastName`, `title`, `email` (any of the contact's addresses) | `eq`, `contains` (both ignoring case) |
| `companyId` (any of the contact's companies) | `eq` |
| `custom.<key>` (a contact field of the agency) | `eq`; also `gt`, `gte`, `lt`, `lte` for number and date fields; `exists` (`true`/`false`) |
| `openPipeline` | `eq`, `gt`, `gte`, `lt`, `lte` |
| `lastActivityAt` | `before`, `after` (timestamps; a contact without activities matches neither) |

- A value must fit the field (a number for `openPipeline`, an option for a select field, …). An unknown
  field or operator, or a wrong value → `400`.
- → `201` with the segment, including `id`.

`GET /agencies/:agencyId/segments`, `GET /segments/:id`, `PATCH /segments/:id` (`name` and/or `filter`,
same validation), `DELETE /segments/:id` (`204`).

`GET /segments/:id/contacts` → the contacts that match now, as in `GET /contacts`, ordered by
`lastName`, then `firstName`; `?limit=` as there. `GET /segments/:id/count` → `{ "count": 12 }`.
