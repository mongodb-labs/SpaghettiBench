# Deleting and restoring contacts

Agencies need to delete contacts (people ask to be removed, imports go wrong), but a wrong click must be
easy to undo.

- `DELETE /contacts/:id` → `204`. The contact moves to the trash together with its deals, its
  activities and its leads. From then on they appear nowhere: not in lists, filters, search, segments,
  reports, totals or timelines, and `GET` of the contact, its deals or activities → `404`. Meetings
  of other contacts keep existing, without the deleted contact among their attendees.
- `GET /trash?agencyId=…` → the agency's deleted contacts, most recently deleted first:
  `[ { "id", "firstName", "lastName", "deletedAt", "deals": 2, "activities": 5 } ]`.
- `POST /trash/:id/restore` → `200` with the contact. Everything comes back exactly as it was, with the
  same ids, including its place among the attendees of other contacts' meetings.
- `DELETE /trash/:id` → `204`: deleted for good, with everything that was trashed with it. Contacts
  that have been in the trash for 30 days are deleted for good automatically.
- While a contact is in the trash, a new inbound lead with its email creates a new contact.
