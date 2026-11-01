# Inbound leads from web forms

Agencies collect leads through two form providers, FormFlow and LeadPipe. Today someone copies every
lead into the CRM by hand. The providers will now send them to us as webhooks.

## 1. Webhooks
`POST /webhooks/formflow`, for example:
```json
{
  "event": "lead.created",
  "id": "ff_8f2k1",
  "agency": "harbor-realty",
  "submitted": "2026-10-03T10:00:00Z",
  "fields": {
    "name": "María García",
    "email": "maria@solproperties.es",
    "phone": "+34 600 111 222",
    "company": "Sol Properties",
    "message": "Interested in the Harbour View flats"
  },
  "tracking": { "utm_source": "google", "utm_campaign": "spring-2027" }
}
```

`POST /webhooks/leadpipe` receives two payload versions. LeadPipe is moving its customers to
version 2 over the next months, so both arrive at the same URL.

Version 1:
```json
{
  "type": "new_lead",
  "lead_id": 99812,
  "account": "harbor-realty",
  "created_at": 1791021600,
  "person": {
    "first_name": "Tom", "last_name": "Becker",
    "emails": ["tom@becker.de"], "phones": ["+49 171 555 0101"]
  },
  "organization": { "name": "Becker Bau" },
  "answers": [ { "question": "Budget", "answer": "400k-600k" } ]
}
```
Version 2 (has `"version": 2`):
```json
{
  "version": 2,
  "event": "lead.new",
  "lead": {
    "id": "lp2_77f1c",
    "account_id": "harbor-realty",
    "created": "2026-10-05T08:30:00Z",
    "contact": {
      "name": { "first": "Tom", "last": "Becker" },
      "email_addresses": [ { "value": "tom@becker.de", "primary": true } ],
      "phone_numbers": [ { "value": "+49 171 555 0101" } ]
    },
    "organization": { "name": "Becker Bau" },
    "custom_answers": { "budget": "400k-600k" }
  }
}
```

- Each lead event creates a lead → `201` with the lead.
- Other events (FormFlow `event` other than `lead.created`, LeadPipe v1 `type` other than
  `new_lead`, v2 `event` other than `lead.new`) → `204`, nothing stored.
- A lead event without the provider's lead id or the agency → `400`, nothing stored.
- Providers retry deliveries, sometimes several at the same time. A lead with the same provider and
  lead id as an existing one → `200` with the existing lead, and nothing else changes.

## 2. Leads
`GET /leads/:id` →
```json
{
  "id": "…", "agencyId": "harbor-realty", "provider": "formflow", "externalId": "ff_8f2k1",
  "contactId": "…", "payload": { …the webhook body exactly as received… }
}
```
`externalId` is the provider's lead id as a string (`"99812"` for the v1 example).

`GET /leads?agencyId=harbor-realty` → that agency's leads, with filters `provider`, `contactId`, and
`payload.<path>=<value>`:
- The path is a dotted path into the payload, e.g. `payload.tracking.utm_campaign=spring-2027` or
  `payload.lead.contact.name.last=Becker`.
- If the path passes through an array, any element may match: `payload.answers.question=Budget`.
- A string matches if it equals the value; a number or boolean matches if its JSON text equals the
  value (`payload.lead_id=99812`).
- Multiple filters are ANDed. `?limit=` (default 50, max 500).

## 3. Contacts
Every lead is linked to a contact of its agency (`contactId`).

The lead's email addresses are FormFlow `fields.email`, LeadPipe v1 `person.emails`, and v2
`lead.contact.email_addresses` (the `primary` one first, then the others in order). Its phone
numbers are FormFlow `fields.phone`, v1 `person.phones`, and v2 `lead.contact.phone_numbers[].value`.

- If a contact of the agency has one of the lead's addresses (in any position, case-insensitively),
  the lead is linked to that contact. Addresses and numbers from the lead that the contact doesn't
  have yet are appended to its lists with label `other`. Nothing else about the contact changes.
- Otherwise a new contact is created with the lead's addresses and numbers (in order, label
  `other`), and:
  - names: FormFlow `fields.name` (the last word is the last name, everything before it the first
    name; a single word is used for both), v1 `person.first_name` and `last_name`, v2
    `lead.contact.name.first` and `last`.
  - company: FormFlow `fields.company`, LeadPipe `organization.name`. It's the agency's company with
    that name (ignoring case and surrounding spaces), or a new company with that name if there is
    none. No company if the lead has none.
