# Category attributes

Every category has its own product details: a mug has a capacity and is dishwasher safe or not, a
shirt has a material, a cable a length and a connector. Shops want to record them, and customers
want to filter by them.

## 1. Definitions
`POST /shops/:shopId/categories/:category/attributes`
```json
{ "key": "capacity_ml", "label": "Capacity (ml)", "type": "number" }
```
- `key`: letters, digits and underscores; unique within the shop and category → `409` otherwise.
- `label`: a non-empty string.
- `type`: `text`, `number`, `boolean` or `select`. A `select` needs `options`, a non-empty list of
  distinct strings.
- Invalid definitions → `400`.

→ `201` with the definition.

`GET /shops/:shopId/categories/:category/attributes` → the category's definitions, in the order
they were created.

## 2. Values
- Products get `attributes`, values for the attributes of their category:
  `"attributes": { "capacity_ml": 350, "dishwasher_safe": true }`. Responses always include it
  (`{}` if there are none).
- `POST /products` and `PATCH /products/:id` accept `attributes`. A key the product's category
  doesn't define, or a value of the wrong type (for a `select`, a value not among its options) →
  `400`, and nothing changes.
- `PATCH` merges into the existing attributes; `null` removes an attribute.
- When a product moves to another category, it keeps the attributes that the new category also
  defines with the same type; the others are removed. Attributes in the same request are
  validated against the new category.

## 3. Filters
`GET /products?shopId=clay-and-kiln&category=mugs&attr.dishwasher_safe=true&attr.capacity_ml.min=300`
- `attr.<key>=<value>`: products with exactly that value (numbers compared as numbers, booleans
  written `true`/`false`).
- `attr.<key>.min` and `attr.<key>.max`: inclusive bounds for `number` attributes.
- Attribute filters need a `category` filter, and the attribute must be defined for that category →
  `400` otherwise.

## 4. Facets
`GET /products/facets?shopId=clay-and-kiln&category=mugs`, plus any of the product filters →
```json
{ "total": 12,
  "attributes": { "dishwasher_safe": { "true": 8, "false": 3 }, "glaze": { "matte": 5, "gloss": 7 } } }
```
- `total`: the number of products matching the filters.
- For every `select` and `boolean` attribute of the category: how many of the matching products have
  each value, listing only values that at least one product has.
- `category` is required → `400` without it.

## 5. Northloom's materials
Northloom wants an attribute `material` (label `Material`, type `select`, options `cotton`, `linen`,
`wool`, `other`) for its category `tops`. Create it, and set it on all of Northloom's existing tops
from the product name: a name containing the word "linen" → `linen`; otherwise "wool" or "merino" →
`wool`; otherwise "cotton" → `cotton`; otherwise `other`. Case doesn't matter, and only whole words
count ("Cottonwood" is not cotton).
