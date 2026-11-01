# Order numbers

Customers call about "my order", and nobody can read our order ids out over the phone.

- Every order gets a `number`: per shop, 1001 for the shop's first order, then 1002, and so on, in
  the order the orders were placed.
- Numbers are never reused or skipped: a rejected order doesn't take a number, and a cancelled order
  keeps its number. Orders placed at the same moment still get distinct numbers, without gaps.
- Number the existing orders the same way, by `createdAt`. New orders continue from there.
- `number` appears wherever orders appear.
- New order filter: `number`, e.g. `GET /orders?shopId=clay-and-kiln&number=1042`.
