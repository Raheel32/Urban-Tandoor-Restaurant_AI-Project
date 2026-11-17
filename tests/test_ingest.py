import copy

import pytest

from app.ingest import ingest, upsert_collection
from app.transform import build_collections


def _contains_null_or_nan(obj) -> bool:
    if obj is None:
        return True
    if isinstance(obj, str):
        return obj.strip().lower() == "nan"
    if isinstance(obj, float):
        return obj != obj
    if isinstance(obj, dict):
        return any(_contains_null_or_nan(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_null_or_nan(v) for v in obj)
    return False


def test_counts_and_embedded_shape(db):
    assert db.menu_items.count_documents({}) == 118
    assert db.ingredients.count_documents({}) == 60
    assert db.branch_stock.count_documents({}) == 120
    assert db.deals.count_documents({}) == 6
    assert db.documents.count_documents({}) == 199
    m = db.menu_items.find_one({"_id": "MENU001"})
    assert m["item_name"] == "Chicken Biryani"
    assert len(m["variants"]) >= 1 and len(m["aliases"]) >= 1 and len(m["recipe"]) >= 1 and len(m["branches"]) == 2
    assert all("ingredient_name" in r for r in m["recipe"])
    assert not _contains_null_or_nan(m)    # empty cells are absent, never stored as null / 'nan'


def test_second_ingest_changes_nothing(db, workbooks):
    stats = ingest(db, *workbooks)
    for name, r in stats["collections"].items():
        assert r["inserted"] == 0 and r["updated"] == 0, name
    assert stats["embeddings"]["embedded"] == 0 and stats["embeddings"]["unchanged"] == 199


def test_changed_price_updates_one_document_and_one_embedding(db, workbooks):
    menu, inv = copy.deepcopy(workbooks[0]), workbooks[1]
    menu["Menu_Items"][0]["Base_Price_PKR"] = 999
    stats = ingest(db, menu, inv)
    assert stats["collections"]["menu_items"]["updated"] == 1
    assert stats["embeddings"]["embedded"] == 1
    assert db.menu_items.find_one({"_id": menu["Menu_Items"][0]["Menu_ID"]})["base_price_pkr"] == 999


def test_prune_removes_documents_missing_from_the_file(db, workbooks):
    menu, inv = copy.deepcopy(workbooks[0]), workbooks[1]
    removed = menu["Menu_Items"].pop()
    for sheet in ("Variants", "Item_Addons", "Item_Aliases", "Branch_Menu", "Inventory_Links"):
        menu[sheet] = [r for r in menu[sheet] if r["Menu_ID"] != removed["Menu_ID"]]
    inv = copy.deepcopy(inv)
    inv["Recipes"] = [r for r in inv["Recipes"] if r["Menu_ID"] != removed["Menu_ID"]]
    menu["Deal_Items"] = [r for r in menu["Deal_Items"] if r["Menu_ID"] != removed["Menu_ID"]]
    stats = ingest(db, menu, inv, prune=True)
    assert stats["collections"]["menu_items"]["pruned"] == 1
    assert db.menu_items.count_documents({}) == 117
    assert db.documents.find_one({"_id": f"menu_item:{removed['Menu_ID']}"}) is None


@pytest.mark.parametrize("mutate, expected", [
    (lambda m, i: m["Variants"][0].__setitem__("Menu_ID", "MENU_NOPE"), "Variants"),
    (lambda m, i: m["Menu_Items"][0].__setitem__("Base_Price_PKR", "abc"), "base_price_pkr"),
    (lambda m, i: m["Menu_Items"][0].__setitem__("Base_Price_PKR", -5), "base_price_pkr"),
    (lambda m, i: m["Menu_Items"][1].__setitem__("Menu_ID", m["Menu_Items"][0]["Menu_ID"]), "duplicate id"),
    (lambda m, i: m["Menu_Items"][0].__setitem__("Category_ID", "CAT-NOPE"), "category_id -> categories"),
    (lambda m, i: i["Recipes"][0].__setitem__("Ingredient_ID", "ING_NOPE"), "unknown ingredient"),
    (lambda m, i: i["Stock_Movements"][0].__setitem__("Movement_Date", "31/10/2026"), "movement_date"),
    (lambda m, i: m["Menu_Items"][0].__setitem__("Surprise_Column", "x"), "surprise_column"),
])
def test_bad_data_is_rejected_and_nothing_is_written(workbooks, mutate, expected):
    menu, inv = copy.deepcopy(workbooks[0]), copy.deepcopy(workbooks[1])
    mutate(menu, inv)
    _docs, errors = build_collections(menu, inv)
    assert any(expected in e for e in errors), errors


def test_ingest_raises_before_writing_anything(workbooks):
    import mongomock
    database = mongomock.MongoClient().x
    menu = copy.deepcopy(workbooks[0])
    menu["Menu_Items"][0]["Base_Price_PKR"] = "abc"
    with pytest.raises(ValueError):
        ingest(database, menu, workbooks[1])
    assert database.list_collection_names() == []
