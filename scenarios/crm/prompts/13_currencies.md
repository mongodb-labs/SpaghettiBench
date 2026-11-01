# Deals in several currencies

More and more deals are signed in US dollars or pounds, but owners compare everything in euros.

## 1. Exchange rates
`PUT /agencies/:agencyId/rates/:date` with the rates from that date (`YYYY-MM-DD`) on:
```json
{ "USD": 1.0842, "GBP": 0.8551 }
```
- Units of the currency per 1 EUR; codes are 3 capital letters, values positive numbers (`400`
  otherwise). It replaces that date's rates → `200`.
- A rate is valid from its date until the next date that has a rate for the same currency.
- `GET /agencies/:agencyId/rates` → `{ "2027-03-01": { "USD": 1.0842, … }, … }`.

## 2. Deals
- Deals get `currency`: `EUR` by default and for all existing deals; `POST` and `PATCH` accept it. It
  must be `EUR` or a currency the agency has a rate for (`400`).
- Deals show `valueEur`: the value converted with the rate valid on the deal's `closeDate`, or today
  (UTC) for a deal without one; the earliest rate if the date is before the first one. Rounded to 2
  decimals; for `EUR` deals the value itself.
- `GET /deals` accepts `currency`.
- Everything that adds up deal values uses `valueEur`: the `value` of pipeline reports,
  `openPipeline`, and segments on `openPipeline`. Changing rates changes them at once.
