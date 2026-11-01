# Importing listings from Craftly

Many of our makers also sell on the Craftly marketplace and want to bring their listings over, and
keep names, descriptions and prices in sync by importing again from time to time.

`POST /shops/:shopId/imports/craftly` with Craftly's export, as is:
```json
{ "listings": [
  { "listing_id": 9001, "title": "Speckled mug", "description": "…", "state": "active",
    "taxonomy_path": ["Home & Living", "Kitchen", "Mugs"], "tags": ["stoneware"],
    "price": { "amount": 2450, "divisor": 100, "currency_code": "EUR" }, "quantity": 12, "sku": ["MUG-SPECKLED"] },
  { "listing_id": 9002, "title": "Linen tee", "state": "inactive", "taxonomy_path": ["Clothing", "Tops"],
    "variations": {
      "properties": [ { "property_id": 1, "name": "Size" }, { "property_id": 2, "name": "Colour" } ],
      "offerings": [ { "sku": "TEE-S-RED", "values": { "1": "S", "2": "Red" },
                       "price": { "amount": 3000, "divisor": 100, "currency_code": "EUR" }, "quantity": 4 } ] } }
] }
```
- A listing becomes a product: `name` from `title`, `description`, `category` from the last entry of
  `taxonomy_path` in lower case. Without `variations` it has one variant (the first `sku`, `price`,
  `quantity` as stock); with them, the option names are the property names in lower case, and every
  offering is a variant (its values by `property_id`). Prices are `amount / divisor`.
- Products get `active` (default `true`; `PATCH` accepts it). A listing in state `inactive` makes it
  `false`, `active` makes it `true`. Inactive products can't be ordered or put in carts (`400`) and don't
  appear in search; `GET /products` still lists them, and accepts `active=true|false`.
- Imported products remember where they came from: `source: { "marketplace": "craftly", "listingId":
  "9001" }`, included wherever a product is returned (`null` for other products); `GET
  /products/:id/source` → the listing exactly as last imported.
- Importing again updates the product with the same `listingId`: name, description, category, active,
  prices; variants are matched by sku, new offerings are added. Stock is only taken from Craftly when a
  product or variant is created: after that the shop's own stock counts.
- A listing that can't be imported (missing title or price, a sku another product of the shop has) is
  skipped and reported; the others are imported.
- → `200` with `{ "created": 3, "updated": 1, "unchanged": 40, "rejected": [ { "listingId": "9003",
  "error": "…" } ] }`. Importing the same export again changes nothing.
- Exports have up to 2,000 listings; the response must come within 60 seconds.
