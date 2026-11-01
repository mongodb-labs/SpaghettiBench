# Pipeline reports

Agency owners want numbers rather than lists: how much is in the pipeline per stage, what was won
per month, and which kinds of clients and deals convert best, including by their own custom fields.

`GET /reports/deals?agencyId=harbor-realty&groupBy=<keys>`, plus any of the `GET /deals` filters
(`stage`, `contactId`, `companyId`, `custom.<key>`) to select which deals are included.

- `agencyId` and `groupBy` are required (`400` without them).
- `groupBy` is a comma-separated list of keys:
  - `stage`
  - `closeMonth`: the month of `closeDate`, formatted `YYYY-MM`
  - `companyId`: the company of the deal's contact
  - `custom.<key>`: a custom field of the deal
  - `contact.custom.<key>`: a custom field of the deal's contact

  An unknown key, or a custom field the agency hasn't defined → `400`.
- Deals without a value for a key are grouped under `null`.
- Reports always reflect the current data; e.g. when a contact moves to another company, its deals
  move with it.

Response, one entry per combination of values that has at least one deal:
```json
{
  "groups": [
    { "stage": "won", "closeMonth": "2026-11", "count": 4, "value": 1250000, "winRate": 1 }
  ]
}
```
- One property per `groupBy` key, named exactly like the key.
- `count`: the number of deals. `value`: the sum of their values.
- `winRate`: of the group's deals in stage `won` or `lost`, the share that is `won` (a number from 0
  to 1); `null` if the group has none.
