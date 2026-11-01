# Personalised items

Clay & Kiln engraves names on mugs, Northloom embroiders initials, and some items can be gift
wrapped. What can be personalised differs from product to product.

## 1. Personalisation fields
Products get `personalization`, the fields a customer fills in when ordering:
```json
"personalization": [
  { "key": "engraving", "label": "Name to engrave", "type": "text", "required": true, "maxLength": 20 },
  { "key": "font", "label": "Font", "type": "select", "options": ["serif", "script"] },
  { "key": "gift_wrap", "label": "Gift wrap", "type": "boolean" }
]
```
- `key`: letters, digits and underscores, unique within the list. `label`: a non-empty string.
- `type`: `text`, `select` or `boolean`. A `select` needs `options`, a non-empty list of distinct
  strings. A `text` field may have a `maxLength` (an integer ≥ 1).
- `required`: `true` or `false` (default `false`).
- `POST /products` and `PATCH /products/:id` accept `personalization`; a `PATCH` replaces the whole
  list. Invalid fields → `400`. Responses always include it (`[]` if there are none).

## 2. Ordering
- Order items take `personalization`, the customer's values:
  `{ "variantId": "…", "quantity": 1, "personalization": { "engraving": "Ana", "gift_wrap": true } }`.
- The values are checked against the product's fields at the time of the order: every required field
  is present (a required text is not empty), there are no other keys, and every value has the
  field's type, fits its `maxLength` and is one of its `options` → `400` otherwise. Items of a
  product without fields can't have personalisation → `400`.
- Order lines show `personalization` exactly as sent (`{}` if there is none).
- An order may now contain the same variant more than once, if the lines' personalisation differs
  (two mugs with different names). The same variant twice with the same personalisation → `400`.
  There must be enough stock for the variant's total quantity in the order.
- Changing a product's fields later doesn't change existing orders.

## 3. Finding orders
New order filter: `personalization.<key>=<value>` matches orders with a line that has that value
(booleans written `true`/`false`), e.g. all orders with gift wrapping.
