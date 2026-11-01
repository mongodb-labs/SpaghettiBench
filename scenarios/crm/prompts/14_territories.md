# Locations and sales territories

Brokers and event planners work by area: "who do we know within 10 km of this venue?", "which
contacts are in my territory?".

## 1. Locations
- Companies and contacts get `location`: `{ "lat": 38.7223, "lng": -9.1393 }` or `null`. `lat` is
  between −90 and 90 and `lng` between −180 and 180 (`400` otherwise). `POST` and `PATCH` accept it.
- For searches, a contact without its own location is where its primary company is. Its `location`
  in responses stays its own.

## 2. Nearby
`GET /contacts?near=38.7223,-9.1393&radiusKm=10` → the contacts within that great-circle distance,
nearest first, each with `distanceKm` (1 decimal). It combines with the other filters and `?limit=`.

## 3. Territories
`POST /agencies/:agencyId/territories`
```json
{ "name": "Lisbon centre", "area": [ [-9.20, 38.69], [-9.10, 38.69], [-9.10, 38.75], [-9.20, 38.75] ] }
```
- `area` is a polygon: at least 3 corners as `[lng, lat]`, in order (it may repeat the first corner at
  the end) → `400` otherwise. → `201` with the territory, including `id`.
- `GET /agencies/:agencyId/territories`, `DELETE /territories/:id` (`204`).
- `GET /contacts?territory=<id>` → contacts whose location (their own, or their primary company's) is
  inside the area.
- `GET /territories/:id/summary` → `{ "contacts": 14, "openPipeline": 1250000 }`: the number of those
  contacts and the sum of their `openPipeline`.
