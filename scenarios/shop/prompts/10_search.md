# Product search

Customers want a search box that finds products while they type.

- Products get `description`: optional text of at most 5000 characters. `POST /products` and
  `PATCH /products/:id` accept it (`null` clears it).
- `GET /products/search?shopId=clay-and-kiln&q=speck mug` → `{ "results": [ …products as GET returns
  them… ] }`, at most `limit` (default 20, max 100). `q` is required (`400`).
- What is searched: the name, description and category, the skus of the variants, and the text and
  `select` attribute values.
- Matching works by word, ignoring case and accents. Every word of `q` must match. A word also matches
  other grammatical forms of the same English word (`mug` and `mugs`). The last word of `q` also
  matches words that start with it, as the customer may still be typing (`speck` finds "Speckled").
  A `q` that is a whole sku finds that product (ignoring case).
- Order: products whose name contains all the words first; within each group, most `unitsSold`
  first, then by `name`.
- It combines with the product filters `category`, `inStock` and `minRating`.
- Search always reflects the current data.
