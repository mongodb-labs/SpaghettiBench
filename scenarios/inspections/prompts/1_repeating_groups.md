# Repeating groups in forms

Customers usually inspect several of the same thing in one visit: every fire extinguisher
in a building, every fridge in a kitchen. Right now they have to create a separate
submission for each one.

## 1. New field type: `group`
```json
{
  "key": "extinguishers", "label": "Extinguishers", "type": "group",
  "minItems": 1, "maxItems": 50,
  "fields": [
    { "key": "location", "label": "Location", "type": "text", "required": true },
    { "key": "expiry", "label": "Expiry", "type": "date" },
    { "key": "pressure_ok", "label": "Pressure OK", "type": "boolean" }
  ]
}
```
- Subfields can be any existing type except `group`.
- In a submission, a group's answer is an array of objects keyed by subfield key.
  Each item is validated against the subfields; `minItems`/`maxItems` are enforced.

Example submission:
```json
{
  "submittedBy": "worker-17",
  "submittedAt": "2026-10-02T09:00:00Z",
  "answers": {
    "site_name": "Depot 4",
    "extinguishers": [
      { "location": "Lobby", "expiry": "2027-03-01", "pressure_ok": true },
      { "location": "Kitchen", "expiry": "2026-11-15", "pressure_ok": false }
    ]
  }
}
```

## 2. Filtering inside groups
- `?answers.extinguishers.pressure_ok=false` matches submissions where at least one item matches.
- Multiple filters on the same group must be satisfied by the same item:
  `?answers.extinguishers.pressure_ok=false&answers.extinguishers.location=Lobby`
  matches only if a single extinguisher is both in the Lobby and has failed pressure.
- Filters on top-level fields work as before.

## 3. Convert Acme's "Fire Safety Check" form
Acme's form "Fire Safety Check" (companyId `acme`) currently has the flat fields
`extinguisher_location`, `extinguisher_expiry` and `pressure_ok`. Replace those three
fields with a repeating group `extinguishers` (subfields `location`, `expiry`,
`pressure_ok`), in the same position in the field list. All other fields stay unchanged,
and the form keeps its id.

- Every existing submission of this form must be converted: its three values become the
  single item of `extinguishers`. If all three were missing, `extinguishers` is an empty
  array (`minItems` does not apply to these existing submissions).
  All other answers are unchanged.
- Older tablets will keep submitting this form in the old flat shape for several weeks.
  Accept those submissions and store them in the new shape, as a one-item group.
- Manager dashboards still filter this form with the old keys
  (e.g. `?answers.pressure_ok=false`). These must keep working, matching against any extinguisher.
- `GET` endpoints return the new shape for this form.
