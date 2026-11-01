# Merging duplicate contacts

Agencies have many duplicate contacts: people entered twice by different colleagues, or created again
by an inbound lead with a new address. They need to merge them.

`POST /contacts/:id/merge`
```json
{ "duplicateId": "…" }
```
merges the duplicate into contact `:id` (the one that is kept) → `200` with the kept contact.

- Both must be contacts of the same agency, and different → `400` otherwise.
- The kept contact keeps its names, title and company. A title or company it doesn't have is taken
  from the duplicate.
- `emails`: the kept contact's, followed by the duplicate's addresses it doesn't have yet (compared
  case-insensitively). `phones` likewise. `custom`: the kept contact's values, plus the duplicate's
  values for keys the kept contact has no value for.
- All deals and leads of the duplicate now belong to the kept contact, everywhere they appear.
- The duplicate disappears from all lists and filters. `GET /contacts/<duplicate id>` returns the
  kept contact (with the kept contact's `id`), so old links keep working.
- A contact that has already been merged into another one can't take part in a merge again, on
  either side → `409`.
- `GET /contacts/:id/merges` → the merges into this contact, including the merges its duplicates
  had received themselves:
  ```json
  [
    { "duplicateId": "…", "mergedAt": "2026-11-26T09:12:00Z",
      "duplicate": { …the duplicate contact exactly as it was just before the merge… } }
  ]
  ```
- Several people clean up duplicates at the same time, sometimes the same ones. Concurrent merges
  must never lose a deal, a lead or a merge record, never leave one attached to a contact that was
  merged away, and never merge a contact twice.
