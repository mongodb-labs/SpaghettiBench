# Advanced search

Managers want to find inspections by ranges ("chlorine below 1", "expiring before December") and
across all of their company's forms, not only by exact values within one form.

## 1. Filter operators
Answer filters accept an operator in brackets after the path:
`?answers.<path>[<op>]=<value>`. Without an operator, `eq` applies, exactly as today.

| Operator | Meaning | Field types |
|---|---|---|
| `eq` | equal (the default); for `text` ignoring case, as today | all |
| `gt`, `gte`, `lt`, `lte` | greater than / at least / less than / at most | `number`, `date` |
| `in` | equal to any value in a comma-separated list; for `text` ignoring case | `text`, `select`, `number` |
| `contains` | contains the value as a substring, case-insensitive | `text` |

- Several filters on the same path combine with AND, e.g.
  `?answers.inspected_on[gte]=2026-11-01&answers.inspected_on[lt]=2026-12-01`.
- The same-item rule for group paths applies to every operator:
  `?answers.checks.item=pH&answers.checks.reading[gt]=7.5` requires a single check item that is
  `pH` *and* above 7.5.
- As before, each submission is matched using its own form version: if that version has no such
  field, or the operator or value doesn't fit that version's field type, the submission doesn't
  match.
- An unknown operator is rejected with `400`.
- `?submittedAt[gt|gte|lt|lte]=<ISO 8601 timestamp>` filters on the submission time.

## 2. Company-wide search
`GET /submissions?companyId=<id>` searches the submissions of all of that company's forms, with
the same filters and operators, `?limit=`, and ordering (newest `submittedAt` first).
- Each submission is matched using its own form and form version; submissions whose form has no
  such field don't match.
- Only that company's submissions are returned. `companyId` is required (`400` without it).
- Responses have the same shape as `GET /forms/:formId/submissions` (each submission includes its
  `formId`).
