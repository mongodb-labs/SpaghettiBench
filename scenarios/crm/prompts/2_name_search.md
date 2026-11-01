# Finding contacts by name

Colleagues can't find contacts they know are there: searching for `garcia` doesn't find "García",
and `MULLER` doesn't find "Müller".

Change the `lastName` filter of `GET /contacts` so that it ignores case and accents: a letter with an
accent or other mark matches the same letter without it (é and e, ü and u, ñ and n, ç and c).
- `?lastName=garcia`, `?lastName=GARCIA` and `?lastName=García` all find "García" and "Garcia".
- It still matches the whole last name: `?lastName=garc` finds nobody.
- Different letters stay different: `?lastName=muller` finds "Müller" but not "Mueller".
- Nothing else about the contact list or its other filters changes.
