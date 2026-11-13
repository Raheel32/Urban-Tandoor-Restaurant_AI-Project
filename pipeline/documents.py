"""
DOCUMENT step - turn relational rows into natural-language documents for embedding.

Design choices:
  * Documents are built FROM THE DATABASE (not from Excel) so they always match the loaded data.
  * Only STABLE facts are embedded (names, descriptions, prices, aliases, rules).
    Volatile numbers (live stock) are NOT embedded - they would go stale within hours.
    Query them live via the v_menu_live_availability view instead (hybrid retrieval).
  * Each doc has a deterministic doc_id, so re-runs update the same row.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from psycopg2.extras import RealDictCursor


@dataclass
class Doc:
    doc_id: str
    doc_type: str
    source_table: str
    source_id: str
    content: str
    metadata: dict = field(default_factory=dict)


def _lines(*pairs) -> str:
    """Join 'Label: value' lines, skipping empty values."""
    return "\n".join(f"{k}: {v}" for k, v in pairs if v not in (None, "", "None"))


def _price(v):
    return None if v is None else f"PKR {int(round(float(v)))}"


MENU_SQL = """
SELECT m.*,
  (SELECT string_agg(v.variant_name || ' ' || COALESCE('PKR ' || ROUND(v.price_pkr)::int, ''), '; '
                     ORDER BY v.variant_id)
     FROM variants v WHERE v.menu_id = m.menu_id AND v.shown_in_menu = 'Yes')              AS variants,
  (SELECT string_agg(a.addon_name || ' PKR ' || ROUND(a.price_pkr)::int, '; ' ORDER BY a.addon_id)
     FROM item_addons ia JOIN addons a ON a.addon_id = ia.addon_id
     WHERE ia.menu_id = m.menu_id AND ia.selectable = 'Yes')                               AS addons,
  (SELECT string_agg(DISTINCT al.customer_phrase, ', ' ORDER BY al.customer_phrase)
     FROM item_aliases al WHERE al.menu_id = m.menu_id)                                    AS aliases,
  (SELECT string_agg(b.branch_name || ' (' ||
                     CASE WHEN bm.enabled_for_order = 'Yes' THEN 'orderable' ELSE 'not orderable' END || ')',
                     ', ' ORDER BY b.branch_id)
     FROM branch_menu bm JOIN branches b ON b.branch_id = bm.branch_id
     WHERE bm.menu_id = m.menu_id)                                                         AS branch_info,
  (SELECT string_agg(i.ingredient_name, ', ' ORDER BY i.ingredient_name)
     FROM recipes r JOIN ingredients i ON i.ingredient_id = r.ingredient_id
     WHERE r.menu_id = m.menu_id)                                                          AS ingredients
FROM menu_items m
ORDER BY m.menu_id
"""

DEAL_SQL = """
SELECT d.*,
  (SELECT string_agg(di.qty || ' x ' || COALESCE(di.item_name, di.menu_id), ', ' ORDER BY di.menu_id)
     FROM deal_items di WHERE di.deal_id = d.deal_id) AS contents
FROM deals d ORDER BY d.deal_id
"""

INGREDIENT_SQL = """
SELECT i.*, s.supplier_name,
  (SELECT string_agg(DISTINCT m.item_name, ', ' ORDER BY m.item_name)
     FROM recipes r JOIN menu_items m ON m.menu_id = r.menu_id
     WHERE r.ingredient_id = i.ingredient_id) AS used_in
FROM ingredients i LEFT JOIN suppliers s ON s.supplier_id = i.supplier_id
ORDER BY i.ingredient_id
"""


def build_documents(conn) -> list:
    docs: list = []
    with conn.cursor(cursor_factory=RealDictCursor) as cur:

        cur.execute(MENU_SQL)
        for r in cur.fetchall():
            content = _lines(
                ("Menu item", f"{r['item_name']} ({r['menu_id']})"),
                ("Category", r["category"]),
                ("Description", r["description"]),
                ("Price", _price(r["base_price_pkr"])),
                ("Sizes / variants", r["variants"]),
                ("Add-ons", r["addons"]),
                ("Preparation time", f"{r['prep_time_min']} minutes" if r["prep_time_min"] else None),
                ("Dietary", r["dietary_hint"]),
                ("Allergens", f"{r['allergen_hint']} (verification: {r['allergen_verified']})"
                 if r["allergen_hint"] else None),
                ("Service window", r["service_window"]),
                ("Also called", r["aliases"]),
                ("Main ingredients", r["ingredients"]),
                ("Branches", r["branch_info"]),
                ("Catalog status", r["catalog_status"]),
            )
            docs.append(Doc(f"menu_item:{r['menu_id']}", "menu_item", "menu_items", r["menu_id"], content, {
                "menu_id": r["menu_id"], "item_name": r["item_name"], "category": r["category"],
                "base_price_pkr": float(r["base_price_pkr"]) if r["base_price_pkr"] is not None else None,
                "dietary_hint": r["dietary_hint"], "catalog_status": r["catalog_status"],
                "service_window": r["service_window"]}))

        cur.execute(DEAL_SQL)
        for r in cur.fetchall():
            content = _lines(
                ("Deal", f"{r['deal_name']} ({r['deal_id']})"),
                ("Type", r["deal_type"]),
                ("Description", r["description"]),
                ("Includes", r["contents"]),
                ("Price", _price(r["deal_price_pkr"])),
                ("Branches", r["branches"]),
                ("Service window", r["service_window"]),
                ("Minimum spend", _price(r["min_spend_pkr"])),
                ("Can stack with bank discount", r["stack_bank_discount"]),
                ("Approval status", r["approval_status"]),
                ("Policy notes", r["policy_notes"]),
            )
            docs.append(Doc(f"deal:{r['deal_id']}", "deal", "deals", r["deal_id"], content, {
                "deal_id": r["deal_id"], "deal_name": r["deal_name"],
                "deal_price_pkr": float(r["deal_price_pkr"]) if r["deal_price_pkr"] is not None else None,
                "approval_status": r["approval_status"]}))

        cur.execute("SELECT * FROM service_rules ORDER BY rule_id")
        for r in cur.fetchall():
            content = _lines(
                ("Restaurant rule", r["rule_name"]), ("Value", r["value"]), ("Applies to", r["applies_to"]),
                ("Verification", r["verification_status"]), ("Comment", r["comment"]))
            docs.append(Doc(f"service_rule:{r['rule_id']}", "service_rule", "service_rules", r["rule_id"],
                            content, {"rule_id": r["rule_id"], "rule_name": r["rule_name"]}))

        cur.execute("SELECT * FROM allergen_guide ORDER BY allergen_code")
        for r in cur.fetchall():
            content = _lines(
                ("Allergen", f"{r['display_name']} ({r['allergen_code']})"),
                ("Status", r["status"]), ("Policy", r["use_policy"]))
            docs.append(Doc(f"allergen:{r['allergen_code']}", "allergen", "allergen_guide", r["allergen_code"],
                            content, {"allergen_code": r["allergen_code"]}))

        cur.execute(INGREDIENT_SQL)
        for r in cur.fetchall():
            content = _lines(
                ("Ingredient", f"{r['ingredient_name']} ({r['ingredient_id']})"),
                ("Category", r["category"]), ("Unit", r["unit"]),
                ("Supplier", r["supplier_name"]), ("Used in dishes", r["used_in"]))
            docs.append(Doc(f"ingredient:{r['ingredient_id']}", "ingredient", "ingredients", r["ingredient_id"],
                            content, {"ingredient_id": r["ingredient_id"], "category": r["category"]}))
    return docs
