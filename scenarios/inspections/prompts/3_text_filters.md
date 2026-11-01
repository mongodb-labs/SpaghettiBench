# Case-insensitive text filters

Managers keep missing inspections because what they type doesn't match the capitalisation the worker
used: `?answers.site_name=depot 4` doesn't find "Depot 4".

Filters on `text` fields now ignore case: `?answers.site_name=depot 4` finds "Depot 4" and "DEPOT 4".
- This applies at any depth (e.g. `?answers.floors.rooms.room_name=lobby`), with the same-item rule as
  before, and to the old filter keys of Acme's form (`?answers.extinguisher_location=loading bay`).
- The whole value must still match: `?answers.site_name=depot` doesn't find "Depot 4".
- Only `text` fields change. Filters on `select`, `number`, `boolean` and `date` fields work exactly
  as before.
