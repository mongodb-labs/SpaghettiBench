"""Checks introduced by the `translations` phase: shop languages and translated products."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get, get_id, listing, ok, rejected
from scenarios.shop.checks.helpers import by_variant, fresh_shop, items_of, order_body, place, variants_of


def v9(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v9")
    st = s.state
    sh = fresh_shop(c, "porto-langs")

    def languages():
        assert get(c, f"/shops/{sh}/languages") == {"default": "en", "others": []}, "default languages"
        for bad in ({"default": "EN", "others": []}, {"default": "en", "others": ["en"]},
                    {"default": "en", "others": ["portuguese"]}):
            rejected(c.put(f"/shops/{sh}/languages", json=bad), f"languages {bad}")
        ok(c.put(f"/shops/{sh}/languages", json={"default": "en", "others": ["pt", "de"]}), "PUT languages")
        got = get(c, f"/shops/{sh}/languages")
        assert got.get("default") == "en" and sorted(got.get("others") or []) == ["de", "pt"], f"languages {got}"
    s.check("shop_languages", languages)

    def fixtures():
        body = {"shopId": sh, "name": "Speckled mug", "category": "mugs", "description": "A big mug",
                "options": ["colour"],
                "variants": [{"sku": "L-BLACK", "options": {"colour": "black"}, "price": 20, "stock": 20},
                             {"sku": "L-WHITE", "options": {"colour": "white"}, "price": 20, "stock": 20}],
                "translations": {"pt": {"name": "Caneca salpicada", "options": {"black": "preto"}}}}
        for bad in ({"fr": {"name": "Tasse"}}, {"pt": {"name": ""}}):
            rejected(c.post("/products", json={**body, "translations": bad}), f"translations {bad}")
        p = created(c.post("/products", json=body), "translated product")
        assert (p.get("translations") or {}).get("pt", {}).get("name") == "Caneca salpicada", f"translations {p.get('translations')}"
        plain = created(c.post("/products", json={"shopId": sh, "sku": "L-BOWL", "name": "Bowl", "category": "bowls",
                                                   "price": 10, "stock": 5}), "plain product")
        assert plain.get("translations") == {}, f"translations without any: {plain.get('translations')!r}"
        st.update(p=get_id(p), plain=get_id(plain), v=variants_of(p))
    s.check("create_with_translations", fixtures)

    def view(lang: str | None, pid: str | None = None) -> dict:
        return get(c, f"/products/{pid or s.need('p')}", {"lang": lang} if lang else None)

    def colours(p: dict) -> dict[str, str]:
        return {v.get("sku"): (v.get("options") or {}).get("colour") for v in p.get("variants") or []}

    def reading():
        pt = view("pt")
        assert (pt.get("name"), pt.get("description")) == ("Caneca salpicada", "A big mug"), \
            f"pt: {pt.get('name')!r}, {pt.get('description')!r} (description falls back)"
        assert colours(pt) == {"L-BLACK": "preto", "L-WHITE": "white"}, f"pt option values {colours(pt)}"
        de = view("de")
        assert de.get("name") == "Speckled mug" and colours(de)["L-BLACK"] == "black", "de falls back to the default"
        assert view(None).get("name") == "Speckled mug", "without lang"
        rejected(c.get(f"/products/{s.need('p')}", params={"lang": "fr"}), "unknown language")
        listed = {get_id(x): x.get("name") for x in listing(c, "/products", {"shopId": sh, "lang": "pt"})}
        assert listed.get(s.need("p")) == "Caneca salpicada" and listed.get(s.need("plain")) == "Bowl", f"list in pt {listed}"
    s.check("read_in_a_language", reading)

    def merging():
        pid = s.need("p")
        ok(c.patch(f"/products/{pid}", json={"translations": {"pt": {"description": "Caneca grande"},
                                                               "de": {"name": "Gesprenkelte Tasse"}}}), "PATCH merge")
        pt, de = view("pt"), view("de")
        assert (pt.get("name"), pt.get("description")) == ("Caneca salpicada", "Caneca grande"), "PATCH lost pt fields"
        assert colours(pt)["L-BLACK"] == "preto", "PATCH lost pt options"
        assert de.get("name") == "Gesprenkelte Tasse", "de name"
        ok(c.patch(f"/products/{pid}", json={"translations": {"pt": {"name": None}, "de": None}}), "PATCH removals")
        got = get(c, f"/products/{pid}").get("translations") or {}
        assert "de" not in got and "name" not in got.get("pt", {}) and got["pt"].get("description") == "Caneca grande", \
            f"translations after removals {got}"
        assert view("pt").get("name") == "Speckled mug", "removed pt name should fall back"
        rejected(c.patch(f"/products/{pid}", json={"translations": {"fr": {"name": "x"}}}), "unknown language")
    s.check("patch_merges_per_language", merging)

    def missing():
        pid, plain = s.need("p"), s.need("plain")
        ok(c.patch(f"/products/{pid}", json={"translations": {"pt": {"name": "Caneca salpicada"}}}), "pt name again")
        got = {get_id(x) for x in listing(c, "/products", {"shopId": sh, "missingTranslation": "pt"})}
        assert got == {plain}, "missingTranslation=pt"
        got = {get_id(x) for x in listing(c, "/products", {"shopId": sh, "missingTranslation": "de"})}
        assert got == {pid, plain}, "missingTranslation=de"
    s.check("missing_translation_filter", missing)

    def search():
        pid = s.need("p")
        for q in ("caneca", "salpicada", "canec"):
            body = get(c, "/products/search", {"shopId": sh, "q": q, "lang": "pt"})
            assert [get_id(x) for x in body.get("results") or []] == [pid], f"search {q!r} in pt"
        body = get(c, "/products/search", {"shopId": sh, "q": "speckled", "lang": "pt"})
        assert not body.get("results"), "search in pt found the English name although pt has its own"
        first = (get(c, "/products/search", {"shopId": sh, "q": "caneca", "lang": "pt"}).get("results") or [{}])[0]
        assert first.get("name") == "Caneca salpicada", "search results not in the language"
    s.check("search_in_a_language", search)

    def orders():
        pid, v = s.need("p"), s.need("v")
        o = place(c, order_body(sh, "ana@example.pt", [by_variant(get_id(v["L-BLACK"]))], lang="pt"))
        line = items_of(o)[0]
        assert line.get("name") == "Caneca salpicada" and (line.get("options") or {}).get("colour") == "preto", \
            f"order line in pt: {line.get('name')!r}, {line.get('options')}"
        ok(c.patch(f"/products/{pid}", json={"translations": {"pt": {"name": "Caneca nova"}}}), "rename in pt")
        line = items_of(get(c, f"/orders/{get_id(o)}"))[0]
        assert line.get("name") == "Caneca salpicada", "order line changed with the product's translation"
        rejected(c.post("/orders", json=order_body(sh, "ana@example.pt", [by_variant(get_id(v["L-BLACK"]))], lang="fr")),
                 "order in an unknown language")
    s.check("orders_in_a_language", orders)
    return s.results
