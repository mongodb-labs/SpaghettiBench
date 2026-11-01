# Due dates by severity

Too many findings have no due date, so nobody feels responsible for them.

- A finding created without `dueDate` gets one from its severity: `critical` 1 day, `high` 7 days,
  `medium` 14 days, `low` 30 days after the date (UTC) of its `createdAt`. An explicit `dueDate`, and
  clearing it later with `null`, work as before.
- Existing findings without a due date get one the same way, from their `createdAt`.
- Findings show `overdue`: `true` when the finding isn't `resolved` and its `dueDate` is before today
  (UTC), otherwise `false`.
- `GET /findings`, the findings report and bulk actions accept the filter `overdue=true|false`.
