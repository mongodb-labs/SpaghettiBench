# Returns

Customers send items back: a mug arrived chipped, a shirt doesn't fit. Shops need to record returns
and refund the right amount.

## 1. Order lines
Every order line gets an `id` (the lines of existing orders too), which never changes, and
`returnedQuantity` (0 until something is returned).

## 2. Returns
`POST /orders/:id/returns`
```json
{ "items": [ { "lineId": "…", "quantity": 1 } ], "reason": "arrived chipped" }
```
- `items`: at least one; `lineId` a line of this order; `quantity` an integer ≥ 1; a line at most
  once. `reason` is an optional string. Invalid requests → `400`.
- Lines with personalisation can't be returned → `400`.
- Cancelled orders → `409`.
- An item returning more units than the line has left (its quantity minus the units already
  returned) → `409`.
- Nothing changes when a return is rejected.

→ `201` with the return:
```json
{ "id": "…", "orderId": "…", "createdAt": "2026-12-28T10:02:00Z", "reason": "arrived chipped",
  "items": [ { "lineId": "…", "quantity": 1, "refund": 22.05 } ], "refund": 22.05 }
```
- What a line was paid is its line total minus its discount. An item refunds the returned share of
  that: paid × returned quantity / line quantity, rounded. When a return brings a line's
  `returnedQuantity` up to its quantity, it refunds whatever of the line's paid amount hasn't been
  refunded yet, so the refunds of a line always add up exactly to what was paid for it.
- `refund` is the sum of the items' refunds.
- Returned units go back into the variant's stock.
- The order: `returnedQuantity` per line, `refunded` (the total refunded so far, 0 if nothing), and
  status `partially_returned` after the first return, `returned` once every unit of every line has
  been returned. Returned and partially returned orders can't be cancelled → `409`.
- A return doesn't free the use of a discount code.
- `GET /orders/:id/returns` → the order's returns, oldest first.
- Customers and support staff sometimes submit the same return at the same moment: an order must
  never have more units returned than ordered, or more refunded than paid.
