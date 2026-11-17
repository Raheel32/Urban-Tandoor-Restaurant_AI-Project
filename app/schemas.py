"""
Document schemas (Pydantic) - the single source of truth for what a document looks like.

Used in TWO places:
  1. ingestion  : every JSON row is validated against its model BEFORE it is written to MongoDB,
                  so the database can never contain a wrongly-typed price or a misspelled field.
  2. the API    : the same models validate request bodies and shape responses (and drive /docs).

MongoDB itself is schema-less; this file is what gives the data its discipline.
`extra="forbid"` means unknown fields are rejected - add new columns here on purpose.
"""
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, create_model

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"      # dates are stored as ISO strings: sortable and JSON friendly


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)


class TopLevel(Base):
    """Fields every top-level document may carry. Set by the API, not by the JSON files."""
    created_at: str | None = None
    updated_at: str | None = None


# ----------------------------------------------------------------------------- embedded sub-documents
class Variant(Base):
    variant_id: str
    variant_name: str | None = None
    price_pkr: float | None = Field(None, ge=0)
    recipe_multiplier_est: float | None = None
    shown_in_menu: str | None = None
    approved_for_orders: str | None = None
    operational_notes: str | None = None


class Addon(Base):
    addon_id: str
    addon_name: str | None = None
    price_pkr: float | None = Field(None, ge=0)
    category: str | None = None
    allergen_hint: str | None = None
    ingredient_id: str | None = None
    qty_ingredient_units: float | None = None
    shown_in_menu: str | None = None
    order_status: str | None = None
    mapping_id: str | None = None
    selectable: str | None = None
    operational_notes: str | None = None


class Alias(Base):
    alias_id: str
    customer_phrase: str
    language: str | None = None
    alias_notes: str | None = None


class BranchOffer(Base):
    """What one branch does with this menu item (merges the Branch_Menu and Inventory_Links sheets)."""
    branch_id: str
    branch_menu_id: str | None = None
    branch_name: str | None = None
    enabled_for_order: str | None = None
    price_pkr: float | None = Field(None, ge=0)
    base_variant_orderable: str | None = None
    stock_decision: str | None = None
    snapshot_max_portions: float | None = None
    notes: str | None = None
    recipe_linked: str | None = None
    recipe_link_status: str | None = None
    runtime_check_policy: str | None = None


class RecipeLine(Base):
    ingredient_id: str
    ingredient_name: str | None = None     # copied from `ingredients` for convenience (refreshed on every ingest)
    unit: str | None = None
    qty_per_portion: float = Field(ge=0)


class DealItem(Base):
    menu_id: str
    variant_id: str | None = None
    item_name: str | None = None
    qty: int | None = Field(None, ge=0)


# ----------------------------------------------------------------------------- top-level collections
class MenuItem(TopLevel):
    menu_id: str
    item_name: str
    category_id: str | None = None
    category: str | None = None
    description: str | None = None
    base_price_pkr: float | None = Field(None, ge=0)
    currency: str | None = None
    item_type: str | None = None
    catalog_status: str | None = None
    recipe_linked: str | None = None
    prep_time_min: int | None = Field(None, ge=0)
    dietary_hint: str | None = None
    allergen_hint: str | None = None
    allergen_verified: str | None = None
    service_window: str | None = None
    upsell_eligible: str | None = None
    slug: str | None = None
    data_provenance: str | None = None
    variants: list[Variant] = Field(default_factory=list)
    addons: list[Addon] = Field(default_factory=list)
    aliases: list[Alias] = Field(default_factory=list)
    branches: list[BranchOffer] = Field(default_factory=list)
    recipe: list[RecipeLine] = Field(default_factory=list)


class Category(TopLevel):
    category_id: str
    category_name: str
    sort_order: int | None = None
    status: str | None = None
    customer_description: str | None = None


class Deal(TopLevel):
    deal_id: str
    deal_name: str
    deal_type: str | None = None
    description: str | None = None
    deal_price_pkr: float | None = Field(None, ge=0)
    currency: str | None = None
    approval_status: str | None = None
    branches: str | None = None
    service_window: str | None = None
    min_spend_pkr: float | None = None
    stack_bank_discount: str | None = None
    policy_notes: str | None = None
    items: list[DealItem] = Field(default_factory=list)


class AllergenGuide(TopLevel):
    allergen_code: str
    display_name: str | None = None
    status: str | None = None
    use_policy: str | None = None


class ServiceRule(TopLevel):
    rule_id: str
    rule_name: str
    value: str | None = None
    applies_to: str | None = None
    verification_status: str | None = None
    comment: str | None = None


class Supplier(TopLevel):
    supplier_id: str
    supplier_name: str
    supply_category: str | None = None
    contact: str | None = None
    verification: str | None = None


class Branch(TopLevel):
    branch_id: str
    branch_name: str
    address_demo: str | None = None
    tables: int | None = Field(None, ge=0)
    seats: int | None = Field(None, ge=0)
    hours: str | None = None
    status: str | None = None


class Ingredient(TopLevel):
    ingredient_id: str
    ingredient_name: str
    category: str | None = None
    unit: str | None = None
    unit_cost_pkr: float | None = Field(None, ge=0)
    default_reorder: float | None = None
    default_target: float | None = None
    supplier_id: str | None = None
    status: str | None = None


class BranchStock(TopLevel):
    branch_id: str
    ingredient_id: str
    opening: float | None = None
    received: float | None = None
    consumed: float | None = None
    wastage: float | None = None
    adjustment: float | None = None
    reserved: float | None = None
    on_hand: float | None = None
    available: float | None = None
    reorder_level: float | None = None
    target_stock: float | None = None
    suggested_buy: float | None = None
    stock_status: str | None = None
    unit_cost_pkr: float | None = Field(None, ge=0)
    stock_value_pkr: float | None = None
    as_of_date: str | None = Field(None, pattern=DATE_PATTERN)      # Excel's "Updated_At" column


class StockMovement(TopLevel):
    movement_id: str
    movement_date: str | None = Field(None, pattern=DATE_PATTERN)
    branch_id: str
    ingredient_id: str
    movement_type: str | None = None
    qty_signed: float | None = None
    unit: str | None = None
    reference: str | None = None
    reason: str | None = None
    data_type: str | None = None


# ----------------------------------------------------------------------------- registry
@dataclass(frozen=True)
class Coll:
    name: str                    # MongoDB collection name
    model: type
    keys: tuple                  # field(s) forming the natural key -> stored as `_id`

    def make_id(self, doc: dict) -> str:
        return ":".join(str(doc[k]) for k in self.keys)


COLLECTIONS: dict = {c.name: c for c in [
    Coll("menu_items", MenuItem, ("menu_id",)),
    Coll("categories", Category, ("category_id",)),
    Coll("deals", Deal, ("deal_id",)),
    Coll("allergen_guide", AllergenGuide, ("allergen_code",)),
    Coll("service_rules", ServiceRule, ("rule_id",)),
    Coll("suppliers", Supplier, ("supplier_id",)),
    Coll("branches", Branch, ("branch_id",)),
    Coll("ingredients", Ingredient, ("ingredient_id",)),
    Coll("branch_stock", BranchStock, ("branch_id", "ingredient_id")),     # composite key -> "BRANCH:ING"
    Coll("stock_movements", StockMovement, ("movement_id",)),
]}


def make_patch_model(coll: Coll):
    """A copy of the model where every field is optional and key fields are removed (for PATCH)."""
    skip = set(coll.keys) | {"created_at", "updated_at"}
    fields = {n: (f.annotation | None, None) for n, f in coll.model.model_fields.items() if n not in skip}
    return create_model(f"{coll.model.__name__}Patch", __base__=Base, **fields)
