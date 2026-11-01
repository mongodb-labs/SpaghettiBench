# Carts that hold stock

During drops, customers lose items between choosing them and paying. Carts should hold the stock for
a while.

## 1. Settings
`PUT /shops/:shopId/settings` with `{ "cartHoldSeconds": 900 }` (an integer from 5 to 86400) → `200`.
`GET` returns it; the default is 900.

## 2. Carts
- `POST /carts` with `{ "shopId", "customerEmail" }` (validated like orders) → `201`:
  `{ "id", "shopId", "customerEmail", "status": "active", "items": [], "expiresAt": "…" }`.
- `PUT /carts/:id/items/:variantId` with `{ "quantity": 2 }` sets the cart's quantity of that variant
  (`0` removes it) → `200` with the cart. Products with personalisation fields can't go into carts
  (`400`). Each change renews the hold: `expiresAt` becomes now + `cartHoldSeconds`.
- Items show `variantId`, `productId`, `sku`, `quantity`, the current `unitPrice` and `lineTotal`;
  the cart shows `total`.
- `GET /carts/:id`; `DELETE /carts/:id` → `204`, releasing what it held.

## 3. Holding stock
- Units in active carts are held: variants and products get `available`, the stock minus the units
  held. `stock` stays the units the shop has.
- Orders (without a cart), cart changes and the `inStock` filter use `available`. Not enough → `409`,
  and nothing changes.
- A cart whose `expiresAt` has passed is `expired`: its units are available again at once, without
  waiting for any cleanup to run.
- Many customers fill carts at the same moment in a drop: never hold or sell more than the stock.

## 4. Checkout
`POST /carts/:id/checkout` with optional `discountCode` (and the other order fields that aren't items)
→ `201` with the order, placed like `POST /orders` with the cart's items, which may use the units the
cart holds. The cart becomes `checked_out`. An expired or checked-out cart → `409`; an empty cart →
`400`.
