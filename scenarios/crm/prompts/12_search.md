# Search

Colleagues want one search box that finds contacts, companies and deals.

`GET /search?agencyId=harbor-realty&q=garcia sol` →
```json
{ "results": [ { "kind": "contact", "id": "…", "title": "María García" } ] }
```
- `kind` is `contact`, `company` or `deal`; `title` is the contact's full name, the company's name or
  the deal's title. At most `limit` results (default 20, max 100). `q` is required (`400`).
- What is searched:
  - contacts: first and last name, title, email addresses, the names of their companies, and the
    subjects of their emails and texts of their notes (activities);
  - companies: name and domain;
  - deals: title and the full name of their contact.
- Matching works by word, ignoring case and accents (`garcia` finds García). Every word of `q` must
  match somewhere in the record's searched text. A word also matches other grammatical forms of the
  same English word (`apartment` and `apartments`). A whole email address in `q` finds the contacts
  with that address.
- Most relevant first: records whose own name or title contains all the words come before records that
  match only through other text. Otherwise the order is up to you.
- Search always reflects the current data (renames, new records, merges: a merged-away contact is
  never found).
