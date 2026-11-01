# Reports in local time

Our customers work in different timezones. Report months are currently UTC months, so an inspection
done on the evening of the last day of a month can count towards the next one.

- `GET /forms/:formId/report` and `GET /reports` accept `?timezone=<IANA timezone name>`, e.g.
  `Europe/Berlin` or `America/New_York`. `month` is then the month of `submittedAt` in that timezone,
  including daylight saving time.
- Without `timezone`, months stay UTC.
- An unknown timezone → `400`.
- Nothing else about reports changes; `submittedAt` filters still take ISO 8601 timestamps.
