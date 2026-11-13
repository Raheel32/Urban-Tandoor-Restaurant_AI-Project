"""
Central configuration: settings + a declarative spec for every Excel sheet we load.

To support a NEW sheet in the future:
  1. add a CREATE TABLE to sql/schema.sql
  2. add one TableSpec to SPECS below (in dependency order: parents before children)
Nothing else changes - extract / validate / load are all driven by this list.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
INCOMING_DIR = ROOT / "data" / "incoming"
ARCHIVE_DIR = ROOT / "data" / "archive"
SCHEMA_FILE = ROOT / "sql" / "schema.sql"

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tandoor:tandoor@localhost:5432/urban_tandoor")

EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "gemini").lower()   # gemini | fake
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "20"))
EMBED_SLEEP_SECONDS = float(os.getenv("EMBED_SLEEP_SECONDS", "1.0"))

# A workbook is recognised by one sheet that only it contains (file names can change).
SIGNATURES = {"menu": "Menu_Items", "inventory": "Branch_Stock"}


@dataclass(frozen=True)
class TableSpec:
    table: str                       # Postgres table name
    workbook: str                    # "menu" | "inventory"
    sheet: str                       # Excel sheet name
    pk: tuple                        # primary-key columns (lowercase DB names)
    columns: dict                    # Excel header -> type: text | int | float | date
    fks: tuple = ()                  # ((child_cols), parent_table, (parent_cols))
    non_negative: tuple = ()         # lowercase columns that must be >= 0

    @property
    def db_columns(self) -> list:
        return [c.lower() for c in self.columns]


T, I, F, D = "text", "int", "float", "date"

# ORDER MATTERS: parents first so foreign keys are satisfied while loading.
SPECS: list = [
    # ---------------- inventory workbook: reference data ----------------
    TableSpec("suppliers", "inventory", "Suppliers", ("supplier_id",), {
        "Supplier_ID": T, "Supplier_Name": T, "Supply_Category": T, "Contact": T, "Verification": T}),
    TableSpec("branches", "inventory", "Branches", ("branch_id",), {
        "Branch_ID": T, "Branch_Name": T, "Address_Demo": T, "Tables": I, "Seats": I, "Hours": T, "Status": T}),
    TableSpec("ingredients", "inventory", "Ingredients", ("ingredient_id",), {
        "Ingredient_ID": T, "Ingredient_Name": T, "Category": T, "Unit": T, "Unit_Cost_PKR": F,
        "Default_Reorder": F, "Default_Target": F, "Supplier_ID": T, "Status": T},
        fks=((("supplier_id",), "suppliers", ("supplier_id",)),), non_negative=("unit_cost_pkr",)),

    # ---------------- menu workbook ----------------
    TableSpec("categories", "menu", "Categories", ("category_id",), {
        "Category_ID": T, "Category_Name": T, "Sort_Order": I, "Status": T, "Customer_Description": T}),
    TableSpec("menu_items", "menu", "Menu_Items", ("menu_id",), {
        "Menu_ID": T, "Item_Name": T, "Category_ID": T, "Category": T, "Description": T, "Base_Price_PKR": F,
        "Currency": T, "Item_Type": T, "Catalog_Status": T, "Recipe_Linked": T, "Prep_Time_Min": I,
        "Dietary_Hint": T, "Allergen_Hint": T, "Allergen_Verified": T, "Service_Window": T,
        "Upsell_Eligible": T, "Slug": T, "Data_Provenance": T},
        fks=((("category_id",), "categories", ("category_id",)),), non_negative=("base_price_pkr",)),
    TableSpec("variants", "menu", "Variants", ("variant_id",), {
        "Variant_ID": T, "Menu_ID": T, "Variant_Name": T, "Price_PKR": F, "Recipe_Multiplier_Est": F,
        "Shown_In_Menu": T, "Approved_For_Orders": T, "Operational_Notes": T},
        fks=((("menu_id",), "menu_items", ("menu_id",)),), non_negative=("price_pkr",)),
    TableSpec("addons", "menu", "Addons", ("addon_id",), {
        "Addon_ID": T, "Addon_Name": T, "Price_PKR": F, "Category": T, "Allergen_Hint": T,
        "Ingredient_ID": T, "Qty_Ingredient_Units": F, "Shown_In_Menu": T, "Order_Status": T},
        fks=((("ingredient_id",), "ingredients", ("ingredient_id",)),), non_negative=("price_pkr",)),
    TableSpec("item_addons", "menu", "Item_Addons", ("mapping_id",), {
        "Mapping_ID": T, "Menu_ID": T, "Addon_ID": T, "Selectable": T, "Operational_Notes": T},
        fks=((("menu_id",), "menu_items", ("menu_id",)), (("addon_id",), "addons", ("addon_id",)))),
    TableSpec("branch_menu", "menu", "Branch_Menu", ("branch_menu_id",), {
        "Branch_Menu_ID": T, "Branch_ID": T, "Branch_Name": T, "Menu_ID": T, "Enabled_For_Order": T,
        "Price_PKR": F, "Base_Variant_Orderable": T, "Stock_Decision": T, "Snapshot_Max_Portions": F, "Notes": T},
        fks=((("branch_id",), "branches", ("branch_id",)), (("menu_id",), "menu_items", ("menu_id",))),
        non_negative=("price_pkr",)),
    TableSpec("inventory_links", "menu", "Inventory_Links", ("menu_id", "branch_id"), {
        "Menu_ID": T, "Branch_ID": T, "Recipe_Linked": T, "Snapshot_Max_Portions": F,
        "Recipe_Link_Status": T, "Runtime_Check_Policy": T},
        fks=((("menu_id",), "menu_items", ("menu_id",)), (("branch_id",), "branches", ("branch_id",)))),
    TableSpec("item_aliases", "menu", "Item_Aliases", ("alias_id",), {
        "Alias_ID": T, "Menu_ID": T, "Customer_Phrase": T, "Language": T, "Alias_Notes": T},
        fks=((("menu_id",), "menu_items", ("menu_id",)),)),
    TableSpec("deals", "menu", "Deals", ("deal_id",), {
        "Deal_ID": T, "Deal_Name": T, "Deal_Type": T, "Description": T, "Deal_Price_PKR": F, "Currency": T,
        "Approval_Status": T, "Branches": T, "Service_Window": T, "Min_Spend_PKR": F,
        "Stack_Bank_Discount": T, "Policy_Notes": T}, non_negative=("deal_price_pkr",)),
    TableSpec("deal_items", "menu", "Deal_Items", ("deal_id", "menu_id", "variant_id"), {
        "Deal_ID": T, "Menu_ID": T, "Item_Name": T, "Qty": I, "Variant_ID": T},
        fks=((("deal_id",), "deals", ("deal_id",)), (("menu_id",), "menu_items", ("menu_id",)),
             (("variant_id",), "variants", ("variant_id",))), non_negative=("qty",)),
    TableSpec("allergen_guide", "menu", "Allergen_Guide", ("allergen_code",), {
        "Allergen_Code": T, "Display_Name": T, "Status": T, "Use_Policy": T}),
    TableSpec("service_rules", "menu", "Service_Rules", ("rule_id",), {
        "Rule_ID": T, "Rule_Name": T, "Value": T, "Applies_To": T, "Verification_Status": T, "Comment": T}),

    # ---------------- inventory workbook: facts ----------------
    # NOTE: Excel's Branch_Stock repeats Ingredient_Name/Category/Unit and Recipes repeats names and
    # per-branch max portions. We deliberately DROP those duplicated/derived columns (normalisation):
    # names live in `ingredients`/`menu_items`, and max portions are computed live by a SQL view.
    TableSpec("branch_stock", "inventory", "Branch_Stock", ("branch_id", "ingredient_id"), {
        "Branch_ID": T, "Ingredient_ID": T, "Opening": F, "Received": F, "Consumed": F, "Wastage": F,
        "Adjustment": F, "Reserved": F, "On_Hand": F, "Available": F, "Reorder_Level": F,
        "Target_Stock": F, "Suggested_Buy": F, "Stock_Status": T, "Unit_Cost_PKR": F,
        "Stock_Value_PKR": F, "Updated_At": D},
        fks=((("branch_id",), "branches", ("branch_id",)), (("ingredient_id",), "ingredients", ("ingredient_id",)))),
    TableSpec("recipes", "inventory", "Recipes", ("menu_id", "ingredient_id"), {
        "Menu_ID": T, "Ingredient_ID": T, "Qty_Per_Portion": F},
        fks=((("menu_id",), "menu_items", ("menu_id",)), (("ingredient_id",), "ingredients", ("ingredient_id",))),
        non_negative=("qty_per_portion",)),
    TableSpec("stock_movements", "inventory", "Stock_Movements", ("movement_id",), {
        "Movement_ID": T, "Movement_Date": D, "Branch_ID": T, "Ingredient_ID": T, "Movement_Type": T,
        "Qty_Signed": F, "Unit": T, "Reference": T, "Reason": T, "Data_Type": T},
        fks=((("branch_id",), "branches", ("branch_id",)), (("ingredient_id",), "ingredients", ("ingredient_id",)))),
]

SPEC_BY_TABLE = {s.table: s for s in SPECS}
