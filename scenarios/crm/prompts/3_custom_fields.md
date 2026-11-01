# Custom fields

Every agency tracks different things: a recruiter needs a candidate's seniority and notice period, a
broker a buyer's budget, an event planner a client's preferred venue. Agencies should be able to add
their own fields to contacts and deals.

## 1. Field definitions
`POST /agencies/:agencyId/fields`
```json
{ "entity": "contact", "key": "budget", "label": "Budget (EUR)", "type": "number" }
```
- `entity`: `contact` or `deal`.
- `type`: `text`, `number`, `boolean`, `date` (YYYY-MM-DD) or `select` (with `options`, a non-empty
  list of strings).
- `key`: letters, digits and `_`. Unique per agency and entity (`409` if it already exists).
- Invalid definitions → `400`.

→ `201` with the definition.

`GET /agencies/:agencyId/fields` → the agency's definitions (`?entity=contact` for one entity).

## 2. Values
- Contacts and deals get a `custom` object, e.g. `{ "budget": 450000, "seniority": "senior" }`.
- `POST` accepts `custom`. Values are validated against the agency's definitions for that entity:
  the right type, select values among the options, and no keys that aren't defined → `400`
  otherwise.
- `PATCH` with `custom` sets the given keys and leaves the others unchanged; `null` removes a value.
- Responses include `custom` (`{}` if there are no values).

## 3. Filters
`GET /contacts` and `GET /deals` accept `?custom.<key>=<value>`, interpreted with the field's type
(e.g. `custom.budget=450000` matches `450000` and `450000.0`; booleans are `true`/`false`). They
combine with the existing filters. A filter on a key the agency hasn't defined → `400`.

## 4. Seniority for Talentbridge
Talentbridge (`agencyId` `talentbridge`) wants a contact field `seniority` (label "Seniority", type
`select`, options `junior`, `mid`, `senior`, `lead`), filled in for all its existing contacts from
their job `title`, matching whole words case-insensitively:
- `lead`, `head` or `principal` → `lead`
- otherwise `senior` or `sr.` → `senior`
- otherwise `junior` or `jr.` → `junior`
- otherwise, if the contact has a title → `mid`

Contacts without a title get no value. Create the definition and fill in the values yourself.
