# Best sellers

Shops want to show their best sellers first.

- Products show `unitsSold`: the units of the product (all its variants) in orders that aren't
  cancelled, minus the units returned. It is always current.
- `GET /products?shopId=clay-and-kiln&sort=bestselling` lists the most units sold first; products
  with the same number by `name` (A to Z). It works with the product filters and `limit`.
