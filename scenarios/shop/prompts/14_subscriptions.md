# Subscriptions

Some products are bought again and again: Northloom's sock club, refills of screen protectors. Customers
want to subscribe and get them on a schedule.

## 1. Subscriptions
`POST /subscriptions`
```json
{ "shopId": "northloom", "customerEmail": "ines@example.com", "items": [ { "variantId": "…", "quantity": 2 } ],
  "everyWeeks": 4, "startsOn": "2027-05-01" }
```
- `items` like an order's, but without personalisation (an item with `personalization` → `400`);
  `everyWeeks` an integer from 1 to 52; `startsOn` a
  date. Also optional `delivery`, as for orders. Invalid → `400`.
- → `201` with the subscription: also `id`, `status` (`active`), `nextOn` (the next scheduled date,
  first `startsOn`) and `history` (`[]`).
- The schedule is `startsOn`, then every `everyWeeks` weeks after it.
- `GET /subscriptions/:id`; `GET /subscriptions?shopId=…` with filters `customerEmail` (ignoring case)
  and `status`.
- `PATCH /subscriptions/:id` with any of `status` (`active`, `paused`, `cancelled`), `items`,
  `everyWeeks` → `200`. Cancelled subscriptions can't change (`409`). When a paused subscription is
  resumed, `nextOn` becomes the first date of its schedule on or after today (UTC); missed dates are
  skipped.

## 2. Placing the orders
Our scheduler calls `POST /shops/:shopId/subscriptions/run` with `{ "date": "2027-05-01" }` every day.
Sometimes it calls twice for the same day, and two instances may call at the same moment.
- Every active subscription of the shop with `nextOn` on or before `date` gets one order, placed like
  `POST /orders` (current prices, stock, delivery), with `subscriptionId` on the order. `nextOn` then
  moves to the first date of the schedule after `date`.
- If an order can't be placed (not enough stock, a delivery location no longer in a zone), it is
  skipped, and `nextOn` moves on just the same.
- `history` gets an entry per scheduled date handled: `{ "date", "orderId" }`, or `{ "date",
  "skipped": "<reason>" }`.
- → `200` with `{ "placed": [orderIds], "skipped": [subscriptionIds] }`. However often and
  concurrently it is called, a subscription never gets two orders for the same scheduled date.
- Order filter: `subscriptionId`.
