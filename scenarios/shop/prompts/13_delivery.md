# Local delivery and pickup points

Shops deliver locally with their own couriers, and customers can pick orders up at partner shops.

## 1. Delivery zones
`POST /shops/:shopId/delivery-zones`
```json
{ "name": "Lisbon centre", "area": [[-9.20, 38.69], [-9.10, 38.69], [-9.10, 38.75], [-9.20, 38.75]],
  "fee": 4.5, "freeFrom": 60 }
```
- `area`: a polygon, at least 3 corners as `[lng, lat]`, in order (it may repeat the first corner at the
  end). `fee`: an amount. `freeFrom`: optional amount. Invalid → `400`.
- → `201` with the zone, including `id`. `GET /shops/:shopId/delivery-zones`; `DELETE
  /delivery-zones/:id` (`204`).

## 2. Pickup points
- `POST /shops/:shopId/pickup-points` with `{ "name", "location": { "lat", "lng" } }` → `201`.
- `GET /shops/:shopId/pickup-points?near=38.72,-9.14` → nearest first, each with `distanceKm`
  (great-circle distance, 1 decimal); `?limit=`. Without `near`, all of them.

## 3. Orders
Orders (and cart checkouts) accept `delivery`:
- `{ "type": "shipping", "location": { "lat", "lng" } }`: the location must be inside a zone of the
  shop (`400` otherwise). If several zones contain it, the cheapest applies. `shippingFee` is the
  zone's `fee`, or 0 if the order's subtotal minus its discount is at least `freeFrom`.
- `{ "type": "pickup", "pickupPointId": "…" }` (a pickup point of the shop): `shippingFee` 0.
- Without `delivery`, as today: `shippingFee` 0. Existing orders have `shippingFee` 0 too.

Orders show `delivery` (with the `zoneId` that applied for shipping) and `shippingFee`, and `total` is
now `subtotal` − discount + `shippingFee`. Returns never refund the shipping fee; sales reports stay
based on the lines. Order filter: `pickupPointId`.
