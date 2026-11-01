# Rename "notes" to "remarks"

Tidewater Properties' auditors call the free-text field on inspection items "remarks", and their new
export templates expect that name.

- In Tidewater's "Building Safety Walk" form (companyId `tidewater-properties`), rename the item
  field `notes` (in `floors.rooms.items`) to `remarks`, with the label `Remarks`, in every version of
  the form. No new version is created.
- All existing answers of that form use `remarks` instead of `notes`, at every level where items
  have it.
- Tidewater's older tablets and dashboards will keep using `notes` for two more months, for this
  form only:
  - submissions, appended items and item changes with `notes` store it as `remarks`
  - filters and report `groupBy` keys with `notes` (e.g.
    `?answers.floors.rooms.items.notes=…`) work like the `remarks` ones
- Responses always use `remarks`. Other forms are not affected.
