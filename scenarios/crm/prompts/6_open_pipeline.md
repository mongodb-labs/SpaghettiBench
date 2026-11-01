# Open pipeline per contact

Account managers want to see at a glance which contacts matter most right now.

- Contacts get `openPipeline`: the total `value` of the contact's deals in stage `lead`, `qualified`
  or `proposal` (`0` if there are none). It's included wherever a contact is returned.
- `GET /contacts` accepts `?sort=openPipeline`: largest first; contacts with the same value in order
  of `lastName`, then `firstName`. It combines with the filters and `?limit=`.
- `openPipeline` always reflects the contact's deals as they are now.
