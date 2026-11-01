# Activity timeline

Account managers log calls, emails, meetings and notes against contacts, and want to see a contact's
history at a glance.

## 1. Activities
`POST /activities`
```json
{ "agencyId": "harbor-realty", "type": "call", "contactId": "…", "at": "2027-02-03T10:00:00Z",
  "durationMinutes": 12, "outcome": "connected" }
```
- Required for every activity: `agencyId`, `type`, `contactId` (a contact of the agency) and `at` (an
  ISO 8601 timestamp).
- Each type has its own fields:
  - `call`: `durationMinutes` (integer ≥ 0) and `outcome` (`connected`, `no_answer`, `voicemail`),
    both required.
  - `email`: `direction` (`in` or `out`) and `subject` (non-empty), both required.
  - `meeting`: `endsAt` (a timestamp after `at`, required), `attendeeIds` (other contacts of the agency
    who attended, optional, each at most once), `location` (optional text).
  - `note`: `text` (non-empty, required).
- A missing or invalid field, a field of another type, or an unknown field → `400`, nothing stored.

→ `201` with the activity, including `id`.

`GET /activities/:id` → the activity.
`PATCH /activities/:id` with `at` and/or fields of the activity's own type (same validation;
`location` can be cleared with `null`) → `200`. `type` and `contactId` can't change (`400`).
`DELETE /activities/:id` → `204`.

## 2. Timelines
- `GET /contacts/:id/activities` → the contact's activities, newest `at` first: its own, and meetings
  of other contacts that list it in `attendeeIds`.
- Contacts get `lastActivityAt`: the `at` of the newest activity on their timeline, or `null`. It is
  included wherever a contact is returned and always current.

## 3. Finding activities
`GET /activities?agencyId=…`, newest first, `?limit=` (default 50, max 500), with filters:
`type`, `contactId` (on that contact's timeline), `from` and `to` (`at` within, inclusive), `outcome`,
`direction`, and `minDurationMinutes`. Multiple filters are ANDed.

## 4. Merging contacts
Merging moves the duplicate's activities to the kept contact, including where it is an attendee (a
meeting never lists the same contact twice, nor its own contact as an attendee).
