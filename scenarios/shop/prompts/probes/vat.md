# VAT on orders

Our shops in Portugal and Germany must show the VAT included in what customers pay, on orders, returns
and reports, for their accountants. Prices stay as they are: they already include VAT.

## 1. Rates
`PUT /shops/:shopId/tax`
```json
{ "standardRate": 23, "categoryRates": { "books": 6, "food": 13 } }
```
- Rates are percentages: numbers from 0 to 100 with at most 2 decimals. `standardRate` is required;
  `categoryRates` (optional) maps category names to rates. Invalid → `400`.
- A `PUT` replaces the shop's rates → `200` with them. `GET /shops/:shopId/tax` returns them; shops that
  never set any have `{ "standardRate": 0, "categoryRates": {} }`.
- A product's rate is the rate of its category, or the standard rate if its category has none.

## 2. Orders
- Every order line shows `taxRate`, the rate of its product when the order was placed, and `tax`, the VAT
  included in what was paid for the line: paid × rate / (100 + rate), rounded to the cent, where paid is
  the line total minus its discount.
- The shipping fee includes VAT at the shop's standard rate when the order was placed: orders show
  `shippingTax`, computed the same way (0 without a fee).
- Orders show `tax`: the lines' tax plus `shippingTax`.
- Changing the rates later doesn't change existing orders.
- Orders placed before this change show `taxRate` 0 and `tax` 0 on every line, and `shippingTax` and
  `tax` 0.
- This applies to every way an order is placed (also cart checkouts and subscriptions), and wherever
  orders appear.

## 3. Edits and returns
- When an order is edited, lines that stay keep their `taxRate`; new lines get the current rate. Every
  line's tax is recomputed from what it now pays, and `shippingTax` from the new fee at the order's rate.
- Return items show `tax`: the VAT included in their refund, computed the same way. When a return
  brings a line's returned quantity up to its quantity, its tax is whatever of the line's tax hasn't
  been refunded yet, so the tax refunded for a line always adds up exactly to the line's tax. The return
  shows `tax`, the sum of its items' tax.

## 4. Sales reports
Every group of `GET /reports/sales` gets `tax`: the lines' tax minus the tax refunded for them.
