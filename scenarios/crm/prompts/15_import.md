# Importing contacts from spreadsheets

New customers arrive with thousands of contacts in a spreadsheet, and existing ones re-export from
other tools now and then.

`POST /agencies/:agencyId/imports` with a CSV file as the body (`Content-Type: text/csv`, UTF-8).
- The first line names the columns, in any order, ignoring case: `firstName`, `lastName`, `email`,
  `phone`, `title`, `company`, `tags` (separated by `;`). Other columns are ignored.
- Fields may be quoted (RFC 4180): quoted fields can contain commas, line breaks and `""` for a quote.
- A row needs `firstName`, `lastName` and a valid `email`; otherwise it is rejected and the other
  rows are still imported.
- A row whose email belongs to a contact of the agency (in any position, ignoring case) updates that
  contact: it gets the row's title if it has none, the phone and company if it doesn't have them yet
  (appended), and the row's tags it lacks. Nothing it already has is overwritten.
- Other rows create contacts. The company is found by name in the agency (ignoring case and spaces at
  either end) or created. Rows with the same email in one file are one contact: the first creates it,
  later ones update it.
- → `200`:
  ```json
  { "created": 2950, "updated": 12, "unchanged": 30, "rejected": [ { "row": 7, "error": "invalid email" } ] }
  ```
  `row` is the line number in the file (the header is line 1). A row that changes nothing counts as
  `unchanged`; importing the same file again creates and updates nothing.
- Files have up to 5,000 rows; the response must come within 60 seconds.
