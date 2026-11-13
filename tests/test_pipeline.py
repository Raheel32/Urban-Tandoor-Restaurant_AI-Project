"""Fast tests - no database or API key needed.   Run:  pytest -q"""
import numpy as np
import pandas as pd

from pipeline import config
from pipeline.config import SPEC_BY_TABLE, SPECS
from pipeline.embeddings import FakeEmbedder
from pipeline.extract import read_table
from pipeline.validate import validate_batch


def _xl(tmp_path, sheets: dict):
    path = tmp_path / "t.xlsx"
    with pd.ExcelWriter(path) as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name, index=False)
    return pd.ExcelFile(path)


def test_specs_are_in_dependency_order():
    seen = set()
    for spec in SPECS:
        for _cols, parent, _pcols in spec.fks:
            assert parent in seen, f"{spec.table} is listed before its parent {parent}"
        seen.add(spec.table)


def test_blank_cells_become_null_not_the_string_nan(tmp_path):
    df = pd.DataFrame({"Addon_ID": ["A1", "A2"], "Addon_Name": ["x", "y"], "Price_PKR": [10, 20],
                       "Category": ["c", "c"], "Allergen_Hint": [None, "Milk"], "Ingredient_ID": [None, "ING1"],
                       "Qty_Ingredient_Units": [None, 0.5], "Shown_In_Menu": ["Yes", "Yes"], "Order_Status": ["a", "b"]})
    out, issues = read_table(_xl(tmp_path, {"Addons": df}), SPEC_BY_TABLE["addons"])
    assert not issues
    assert out.loc[0, "ingredient_id"] is None
    assert out.loc[1, "ingredient_id"] == "ING1"


def test_non_numeric_price_is_reported_with_excel_row(tmp_path):
    df = pd.DataFrame({"Category_ID": ["C1"], "Category_Name": ["x"], "Sort_Order": ["oops"],
                       "Status": ["Active"], "Customer_Description": ["d"]})
    _out, issues = read_table(_xl(tmp_path, {"Categories": df}), SPEC_BY_TABLE["categories"])
    assert any("non-numeric" in i.message and "2" in i.message for i in issues)


def test_missing_column_is_an_error(tmp_path):
    df = pd.DataFrame({"Category_ID": ["C1"]})
    out, issues = read_table(_xl(tmp_path, {"Categories": df}), SPEC_BY_TABLE["categories"])
    assert out is None and issues[0].level == "ERROR"


def test_duplicate_primary_key_and_orphan_fk_detected():
    cats = pd.DataFrame({"category_id": ["C1", "C1"], "category_name": ["a", "b"],
                         "sort_order": [1, 2], "status": ["x", "x"], "customer_description": ["", ""]})
    items = pd.DataFrame({"menu_id": ["M1"], "category_id": ["NOPE"]})
    # build minimally-shaped frames containing the columns the validator touches
    full_items = pd.DataFrame({c: [None] for c in SPEC_BY_TABLE["menu_items"].db_columns})
    full_items["menu_id"], full_items["category_id"] = "M1", "NOPE"
    full_items["base_price_pkr"] = 10
    msgs = [str(i) for i in validate_batch({"categories": cats, "menu_items": full_items})]
    assert any("duplicate primary keys" in m for m in msgs)
    assert any("reference missing categories" in m for m in msgs)


def test_fake_embedder_is_deterministic_normalised_and_lexically_sensible():
    e = FakeEmbedder()
    a, b, c = e.embed_documents(["chicken biryani rice", "chicken biryani rice", "chocolate lava cake"])
    assert a == b
    assert abs(np.linalg.norm(a) - 1) < 1e-6
    q = e.embed_query("biryani")
    assert np.dot(q, a) > np.dot(q, c)


# ---------------------------------------------------------------- JSON input
import json

from pipeline.extract import JsonWorkbook, discover, read_table as _read_table


def _write_json(path, sheets):
    path.write_text(json.dumps(sheets), encoding="utf-8")
    return path


def test_json_workbook_reads_like_excel_and_keeps_nulls(tmp_path):
    p = _write_json(tmp_path / "w.json", {"Addons": [
        {"Addon_ID": "A1", "Addon_Name": "x", "Price_PKR": 10, "Category": "c", "Allergen_Hint": None,
         "Ingredient_ID": None, "Qty_Ingredient_Units": None, "Shown_In_Menu": "Yes", "Order_Status": "a"},
        {"Addon_ID": "A2", "Addon_Name": "y", "Price_PKR": 20.5, "Category": "c", "Allergen_Hint": "Milk",
         "Ingredient_ID": "ING1", "Qty_Ingredient_Units": 0.5, "Shown_In_Menu": "Yes", "Order_Status": "b"}]})
    wb = JsonWorkbook(p)
    out, issues = _read_table(wb, SPEC_BY_TABLE["addons"])
    assert not issues
    assert out.loc[0, "ingredient_id"] is None and out.loc[1, "ingredient_id"] == "ING1"
    assert out.loc[1, "price_pkr"] == 20.5


def test_json_errors_use_record_numbers_not_excel_rows(tmp_path):
    p = _write_json(tmp_path / "w.json", {"Categories": [
        {"Category_ID": "C1", "Category_Name": "x", "Sort_Order": "oops", "Status": "s", "Customer_Description": "d"}]})
    _out, issues = _read_table(JsonWorkbook(p), SPEC_BY_TABLE["categories"])
    assert any("record(s) [1]" in i.message for i in issues)


def test_json_dates_are_parsed(tmp_path):
    cols = SPEC_BY_TABLE["stock_movements"].columns
    row = {c: "x" for c in cols}
    row.update({"Movement_Date": "2026-10-31", "Qty_Signed": 12.0})
    out, issues = _read_table(JsonWorkbook(_write_json(tmp_path / "w.json", {"Stock_Movements": [row]})),
                              SPEC_BY_TABLE["stock_movements"])
    assert not issues and str(out.loc[0, "movement_date"]) == "2026-10-31"


def test_discover_recognises_json_workbooks_by_sheet_name(tmp_path):
    _write_json(tmp_path / "anything_v7.json", {"Menu_Items": [], "Categories": []})
    _write_json(tmp_path / "stock.json", {"Branch_Stock": []})
    _write_json(tmp_path / "other.json", {"Random": []})
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    found = discover(tmp_path)
    assert set(found) == {"menu", "inventory"}
    assert found["menu"].name == "anything_v7.json"


def test_json_with_wrong_shape_is_rejected(tmp_path):
    import pytest
    p = _write_json(tmp_path / "bad.json", {"Menu_Items": {"not": "a list"}})
    with pytest.raises(ValueError):
        JsonWorkbook(p)
