# Maker shops — backend

We run a platform that hosts online shops for small independent makers: a pottery studio, a
clothing label, a store for phone and laptop accessories. Each shop manages its products and stock,
and customers order from them. The shops are small, but they sell every day, and some run limited
drops where many customers order at the same moment.

Build the backend API (no UI).

## Database
{database}

## Deployment
Our platform deploys the app by running `./start.sh` from the repository root, with `PORT`
and the database environment variables set. The app must serve the API on `PORT`.

## Shops
Every record belongs to a shop, identified by a `shopId` string that we assign (e.g.
`clay-and-kiln`). There is no endpoint for shops. A record may only reference records of the same
shop, and lists only ever return records of the requested shop.

## Money
Prices and amounts are numbers in the shop's currency with at most 2 decimals. Amounts the app
computes are rounded to the cent (half up).

## Products
`POST /products`
```json
{ "shopId": "clay-and-kiln", "sku": "MUG-SPECKLED", "name": "Speckled mug", "category": "mugs",
  "price": 24.5, "stock": 40 }
```
- `sku`, `name` and `category` are required, non-empty strings. `price` is a number ≥ 0 with at
  most 2 decimals; `stock` an integer ≥ 0.
- `sku` is unique within a shop → `409` if the shop already has a product with that sku.
- Invalid requests → `400` with an error message, and nothing is stored.

→ `201` with the product, including a server-generated `id`.

`GET /products/:id` → the product.

`PATCH /products/:id` with any of `name`, `category`, `price`, `stock` changes only those fields
(same validation) → `200` with the product. The `sku` can't be changed → `400`.

`GET /products?shopId=clay-and-kiln` → that shop's products.
- Filters: `category`, `sku`, `inStock=true` (stock above 0). Multiple filters are ANDed.
- `?limit=` (default 50, max 500).

## Orders
`POST /orders`
```json
{ "shopId": "clay-and-kiln", "customerEmail": "ines@example.com",
  "items": [ { "productId": "…", "quantity": 2 } ] }
```
- `customerEmail` is required and must contain exactly one `@`, with text on both sides.
- `items`: at least one; `productId` must be a product of the same shop; `quantity` an integer ≥ 1;
  a product at most once per order.
- Invalid requests → `400`, and nothing is stored.
- If there isn't enough stock for any of the items → `409`, and nothing changes: no stock is taken
  and no order is created.
- Otherwise the stock of every product is reduced by the quantity ordered.

→ `201` with the order:
```json
{
  "id": "…", "shopId": "clay-and-kiln", "customerEmail": "ines@example.com",
  "status": "placed", "createdAt": "2026-10-02T09:15:00Z",
  "items": [
    { "productId": "…", "sku": "MUG-SPECKLED", "name": "Speckled mug", "unitPrice": 24.5,
      "quantity": 2, "lineTotal": 49 }
  ],
  "total": 49
}
```
- `sku`, `name` and `unitPrice` are the product's at the time of the order: later changes to the
  product don't change existing orders.
- `lineTotal` is `unitPrice` × `quantity`; `total` is the sum of the line totals.
- Many customers order at the same time, especially during limited drops. Stock must never go below
  0, and we must never sell more than we have.

`GET /orders/:id` → the order.

`GET /orders?shopId=clay-and-kiln` → that shop's orders.
- Filters: `customerEmail` (case-insensitive), `status`, `productId` (orders containing that
  product). Multiple filters are ANDed.
- `?limit=` (default 50, max 500).

`POST /orders/:id/cancel` → `200` with the order, now `"status": "cancelled"`; the ordered
quantities go back into stock. Only orders with status `placed` can be cancelled → `409` otherwise.
