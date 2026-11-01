# Shops in several languages

Clay & Kiln sells to Portugal and Germany, and customers there want product pages in their language.

## 1. Languages
`PUT /shops/:shopId/languages` with `{ "default": "en", "others": ["pt", "de"] }` → `200`. Codes are
two lower-case letters; the default isn't among the others (`400` otherwise). `GET` returns them;
shops that never set any have `{ "default": "en", "others": [] }`.

## 2. Translations
Products get `translations`, per language of `others`:
```json
"translations": {
  "pt": { "name": "Caneca salpicada", "description": "…", "options": { "black": "preto", "M": "Médio" } }
}
```
- `name` (non-empty) and `description` translate the product's; `options` translates variant option
  values (keyed by the value in the default language). Every part is optional.
- `POST /products` and `PATCH /products/:id` accept `translations`. A `PATCH` merges per language:
  the fields it sends replace those fields (`options` as a whole), `null` removes a field, and a
  language set to `null` is removed. A language the shop doesn't have → `400`.
- Responses always include `translations` (`{}` if there are none).

## 3. Reading in a language
- `GET /products/:id`, `GET /products` and `/products/search` accept `?lang=pt`: `name`,
  `description` and the variants' option values come in that language where translated, otherwise in
  the default language. A language the shop doesn't have → `400`.
- Search with `lang` searches the texts in that language (falling back the same way). Word forms only
  need to work for English.
- Product filter `missingTranslation=pt`: products without a `name` in that language.

## 4. Orders
`POST /orders` accepts `lang`; the lines' `name` and `options` are then in that language, as at the time
of the order.
