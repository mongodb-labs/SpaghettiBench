# Field Inspections — backend

We're building a SaaS product for companies whose field workers carry out inspections
on tablets: fire safety, facility maintenance, food hygiene, equipment checks, and so on.
Our customers work in very different industries and every company's checklists are
different. Companies design their own checklists and revise them regularly as regulations
and internal processes change. Workers fill in inspections on site; managers review them
and look up inspections by their answers.

Build the backend API (no UI).

## Database
{database}

## Deployment
Our platform deploys the app by running `./start.sh` from the repository root, with `PORT`
and the database environment variables set. The app must serve the API on `PORT`.

## Forms
A company defines a form as an ordered list of fields. Field types:
`text`, `number` (optional `min`/`max`), `boolean`, `date` (YYYY-MM-DD),
`select` (with `options`).

`POST /forms`
```json
{
  "companyId": "acme",
  "name": "Fire Safety Check",
  "fields": [
    { "key": "site_name", "label": "Site name", "type": "text", "required": true },
    { "key": "extinguisher_location", "label": "Extinguisher location", "type": "text" },
    { "key": "extinguisher_expiry", "label": "Expiry date", "type": "date" },
    { "key": "pressure_ok", "label": "Pressure OK", "type": "boolean" },
    { "key": "condition", "label": "Condition", "type": "select", "options": ["good", "fair", "poor"] },
    { "key": "score", "label": "Score", "type": "number", "min": 0, "max": 10 }
  ]
}
```
→ `201` with the form, including a server-generated `id`.

`GET /forms/:id` → the form.
`GET /forms?companyId=acme` → list of that company's forms.

## Submissions
`POST /forms/:formId/submissions`
```json
{
  "submittedBy": "worker-17",
  "submittedAt": "2026-09-01T10:00:00Z",
  "answers": { "site_name": "Depot 4", "pressure_ok": false, "condition": "fair", "score": 6 }
}
```
- `submittedAt` is set by the tablet, not the server.
- Validate against the form: required fields present, values of the right type,
  numbers within min/max, select values among the options, no unknown keys.
  Invalid submissions → `400` with an error message.
- Unanswered optional fields may be omitted.

→ `201` with the submission, including `id` and `formId`.

`GET /submissions/:id` → `{ id, formId, submittedBy, submittedAt, answers }`,
with `answers` exactly as submitted.

`GET /forms/:formId/submissions` → list of submissions, newest `submittedAt` first.
- Filter by answer values with `?answers.<key>=<value>`, e.g.
  `?answers.condition=poor&answers.pressure_ok=false`.
- Values are interpreted according to the field's type; multiple filters are ANDed.
- `?limit=` (default 50, max 500).
