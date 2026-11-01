# People at several companies, and renaming companies

Contacts often work with several companies at once (a broker who is also on the board of a
developer, a freelancer with two clients), and agencies want to record their role at each.
Companies also rebrand, and the new name must show everywhere.

## 1. Several companies per contact
Contacts get a list `companies`, in priority order (the first entry is the primary company):
```json
"companies": [
  { "companyId": "…", "companyName": "Sol Properties", "role": "Board member" },
  { "companyId": "…", "companyName": "Costa Homes", "role": null }
]
```
- `POST /contacts` and `PATCH /contacts/:id` accept `companies`, a list of `{ "companyId", "role" }`
  (`role` is optional). In a `PATCH` it replaces the whole list. Each company must belong to the
  agency and appear at most once → `400` otherwise.
- `POST /contacts/:id/companies` with `{ "companyId": "…", "role": "…" }` appends a company (`409`
  if the contact already has it) → `200` with the contact.
- `DELETE /contacts/:id/companies/:companyId` removes it → `200` with the contact (`404` if the
  contact doesn't have it).
- Responses always include `companies` (empty if there are none).
- Existing contacts: their company becomes the single entry, without a role.

## 2. Compatibility
- `companyId` and `companyName` in contact responses are those of the primary company.
- A request with `companyId` (instead of `companies`) sets the list to that single company, or to an
  empty list for `null`, as before. Both in one request → `400`.
- A deal's company stays the primary company of its contact.
- `GET /contacts?companyId=` now matches contacts that have the company in any position.
- Merging contacts: `companies` is the kept contact's list, followed by the duplicate's companies it
  doesn't have yet.

## 3. A company's contacts
`GET /companies/:id/contacts` → the contacts that have this company in any position, each with an
extra property `role`: their role at this company.

## 4. Renaming companies
`PATCH /companies/:id` with `name` and/or `domain` → `200` with the company. The new name shows
immediately wherever the company appears: contacts, deals and company lists.
