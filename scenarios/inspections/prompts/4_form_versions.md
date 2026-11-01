# Form versions

Companies revise their checklists regularly and want to edit their forms. Past inspections must
keep showing exactly what the worker saw and answered at the time.

## 1. Editing a form creates a new version
`PUT /forms/:id` with the full new definition, in the same shape as `POST /forms`:
- Creates a new version of the form; the form keeps its id. Returns `200` with the new version.
- Anything may change between versions: name, labels, options, required flags, min/max, adding and
  removing fields and group subfields, and a field's type.
- The definition is validated like in `POST /forms`; an invalid one is rejected with `400` and no
  version is created. `companyId` cannot change (`400`).
- Every form has an integer `version`. All existing forms are version 1; each edit adds 1. This
  includes Acme's "Fire Safety Check": its version 1 is the form as it is now, with the
  `extinguishers` group; the flat fields it had before are not a version.
- `GET /forms/:id` and `GET /forms?companyId=` return the latest version, including `version`.
- `GET /forms/:id/versions/:version` returns that version of the form; `404` if it doesn't exist.

## 2. Submissions belong to a version
- New submissions are validated against the latest version of the form, and record it as
  `formVersion`.
- All existing submissions belong to version 1.
- `GET /submissions/:id` and submission lists include `formVersion`. Answers are always returned
  exactly as submitted, even if later versions renamed, removed or retyped those fields.

## 3. Rendering a submission
`GET /submissions/:id/rendered` returns the submission laid out against its own form version, for
display and PDF export:
```json
{
  "id": "…", "formId": "…", "formVersion": 1, "formName": "Fire Safety Check",
  "submittedBy": "worker-17", "submittedAt": "2026-09-01T10:00:00Z",
  "fields": [
    { "key": "site_name", "label": "Site name", "type": "text", "value": "Depot 4" },
    { "key": "extinguishers", "label": "Extinguishers", "type": "group", "value": [
      { "id": "…", "fields": [
        { "key": "location", "label": "Location", "type": "text", "value": "Lobby" },
        { "key": "expiry", "label": "Expiry", "type": "date", "value": null },
        { "key": "pressure_ok", "label": "Pressure OK", "type": "boolean", "value": true }
      ]}
    ]},
    { "key": "condition", "label": "Condition", "type": "select", "value": null },
    { "key": "score", "label": "Score", "type": "number", "value": 6 }
  ]
}
```
- Every field of that version appears, in the version's order, with the version's label and type.
- Unanswered fields have `"value": null`. Group items appear in stored order, each with its `id`
  and its subfields rendered the same way.

## 4. Filtering across versions
`?answers.<path>=<value>` filters keep working across all versions of a form. Each submission is
matched using the field definitions of its own version: the value is interpreted with that
version's field type, and if that version has no such field, or the value doesn't fit that
version's type, the submission doesn't match. For example, if `score` was a number in version 1 and
a select with `low`/`medium`/`high` in version 2, `?answers.score=6` matches only version-1
submissions and `?answers.score=high` only version-2 submissions.
