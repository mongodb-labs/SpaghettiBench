# Discount codes

Shops want to hand out discount codes: to newsletter subscribers, for a weekend sale, or a few codes
that only the first customers of a drop can use.

## 1. Codes
`POST /shops/:shopId/discounts`
```json
{ "code": "WINTER10", "type": "percent", "value": 10, "minSubtotal": 50, "maxUses": 100,
  "oncePerCustomer": true, "startsAt": "2026-12-01T00:00:00Z", "endsAt": "2027-01-01T00:00:00Z" }
```
- `code`: letters, digits, `-` and `_`; unique within the shop, ignoring case → `409` otherwise.
- `type`: `percent` (a `value` above 0 and at most 100) or `fixed` (an amount above 0).
- Optional: `minSubtotal` (an amount ≥ 0), `maxUses` (an integer ≥ 1), `oncePerCustomer` (a boolean,
  default `false`), `startsAt` and `endsAt` (ISO 8601 timestamps).
- Invalid codes → `400`.

→ `201` with the code, including `"active": true` and `"uses": 0`.

`GET /shops/:shopId/discounts` → the shop's codes. `GET /shops/:shopId/discounts/:code` (the code
ignoring case) → the code. `uses` is the number of orders that used the code and aren't cancelled.

`PATCH /shops/:shopId/discounts/:code` with `active` (`true` or `false`) → `200` with the code.

## 2. Orders with a code
- `POST /orders` accepts `discountCode` (ignoring case).
- The code must exist in the shop and be active; the time of the order must be at or after
  `startsAt` and before `endsAt`; the order's subtotal must be at least `minSubtotal`; `uses` must be
  below `maxUses`; and with `oncePerCustomer`, the customer (`customerEmail`, ignoring case) must not
  have another order with this code that isn't cancelled. Otherwise → `400` with a message saying
  why, and nothing is stored.
- Orders get `subtotal`, the sum of the line totals, and `discount`:
  ```json
  "subtotal": 100, "discount": { "code": "WINTER10", "amount": 10 }, "total": 90
  ```
  - `code` is written as the shop defined it.
  - `percent`: `amount` is the subtotal × `value` / 100, rounded. `fixed`: the `value`, but at most
    the subtotal.
  - `total` is `subtotal` − `amount`.
- Every order line gets `discount`, its share of the amount: proportional to its line total and
  rounded, except that the last line gets whatever makes the shares add up exactly to the amount.
- Orders without a code (including all existing ones): `subtotal` equal to `total`, `discount` `null`
  or omitted, and every line's `discount` 0.
- Codes are used in drops, where many customers order at the same moment: `maxUses` and
  `oncePerCustomer` must hold even then.
- Cancelling an order frees its use of the code.
- New order filter: `discountCode` (ignoring case).
