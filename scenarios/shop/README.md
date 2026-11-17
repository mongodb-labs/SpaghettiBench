# Scenario: maker shops

A platform hosting online shops for small independent makers (a pottery studio, a clothing label, a
phone-accessories store): products, stock and orders. This is the scenario where correctness depends
on transactions: stock taken by concurrent orders, discount codes with limited uses, returns and
refunds that must add up. The first nine stages split their feature changes evenly: three are expected to
favour documents, three relations with transactions. Stages 9–17 add breadth: reviews, search as you
type, translations, carts that expire, geo delivery, scheduled orders, order edits, a marketplace import
and a stock ledger. Two improvements (kind `improvement`) change existing behaviour instead of adding
features.

Repo folder in the container: `/workspace/maker-shops`, database `shops`, app ports 3400–3599.

## Stages

The stages, with what each change asks for, which database it is expected to favour and why, are in
[scenario.toml](scenario.toml). Every change prompt is followed by
[prompts/constraints.md](prompts/constraints.md). For one-shot runs (`--mode merged`),
[prompts/merged/spec.md](prompts/merged/spec.md) is all stages as one spec, and
[prompts/merged/data_changes.md](prompts/merged/data_changes.md) the one change to seeded data (Northloom's
materials) that comes after the seed. [prompts/probes/vat.md](prompts/probes/vat.md) is the change-request probe
given to finished runs (`harness.probe`): VAT rates per shop and category, included in order lines, shipping,
returns and sales reports, frozen at order time; its checks are [checks/vat.py](checks/vat.py).

## Seed data

[seed.py](seed.py) creates the same data for every run through the API: 3 shops, 53 products (some out
of stock) and about 140 orders, some of them cancelled. Halfway through the orders, three prices per
shop change, so older orders carry older prices. Northloom's tops have names covering the edge cases
of the material rule ("Cotton-Linen Blend Tee", "Cottonwood Print Tee", "Woolly Mammoth Graphic
Tee", "Merino-Silk Base Layer"). Two orders have fixed keys: `late_cancel` (cancelled by the
`variants` checks) and `late_return` (partly returned by the `returns` checks).

## Checks

Prefixed `v0.` … `v15.` by the feature stage that introduced them (`v2.` is `category_attributes`, and
so on) and `i1.`, `i2.` by the improvements; each later stage re-runs all earlier ones, so a failing
earlier check is a **regression**. Fixture checks run in fresh shops (e.g. `lisbon-ceramics`, then
`lisbon-ceramics-2` on the next run) and compare exact sets. The checks never order seeded products,
so their expected stock follows from the seeded orders.

| Phase | What the checks cover |
|---|---|
| `v0.` | Products: read back, validation (incl. 3 decimals), duplicate sku `409` (but allowed in another shop), filters, PATCH (sku immutable); orders: lines and totals to the cent, stock taken, validation, `409` out of stock taking nothing, prices and names frozen, filters, cancel and restock, 12 concurrent orders for 5 in stock, concurrent multi-item orders consistent; seeded products, stock, orders and filters |
| `v1.` | Variants read back, old fields derived, old shape creates one variant, validation, sku taken across variants, add and patch variants, old PATCH only with one variant, option/sku/in-stock filters, order lines by variant and by product id, variant stock and prices, concurrent variant orders; seeded products and order lines converted, cancelling an existing order |
| `i1.` (`order_numbers`) | 1001 onwards, numbers in every response, rejected orders take no number, cancelled keep theirs, filter, per shop, concurrent orders without gaps; seeded orders numbered by time |
| `v2.` | Definitions and validation, typed values and validation, PATCH merge and removal, exact/boolean/range filters, facets with filters, category moves; Northloom's `material` definition, backfill, filter, and no attributes on other products |
| `v3.` | Fields read back and validation, PATCH replaces; values on lines, the same variant twice, every rule of values, stock for the total quantity, filters, changed fields keep orders; seeded lines without personalisation |
| `v4.` | Codes read back and validation, duplicate ignoring case; percent, fixed, capped and no code; shares adding up over three lines; unknown, inactive, not started, expired and below-minimum codes; once per customer and max uses (freed by cancelling); uses; filter; concurrent orders respect max uses and once per customer; seeded orders without discount |
| `v5.` | Line ids, partial and full returns, the last return refunding the rest (9.67 + 9.67 + 9.66), validation and `409`s, nothing changed on rejection, returned orders can't be cancelled, list, code use kept, status filters, concurrent duplicate returns; seeded line ids, returning part of an existing order |
| `i2.` (`bestsellers`) | `unitsSold` on products and in lists (variants, cancellations, returns), sort with ties by name, with filter and limit, following changes; seeded values match the app's own orders, seeded sort |
| `v6.` | Reports by every key, two keys, date range, cancelled orders excluded, validation, following the current category; seeded reports by category and month compared with an aggregation of the app's own orders and returns |
| `v7.` | Reviews read back; validation, `403` without a (non-cancelled) order, `409` for a second review; ratings on products and in lists; hiding, republishing and editing change ratings; one helpful vote per email; newest and most helpful first, rating filter; `minRating`, `sort=rating` with ties by count |
| `v8.` | Descriptions; names first, then best sellers; prefix of the last word; plurals; every word; accents; attribute values; whole sku; category and `inStock` filters, limit, isolation; renames, cleared descriptions and new products found at once |
| `v9.` | Shop languages and validation; translations read back; reading in a language with fallback per field and option value; PATCH merging and removals; `missingTranslation`; search in a language; order lines in the order's language, frozen |
| `v10.` | Hold settings; carts hold stock (`available`), block orders, cart changes and `inStock`; renewing the hold; checkout; 10 concurrent carts for 4 units; an expired cart frees its units at once (5 s hold) |
| `v11.` | Zones and validation; pickup points nearest first with distances; the cheapest zone's fee, free from a threshold after discount; addresses outside every zone rejected; pickup; no fee without delivery; returns don't refund shipping; deleted zones |
| `v12.` | Subscriptions and validation; a run places due orders, skips when stock is short, moves `nextOn`, records history; the same date twice and 6 concurrent runs place one order; a late run places one order at the current price; pause, resume to the next date, cancel; filters |
| `v13.` | Versions; edits keep line ids and prices, take and return stock; stale versions, missing stock and invalid items change nothing; revisions; cancel and returns are changes; two concurrent edits, one wins; discounts recomputed and minimums enforced |
| `v14.` | A Craftly export with variations, a divisor, a missing title and a taken sku: counts, rejected listings, products, raw source; inactive products; re-import changes nothing; updates keep the shop's stock and add offerings; 2,000 listings within 60 s |
| `v15.` | Initial movements; orders, cancels, returns, edits and stock changes recorded in order with order ids (cart holds not); adjustments; time windows and stock at a moment; movements add up after 10 concurrent orders; imports; seeded variants have opening balances |

Leniencies: `null` counts as omitted; creation may return 200 or 201; rejection may be any 4xx except
404 unless a status is specified; amounts compare to a tenth of a cent and may be decimal strings;
email addresses compare case-insensitively.

## Storage lines in the verification

| Line | What it checks |
|---|---|
| `seeded records still in the database` | Every seeded product and order id still appears in the dump |
| `tables/collections that lost rows` | Row counts compared with the dump after `initial` |

## Testing the checks

[tests/reference_app.py](tests/reference_app.py) implements the spec for all phases in memory.
[tests/test_checks.py](tests/test_checks.py) verifies:

- a correct app passes every check in every phase, with no seed records rejected
- an app that shows the product's current sku, name and price on order lines fails exactly the
  "price at order time" checks
- an app that checks stock and discount codes, then takes them in a separate step (no transaction or
  lock), fails exactly the concurrent-order checks (stock, order numbers, max uses, once per customer)
- an app whose expired carts hold stock until a cleanup runs (a TTL index) fails exactly the expiry check
- an app that ignores `version` on edits fails exactly the version checks
- the one-shot order (`--mode merged`: seed the finished app, every check, the material backfill, every
  check again) passes every check
- the `vat` probe's checks pass on the finished reference app, next to every phase's checks; an app that
  shows tax at the current rates instead of those at order time fails exactly the two frozen-rate checks
