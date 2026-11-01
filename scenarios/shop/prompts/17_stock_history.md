# Stock history

Shops need to know why stock changed: for audits, to find shrinkage, and to settle disputes with
customers.

- Every change to a variant's stock is recorded as a movement:
  ```json
  { "id": "…", "variantId": "…", "productId": "…", "change": -2, "stockAfter": 8, "reason": "order",
    "orderId": "…", "at": "2027-05-26T09:00:00Z" }
  ```
  `reason` is one of `initial` (a new product or variant, or one created by an import), `order`
  (also subscriptions and checkouts), `cancel`, `return`, `edit`, `adjustment` (stock set with
  `PATCH`, or below) and `opening`. `orderId` is set for order, cancel, return and edit. Cart holds don't
  change the stock and have no movements.
- Existing variants get one `opening` movement with their current stock, so that every variant's
  movements add up to its stock.
- `POST /variants/:id/adjustments` with `{ "change": -3, "note": "broken in storage" }` (a non-zero
  integer; stock can't go below 0 → `409`) → `201` with the movement, which also has the `note`.
- `GET /variants/:id/movements` → oldest first, with `from` and `to` (timestamps, inclusive) and
  `?limit=` (default 100, max 1000).
- `GET /reports/stock?shopId=…&at=<timestamp>` → `{ "variants": [ { "variantId", "sku", "stock" } ] }`:
  every variant of the shop with its stock at that moment (0 before it existed).
- The movements of a variant always add up to its stock, also when many orders arrive at the same
  moment.
