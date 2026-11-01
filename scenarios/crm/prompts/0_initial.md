# Agency CRM — backend

We're building a lightweight CRM for small agencies in very different industries: real-estate
brokers, recruiters, event planners. Each agency keeps track of its contacts, the companies they
work for, and the deals it is working on. Agencies are small, but they use the CRM all day.

Build the backend API (no UI).

## Database
{database}

## Deployment
Our platform deploys the app by running `./start.sh` from the repository root, with `PORT`
and the database environment variables set. The app must serve the API on `PORT`.

## Agencies
Every record belongs to an agency, identified by an `agencyId` string that we assign (e.g.
`harbor-realty`). There is no endpoint for agencies. A record may only reference records of the
same agency, and lists only ever return records of the requested agency.

## Companies
`POST /companies`
```json
{ "agencyId": "harbor-realty", "name": "Sol Properties", "domain": "solproperties.es" }
```
- `name` is required; `domain` is optional.

→ `201` with the company, including a server-generated `id`.

`GET /companies/:id` → the company.
`GET /companies?agencyId=harbor-realty` → that agency's companies.

## Contacts
`POST /contacts`
```json
{
  "agencyId": "harbor-realty",
  "firstName": "María",
  "lastName": "García",
  "email": "maria@solproperties.es",
  "phone": "+34 600 111 222",
  "title": "Head of Acquisitions",
  "companyId": "<a company id>"
}
```
- `firstName` and `lastName` are required, non-empty strings. `email`, `phone`, `title` and
  `companyId` are optional.
- `email` must contain exactly one `@`, with text on both sides.
- `companyId` must be a company of the same agency.
- Invalid requests → `400` with an error message, and nothing is stored.

→ `201` with the contact, including `id`.

`GET /contacts/:id` →
```json
{
  "id": "…", "agencyId": "harbor-realty", "firstName": "María", "lastName": "García",
  "email": "maria@solproperties.es", "phone": "+34 600 111 222", "title": "Head of Acquisitions",
  "companyId": "…", "companyName": "Sol Properties"
}
```
Fields that aren't set may be omitted or `null`.

`PATCH /contacts/:id` with any of `firstName`, `lastName`, `email`, `phone`, `title`, `companyId`
changes only those fields (same validation; `null` clears an optional field) → `200` with the
contact.

`GET /contacts?agencyId=harbor-realty` → that agency's contacts.
- Filters: `email` (case-insensitive), `lastName`, `companyId`. Multiple filters are ANDed.
- `?limit=` (default 50, max 500).

## Deals
`POST /deals`
```json
{
  "agencyId": "harbor-realty", "contactId": "…", "title": "Harbour View apartments",
  "value": 250000, "stage": "proposal", "closeDate": "2026-11-30"
}
```
- `contactId` (a contact of the same agency), `title`, `value` (a number ≥ 0) and `stage` are
  required. Stages: `lead`, `qualified`, `proposal`, `won`, `lost`.
- `closeDate` (YYYY-MM-DD) is optional.
- Invalid requests → `400`, and nothing is stored.

→ `201` with the deal, including `id`.

`GET /deals/:id` →
```json
{
  "id": "…", "agencyId": "harbor-realty", "contactId": "…", "contactName": "María García",
  "companyId": "…", "companyName": "Sol Properties", "title": "Harbour View apartments",
  "value": 250000, "stage": "proposal", "closeDate": "2026-11-30"
}
```
`contactName` is the contact's first and last name; `companyId` and `companyName` are those of the
contact's company. They always reflect the contact and the company as they are now.

`PATCH /deals/:id` with any of `title`, `value`, `stage`, `closeDate` → `200` with the deal.

`GET /deals?agencyId=harbor-realty` → that agency's deals.
- Filters: `stage`, `contactId`, `companyId` (the contact's company). Multiple filters are ANDed.
- `?limit=` (default 50, max 500).
