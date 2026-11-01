# Webhooks for agencies' own tools

Agencies want their own tools (accounting, newsletters, dashboards) to hear about changes as they
happen, instead of polling the API.

## 1. Subscriptions
`POST /agencies/:agencyId/subscriptions`
```json
{ "url": "https://hooks.example.com/crm", "events": ["contact.created", "deal.stage_changed"] }
```
- Events: `contact.created`, `contact.updated`, `contact.deleted`, `deal.created`, `deal.updated`,
  `deal.stage_changed`. `url` must be an `http` or `https` URL; `events` a non-empty list of them
  (`400` otherwise).
- → `201` with the subscription, including `id` and a `secret`.
- `GET /agencies/:agencyId/subscriptions`; `DELETE /subscriptions/:id` (`204`) stops deliveries.

## 2. Deliveries
- After every change through the API that matches a subscription, its URL receives a `POST`:
  ```json
  { "id": "…", "type": "deal.stage_changed", "occurredAt": "2027-05-26T09:00:00Z",
    "agencyId": "harbor-realty", "data": { …the deal as GET returns it after the change… },
    "previousStage": "proposal" }
  ```
  `previousStage` only on `deal.stage_changed`, which a deal gets (in addition to `deal.updated`) when
  its stage changes. `contact.deleted` carries the contact as it was.
- Header `X-Signature`: the hex HMAC-SHA256 of the raw body with the subscription's secret.
- At least once: a delivery that doesn't get a 2xx answer within 10 seconds is retried, the first time
  within 10 seconds, then with growing delays for at least 24 hours. Receivers de-duplicate by `id`.
- In order per contact: events of a contact and its deals arrive in the order they happened; a later
  one is not delivered before an earlier one has been accepted.
- Deliveries never slow down the API: requests answer as fast as before, even when a receiver is slow
  or down.
