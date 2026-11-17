"""
DOCUMENT step - turn stored documents into natural-language text for embedding.

Only STABLE facts are embedded (names, descriptions, prices, aliases, rules, ingredients).
Live stock numbers are NOT embedded - they would be stale within hours. Use GET /availability/{branch_id}
for live facts and /search for meaning: that split is called hybrid retrieval.
"""
from dataclasses import dataclass, field


@dataclass
class Doc:
    doc_id: str
    doc_type: str
    source_collection: str
    source_id: str
    content: str
    metadata: dict = field(default_factory=dict)


def _lines(*pairs) -> str:
    return "\n".join(f"{k}: {v}" for k, v in pairs if v not in (None, "", []))


def _price(v):
    return None if v is None else f"PKR {int(round(float(v)))}"


def _join(parts) -> str | None:
    parts = [p for p in parts if p]
    return "; ".join(parts) if parts else None


def build_documents(db) -> list:
    docs: list = []
    menu = list(db["menu_items"].find({}).sort("_id"))

    for m in menu:
        shown_variants = [v for v in m.get("variants", []) if v.get("shown_in_menu", "Yes") == "Yes"]
        offers = [f"{b.get('branch_name', b['branch_id'])} ({'orderable' if b.get('enabled_for_order') == 'Yes' else 'not orderable'})"
                  for b in m.get("branches", [])]
        content = _lines(
            ("Menu item", f"{m['item_name']} ({m['menu_id']})"),
            ("Category", m.get("category")),
            ("Description", m.get("description")),
            ("Price", _price(m.get("base_price_pkr"))),
            ("Sizes / variants", _join(f"{v.get('variant_name', '')} {_price(v.get('price_pkr')) or ''}".strip() for v in shown_variants)),
            ("Add-ons", _join(f"{a.get('addon_name')} {_price(a.get('price_pkr')) or ''}".strip()
                              for a in m.get("addons", []) if a.get("selectable", "Yes") == "Yes")),
            ("Preparation time", f"{m['prep_time_min']} minutes" if m.get("prep_time_min") else None),
            ("Dietary", m.get("dietary_hint")),
            ("Allergens", f"{m['allergen_hint']} (verification: {m.get('allergen_verified')})" if m.get("allergen_hint") else None),
            ("Service window", m.get("service_window")),
            ("Also called", ", ".join(sorted({a["customer_phrase"] for a in m.get("aliases", [])})) or None),
            ("Main ingredients", ", ".join(sorted({r.get("ingredient_name") or r["ingredient_id"] for r in m.get("recipe", [])})) or None),
            ("Branches", ", ".join(offers) or None),
            ("Catalog status", m.get("catalog_status")),
        )
        docs.append(Doc(f"menu_item:{m['menu_id']}", "menu_item", "menu_items", m["_id"], content, {
            "menu_id": m["menu_id"], "item_name": m["item_name"], "category": m.get("category"),
            "base_price_pkr": m.get("base_price_pkr"), "dietary_hint": m.get("dietary_hint"),
            "catalog_status": m.get("catalog_status"), "service_window": m.get("service_window")}))

    for d in db["deals"].find({}).sort("_id"):
        content = _lines(
            ("Deal", f"{d['deal_name']} ({d['deal_id']})"), ("Type", d.get("deal_type")),
            ("Description", d.get("description")),
            ("Includes", ", ".join(f"{i.get('qty', 1)} x {i.get('item_name') or i['menu_id']}" for i in d.get("items", [])) or None),
            ("Price", _price(d.get("deal_price_pkr"))), ("Branches", d.get("branches")),
            ("Service window", d.get("service_window")), ("Minimum spend", _price(d.get("min_spend_pkr"))),
            ("Can stack with bank discount", d.get("stack_bank_discount")),
            ("Approval status", d.get("approval_status")), ("Policy notes", d.get("policy_notes")))
        docs.append(Doc(f"deal:{d['deal_id']}", "deal", "deals", d["_id"], content, {
            "deal_id": d["deal_id"], "deal_name": d["deal_name"], "deal_price_pkr": d.get("deal_price_pkr"),
            "approval_status": d.get("approval_status")}))

    for r in db["service_rules"].find({}).sort("_id"):
        content = _lines(("Restaurant rule", r["rule_name"]), ("Value", r.get("value")), ("Applies to", r.get("applies_to")),
                         ("Verification", r.get("verification_status")), ("Comment", r.get("comment")))
        docs.append(Doc(f"service_rule:{r['rule_id']}", "service_rule", "service_rules", r["_id"], content,
                        {"rule_id": r["rule_id"], "rule_name": r["rule_name"]}))

    for a in db["allergen_guide"].find({}).sort("_id"):
        content = _lines(("Allergen", f"{a.get('display_name', a['allergen_code'])} ({a['allergen_code']})"),
                         ("Status", a.get("status")), ("Policy", a.get("use_policy")))
        docs.append(Doc(f"allergen:{a['allergen_code']}", "allergen", "allergen_guide", a["_id"], content,
                        {"allergen_code": a["allergen_code"]}))

    suppliers = {s["_id"]: s.get("supplier_name") for s in db["suppliers"].find({})}
    used_in: dict = {}
    for m in menu:
        for r in m.get("recipe", []):
            used_in.setdefault(r["ingredient_id"], set()).add(m["item_name"])
    for i in db["ingredients"].find({}).sort("_id"):
        content = _lines(("Ingredient", f"{i['ingredient_name']} ({i['ingredient_id']})"), ("Category", i.get("category")),
                         ("Unit", i.get("unit")), ("Supplier", suppliers.get(i.get("supplier_id"))),
                         ("Used in dishes", ", ".join(sorted(used_in.get(i["_id"], []))) or None))
        docs.append(Doc(f"ingredient:{i['ingredient_id']}", "ingredient", "ingredients", i["_id"], content,
                        {"ingredient_id": i["ingredient_id"], "category": i.get("category")}))
    return docs
