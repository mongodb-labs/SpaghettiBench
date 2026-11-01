# Follow-up dates

Deals in progress slip because nobody follows up.

- Deals get `followUpOn` (`YYYY-MM-DD` or `null`). When a deal is created in an open stage, or moves
  to another open stage, it is set to that day (UTC) plus: `lead` 2 days, `qualified` 5, `proposal` 3.
  When a deal is won or lost it becomes `null`.
- `POST` and `PATCH` accept `followUpOn` (a date, or `null` to clear it). An explicit value wins over
  the default, also in the same request as a stage change.
- Existing open deals get a follow-up the same way, counted from the day you apply the change;
  existing won and lost deals have none.
- Deals show `followUpOverdue`: `true` when the deal is open and `followUpOn` is before today (UTC),
  otherwise `false`.
- `GET /deals` accepts `followUpBefore=YYYY-MM-DD` (deals with `followUpOn` before that date) and
  `overdue=true|false`.
- Contacts get `nextFollowUpOn`: the earliest `followUpOn` of their open deals, or `null`.
