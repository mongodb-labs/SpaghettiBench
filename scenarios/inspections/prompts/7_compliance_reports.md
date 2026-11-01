# Compliance reports

Compliance managers need numbers rather than lists: how many inspections and checked items there
were, and what share failed, per site, per item type and per month, across all versions of a form
and across all of a company's forms.

## 1. Form report
`GET /forms/:formId/report?groupBy=<keys>&rate=<condition>`, plus any of the search filters.

- `groupBy` (required): a comma-separated list of keys. A key is an answer path
  (`answers.site_name`, `answers.floors.rooms.items.kind`), `month` (the UTC month of
  `submittedAt`, formatted `YYYY-MM`) or `formId`.
- **What is counted.** If an answer path in `groupBy` or `rate` goes into a group, the report counts
  the items of the deepest group those paths reference, e.g. every item in `floors.rooms.items`.
  Otherwise it counts submissions. All group paths used must lie along one chain of nested groups
  (`floors` and `floors.rooms.items` is fine; `floors` and `contacts` is not, `400`).
- A counted item's value for a path above its own level comes from its parent items and its
  submission: an item's `answers.site_name` is its submission's, its `answers.floors.floor_name` is
  its floor's. Missing values are grouped under `null`.
- `rate=<answer path>:<value>` (optional) adds the share of each group's counted units for which
  the path equals the value. The value is interpreted with the field type of each submission's own
  form version (`text` values match ignoring case, as in filters); a unit whose version doesn't have
  the field counts as not matching.
- Filters select which submissions are included; every counted unit of an included submission
  counts.
- An unknown `groupBy` key or a malformed `rate` is rejected with `400`.

Response, one entry per combination of group values that has at least one counted unit:
```json
{
  "groups": [
    { "answers.site_name": "Harbour Tower", "answers.floors.rooms.items.kind": "alarm",
      "month": "2026-10", "count": 12, "rate": 0.25 }
  ]
}
```
Each entry has one property per `groupBy` key, named exactly like the key, plus `count`, plus
`rate` (a number from 0 to 1) when `rate` was requested.

Example: `?groupBy=answers.site_name,answers.floors.rooms.items.kind,month&rate=answers.floors.rooms.items.status:fail`
gives the failure rate of each item kind per site per month.

## 2. Company report
`GET /reports?companyId=<id>&groupBy=…` takes the same parameters and reports across all of that
company's forms. For a form that doesn't have an answer path, the value is `null` for its
submissions, and it contributes no items for a group path it doesn't have. `companyId` is required
(`400` without it).
