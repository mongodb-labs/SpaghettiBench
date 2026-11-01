# Several email addresses and phone numbers per contact

Many contacts have a work and a private address, or a desk phone and a mobile. Today agencies
create a second contact for the same person to store them.

## 1. `emails` and `phones`
Contacts get two lists, in priority order (the first entry is the primary one):
```json
"emails": [
  { "address": "maria@solproperties.es", "label": "work" },
  { "address": "maria.garcia@gmail.com", "label": "home" }
],
"phones": [ { "number": "+34 600 111 222", "label": "mobile" } ]
```
- Labels: `work`, `home`, `mobile`, `other`. Every address is validated like `email` today.
- `POST /contacts` and `PATCH /contacts/:id` accept `emails` and `phones`. In a `PATCH` they
  replace the whole list.
- Responses always include both lists (empty if there are none).

## 2. Existing contacts
Convert all existing contacts: an `email` becomes the single entry of `emails`, a `phone` the single
entry of `phones`, both with label `work`. Contacts without one get an empty list.

## 3. Older clients
Our web app and several agencies' integrations still use `email` and `phone`, and will for months.
- Responses keep `email` and `phone`, set to the address and number of the primary entry (omitted or
  `null` if the list is empty).
- A request with `email` or `phone` (instead of the lists) sets the primary entry: it replaces the
  address or number of the first entry, keeping its label, or adds an entry with label `work` if the
  list is empty. `null` removes the primary entry.
- A request with both `email` and `emails` (or `phone` and `phones`) → `400`.

## 4. Filters
- `?email=` matches contacts that have the address in any position, case-insensitively.
- New: `?phone=` matches contacts that have the number in any position.
