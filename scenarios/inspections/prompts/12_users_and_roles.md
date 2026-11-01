# Users and permissions

Until now every company's tablets and dashboards have used the API without users. Companies now
want to know who did what, and to limit what inspectors can do.

## 1. Users
`POST /companies/:companyId/users`
```json
{ "name": "Kofi Mensah", "email": "kofi@tidewater.example", "role": "inspector" }
```
- `name` and `email` (exactly one `@`, text on both sides) are required; `role` is `inspector` or
  `manager`. Invalid → `400`. An email already used in the company (ignoring case) → `409`.
- → `201` with the user, including `id` and `companyId`.

`GET /companies/:companyId/users` → the company's users.

## 2. Acting as a user
Requests may carry an `X-User-Id` header with a user id. Requests without it come from our own
integrations and keep working exactly as today.

With `X-User-Id`:
- An unknown user → `401`.
- Users only see and change their own company's data: forms, submissions, items, findings, users,
  reports and searches of another company → `403`.
- Inspectors can't create or edit forms or users (`403`), and can change a finding's `status` only
  if they are its `assignee` (`403` otherwise). Everything else is allowed.
- Managers can do everything within their company.
- When adding a comment, `author` may be left out; it is then the user's id.
- `GET /findings` accepts `assignee=me`: the findings assigned to the user (`400` without
  `X-User-Id`).
