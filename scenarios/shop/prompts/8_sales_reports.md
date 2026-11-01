# Sales reports

Shop owners want numbers: what sold per month, which categories and sizes sell best, what the
discount codes cost them and how much went back.

`GET /reports/sales?shopId=clay-and-kiln&groupBy=<keys>&from=2026-10-01&to=2026-12-31`

- `shopId` and `groupBy` are required → `400` without them.
- `from` and `to` are optional dates (`YYYY-MM-DD`, inclusive), compared with the UTC date of the
  order's `createdAt`. Invalid dates → `400`.
- The report covers the lines of orders that aren't cancelled.
- `groupBy` is a comma-separated list of keys:
  - `day` (`YYYY-MM-DD`) and `month` (`YYYY-MM`): of the order's `createdAt`, in UTC
  - `productId`, `variantId`
  - `category`: the product's current category
  - `option.<name>`: the line's variant option (as ordered)
  - `attr.<key>`: the product's current attribute value
  - `discountCode`: the order's discount code, written as the shop defined it

  An unknown key → `400`. Lines without a value for a key are grouped under `null`.
- Reports always reflect the current data; e.g. when a product moves to another category, its sales
  move with it.

Response, one entry per combination of values that has at least one line:
```json
{
  "groups": [
    { "category": "mugs", "month": "2026-12", "orders": 12, "units": 30, "gross": 735,
      "discounts": 40.5, "refunds": 24.5, "net": 670 }
  ]
}
```
- One property per `groupBy` key, named exactly like the key.
- `orders`: the number of different orders with lines in the group.
- `units`: the quantities ordered minus the units returned.
- `gross`: the sum of the line totals. `discounts`: the sum of the lines' discounts. `refunds`: the
  sum of what was refunded for the lines. `net`: `gross` − `discounts` − `refunds`.
