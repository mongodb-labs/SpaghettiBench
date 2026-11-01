# Product reviews

Shoppers trust products that other customers liked, and shops want to hear what buyers think.

## 1. Reviews
`POST /products/:id/reviews`
```json
{ "customerEmail": "ines@example.com", "rating": 5, "title": "Lovely glaze", "text": "Holds a big coffee." }
```
- `rating`: an integer from 1 to 5. `text`: a non-empty string of at most 2000 characters. `title`:
  optional, at most 100 characters. Invalid requests → `400`.
- Only customers who ordered the product (any of its variants), in an order that isn't cancelled, may
  review it (`customerEmail` ignoring case) → `403` otherwise.
- One review per customer and product → `409` for a second one.

→ `201` with the review: `id`, `productId`, `customerEmail`, `rating`, `title`, `text`, `createdAt`,
`status` (`published`) and `helpful` (0).

- `PATCH /reviews/:id` with any of `rating`, `title`, `text` (same validation), or `status`
  (`published` or `hidden`, for moderation) → `200` with the review.
- `POST /reviews/:id/helpful` with `{ "customerEmail": "…" }` → `200` with the review. Each email
  counts once (ignoring case); voting again changes nothing.
- `GET /products/:id/reviews` → the product's published reviews, newest first; `?sort=helpful` puts
  the most helpful first (ties newest first); `?rating=5` only those with that rating; `?limit=`
  (default 20, max 100).

## 2. Ratings on products
- Products get `rating`: `{ "average": 4.3, "count": 12 }` over their published reviews, the average
  rounded to 1 decimal; `{ "average": null, "count": 0 }` without any. Always current.
- Product filter `minRating=4` (average at least that).
- `GET /products?sort=rating`: highest average first, then most reviews, then `name`; products without
  reviews last.
