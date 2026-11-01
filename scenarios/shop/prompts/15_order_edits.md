# Changing orders

Customers write in before their order ships: another size, one more mug, drop the scarf. Today support
cancels and re-creates the order, which loses the order number and sometimes the stock.

- Orders get `version`: 1 when placed (existing orders too), increased by every change to the order
  (edits, cancelling, returns).
- `PATCH /orders/:id` with `{ "version": 2, "items": [ … ] }`, the complete new list of items as in
  `POST /orders`, → `200` with the order:
  - Only `placed` orders can be changed (`409` otherwise), and `version` must be the order's current
    one (`409` if it changed in the meantime, e.g. two support staff editing at once).
  - Items are validated as for a new order (`400`).
  - A line that stays (same variant and personalisation) keeps its `id` and `unitPrice`, with the new
    quantity; new lines get the current price.
  - Stock: what the new list needs more is taken, what it needs less is returned, all or nothing (not
    enough stock → `409`, nothing changes).
  - The discount is recomputed for the new subtotal with the order's code (its `minSubtotal` must
    still be met → `400`; limits on uses aren't checked again), with new line shares. The shipping
    fee is recomputed too.
  - `number`, `createdAt`, `customerEmail` and `delivery` don't change.
- `GET /orders/:id/revisions` → every earlier version of the order, complete as it was, oldest first.
