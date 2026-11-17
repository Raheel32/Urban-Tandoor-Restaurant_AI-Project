"""
TRANSFORM step: flat JSON sheets (like SQL tables)  ->  MongoDB documents (embedded).

Relational shape                          Document shape
  Menu_Items + Variants + Item_Addons       menu_items  { ..., variants[], addons[], aliases[],
  + Addons + Item_Aliases + Branch_Menu                     branches[], recipe[] }
  + Inventory_Links + Recipes
  Deals + Deal_Items                        deals       { ..., items[] }
  everything else                           one collection per sheet (data that is used on its own)

Rule of thumb: EMBED what is read together and belongs to one parent (a dish and its sizes).
KEEP SEPARATE what has its own life (stock levels change daily, branches/suppliers are shared).

MongoDB has no foreign keys, so referential integrity is checked HERE, before anything is written.
Returns (documents_by_collection, error_messages). Any error => the ingest stops, nothing is written.
"""
import math
from collections import defaultdict

from pydantic import ValidationError

from .schemas import COLLECTIONS

MENU_SHEETS = ["Menu_Items", "Categories", "Variants", "Addons", "Item_Addons", "Branch_Menu",
               "Inventory_Links", "Item_Aliases", "Deals", "Deal_Items", "Allergen_Guide", "Service_Rules"]
INVENTORY_SHEETS = ["Branches", "Ingredients", "Branch_Stock", "Recipes", "Stock_Movements", "Suppliers"]

# Columns copied onto stock rows by Excel for convenience; the real values live in `ingredients`.
BRANCH_STOCK_DROP = {"ingredient_name", "category", "unit"}


def clean_row(row: dict, drop=()) -> dict:
    """lowercase keys, trim strings, drop empty values, round float noise (42.690000000000005 -> 42.69)."""
    out = {}
    for key, value in row.items():
        key = str(key).strip().lower()
        if key in drop:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        elif value is None or (isinstance(value, float) and math.isnan(value)):
            continue
        elif isinstance(value, float):
            value = round(value, 4)
        out[key] = value
    return out


def _group(rows: list, key: str) -> dict:
    grouped = defaultdict(list)
    for r in rows:
        grouped[r.get(key)].append(r)
    return grouped


def _without(row: dict, *keys) -> dict:
    return {k: v for k, v in row.items() if k not in keys}


def _sample(items, n=4) -> str:
    items = sorted(map(str, items))
    return ", ".join(items[:n]) + (f" (+{len(items) - n} more)" if len(items) > n else "")


def build_collections(menu: dict, inventory: dict):
    errors: list = []

    # ---- all required sheets present? ----
    for wb, sheets, label in ((menu, MENU_SHEETS, "menu"), (inventory, INVENTORY_SHEETS, "inventory")):
        for sheet in sheets:
            if sheet not in wb:
                errors.append(f"{label} file: sheet '{sheet}' is missing")
    if errors:
        return {}, errors

    def rows(wb, sheet):
        return [clean_row(r, drop=BRANCH_STOCK_DROP if sheet == "Branch_Stock" else ()) for r in wb[sheet]]

    items = rows(menu, "Menu_Items")
    categories = rows(menu, "Categories")
    variants = rows(menu, "Variants")
    addon_master = {r.get("addon_id"): r for r in rows(menu, "Addons")}
    item_addons = rows(menu, "Item_Addons")
    branch_menu = rows(menu, "Branch_Menu")
    links = rows(menu, "Inventory_Links")
    aliases = rows(menu, "Item_Aliases")
    deals = rows(menu, "Deals")
    deal_items = rows(menu, "Deal_Items")
    allergens = rows(menu, "Allergen_Guide")
    rules = rows(menu, "Service_Rules")

    branches = rows(inventory, "Branches")
    ingredients = rows(inventory, "Ingredients")
    stock = rows(inventory, "Branch_Stock")
    recipes = rows(inventory, "Recipes")
    movements = rows(inventory, "Stock_Movements")
    suppliers = rows(inventory, "Suppliers")

    for r in stock:                                   # keep the model's field name free of the audit field name
        if "updated_at" in r:
            r["as_of_date"] = r.pop("updated_at")

    menu_ids = {r.get("menu_id") for r in items}
    ingredient_by_id = {r.get("ingredient_id"): r for r in ingredients}

    # ---- children must point at an existing parent (otherwise grouping would silently drop them) ----
    def check_orphans(label, grouped, parents):
        orphans = set(grouped) - set(parents)
        if orphans:
            errors.append(f"{label}: {sum(len(grouped[o]) for o in orphans)} row(s) reference unknown parent: {_sample(orphans)}")

    variants_by = _group(variants, "menu_id")
    mappings_by = _group(item_addons, "menu_id")
    aliases_by = _group(aliases, "menu_id")
    bm_by = _group(branch_menu, "menu_id")
    links_by = _group(links, "menu_id")
    recipes_by = _group(recipes, "menu_id")
    for label, grouped in (("Variants", variants_by), ("Item_Addons", mappings_by), ("Item_Aliases", aliases_by),
                           ("Branch_Menu", bm_by), ("Inventory_Links", links_by), ("Recipes", recipes_by)):
        check_orphans(label, grouped, menu_ids)

    deal_ids = {r.get("deal_id") for r in deals}
    deal_items_by = _group(deal_items, "deal_id")
    check_orphans("Deal_Items", deal_items_by, deal_ids)

    # ---- build menu_items with embedded lists ----
    menu_docs = []
    for item in items:
        mid = item.get("menu_id")
        doc = dict(item)
        doc["variants"] = [_without(v, "menu_id") for v in variants_by.get(mid, [])]

        embedded_addons = []
        for m in mappings_by.get(mid, []):
            master = addon_master.get(m.get("addon_id"))
            if master is None:
                errors.append(f"Item_Addons: {m.get('mapping_id')} references unknown addon {m.get('addon_id')}")
                continue
            embedded_addons.append({**master, **_without(m, "menu_id")})
        doc["addons"] = embedded_addons

        doc["aliases"] = [_without(a, "menu_id") for a in aliases_by.get(mid, [])]

        offers = {}
        for b in bm_by.get(mid, []):
            offers[b.get("branch_id")] = _without(b, "menu_id")
        for l in links_by.get(mid, []):
            merged = offers.setdefault(l.get("branch_id"), {"branch_id": l.get("branch_id")})
            for k, v in _without(l, "menu_id", "branch_id").items():
                merged.setdefault(k, v)                 # Branch_Menu wins; Inventory_Links fills the gaps
        doc["branches"] = list(offers.values())

        lines = []
        for rc in recipes_by.get(mid, []):
            ing = ingredient_by_id.get(rc.get("ingredient_id"))
            if ing is None:
                errors.append(f"Recipes: {mid} uses unknown ingredient {rc.get('ingredient_id')}")
                continue
            lines.append({"ingredient_id": rc["ingredient_id"], "ingredient_name": ing.get("ingredient_name"),
                          "unit": ing.get("unit"), "qty_per_portion": rc.get("qty_per_portion")})
        doc["recipe"] = lines
        menu_docs.append(doc)

    deal_docs = []
    for d in deals:
        doc = dict(d)
        doc["items"] = [_without(i, "deal_id") for i in deal_items_by.get(d.get("deal_id"), [])]
        deal_docs.append(doc)

    raw = {
        "menu_items": menu_docs, "categories": categories, "deals": deal_docs,
        "allergen_guide": allergens, "service_rules": rules, "suppliers": suppliers, "branches": branches,
        "ingredients": ingredients, "branch_stock": stock, "stock_movements": movements,
    }

    # ---- validate every document against its Pydantic model, build _id ----
    result: dict = {}
    for name, raw_docs in raw.items():
        coll = COLLECTIONS[name]
        good, seen = [], set()
        for d in raw_docs:
            try:
                valid = coll.model.model_validate(d).model_dump(exclude_none=True)
            except ValidationError as exc:
                where = d.get(coll.keys[0], "?")
                for e in exc.errors()[:3]:
                    errors.append(f"{name} [{where}]: {'.'.join(map(str, e['loc']))}: {e['msg']}")
                continue
            valid["_id"] = coll.make_id(valid)
            if valid["_id"] in seen:
                errors.append(f"{name}: duplicate id {valid['_id']}")
                continue
            seen.add(valid["_id"])
            good.append(valid)
        result[name] = good

    # ---- references between collections ----
    ids = {name: {d["_id"] for d in docs} for name, docs in result.items()}

    def check_ref(coll, field, target, getter=None):
        bad = set()
        for d in result[coll]:
            for value in (getter(d) if getter else [d.get(field)]):
                if value is not None and value not in ids[target]:
                    bad.add(value)
        if bad:
            errors.append(f"{coll}.{field} -> {target}: unknown value(s) {_sample(bad)}")

    check_ref("menu_items", "category_id", "categories")
    check_ref("ingredients", "supplier_id", "suppliers")
    check_ref("menu_items", "branches.branch_id", "branches", lambda d: [b["branch_id"] for b in d["branches"]])
    check_ref("menu_items", "addons.ingredient_id", "ingredients", lambda d: [a.get("ingredient_id") for a in d["addons"]])
    check_ref("deals", "items.menu_id", "menu_items", lambda d: [i["menu_id"] for i in d["items"]])
    check_ref("branch_stock", "branch_id", "branches")
    check_ref("branch_stock", "ingredient_id", "ingredients")
    check_ref("stock_movements", "branch_id", "branches")
    check_ref("stock_movements", "ingredient_id", "ingredients")

    variant_ids = {v["variant_id"] for d in result["menu_items"] for v in d["variants"]}
    bad_variants = {i["variant_id"] for d in result["deals"] for i in d["items"]
                    if i.get("variant_id") and i["variant_id"] not in variant_ids}
    if bad_variants:
        errors.append(f"deals.items.variant_id -> variants: unknown value(s) {_sample(bad_variants)}")

    return result, errors
