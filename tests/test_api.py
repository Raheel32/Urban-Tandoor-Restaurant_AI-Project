import pytest

NEW_ITEM = {"menu_id": "MENU900", "item_name": "Test Seekh Wrap", "base_price_pkr": 450,
            "category": "Wraps", "variants": [{"variant_id": "VAR900", "variant_name": "Regular", "price_pkr": 450}]}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["menu_items"] == 118


def test_menu_item_full_crud_cycle(client, db):
    # create
    r = client.post("/menu-items", json=NEW_ITEM)
    assert r.status_code == 201 and r.json()["created_at"]
    assert "_id" not in r.json()
    # duplicate
    assert client.post("/menu-items", json=NEW_ITEM).status_code == 409
    # read
    r = client.get("/menu-items/MENU900")
    assert r.status_code == 200 and r.json()["variants"][0]["variant_id"] == "VAR900"
    # patch: only the sent field changes
    r = client.patch("/menu-items/MENU900", json={"base_price_pkr": 500})
    assert r.status_code == 200 and r.json()["base_price_pkr"] == 500 and r.json()["item_name"] == "Test Seekh Wrap"
    # patch null removes a field
    r = client.patch("/menu-items/MENU900", json={"category": None})
    assert "category" not in r.json()
    # put replaces everything
    r = client.put("/menu-items/MENU900", json={**NEW_ITEM, "item_name": "Seekh Wrap Deluxe"})
    assert r.status_code == 200 and r.json()["item_name"] == "Seekh Wrap Deluxe" and r.json()["created_at"]
    # delete
    assert client.delete("/menu-items/MENU900").status_code == 204
    assert client.get("/menu-items/MENU900").status_code == 404
    assert client.delete("/menu-items/MENU900").status_code == 404


def test_validation_errors(client):
    assert client.post("/menu-items", json={"menu_id": "X1"}).status_code == 422                       # name missing
    assert client.post("/menu-items", json={**NEW_ITEM, "base_price_pkr": -1}).status_code == 422     # negative
    assert client.post("/menu-items", json={**NEW_ITEM, "typo_field": 1}).status_code == 422           # unknown field
    assert client.patch("/menu-items/MENU001", json={"menu_id": "OTHER"}).status_code == 422           # id is immutable
    assert client.patch("/menu-items/MENU001", json={}).status_code == 400
    assert client.patch("/menu-items/MENU001", json={"item_name": None}).status_code == 422            # required field
    assert client.put("/menu-items/MENU002", json={**NEW_ITEM}).status_code == 400                     # id mismatch
    assert client.patch("/menu-items/NOPE", json={"base_price_pkr": 1}).status_code == 404


def test_list_pagination_filters_and_search(client):
    r = client.get("/menu-items?limit=5")
    assert r.status_code == 200 and len(r.json()) == 5
    page2 = client.get("/menu-items?limit=5&skip=5").json()
    assert {m["menu_id"] for m in page2}.isdisjoint({m["menu_id"] for m in r.json()})
    by_branch = client.get("/menu-items?branch_id=UTG-KHI-001&limit=500").json()
    assert 0 < len(by_branch) <= 118 and all(any(b["branch_id"] == "UTG-KHI-001" for b in m["branches"]) for m in by_branch)
    found = client.get("/menu-items?q=biryani&limit=500").json()
    assert found and all("biryani" in m["item_name"].lower() for m in found)
    assert client.get("/menu-items?limit=0").status_code == 422


@pytest.mark.parametrize("prefix, count", [
    ("/menu-items", 118), ("/categories", 8), ("/deals", 6), ("/ingredients", 60), ("/suppliers", 7),
    ("/branches", 2), ("/branch-stock", 120), ("/stock-movements", 8), ("/allergens", 8), ("/service-rules", 7)])
def test_every_collection_lists_all_documents(client, prefix, count):
    r = client.get(f"{prefix}?limit=500")
    assert r.status_code == 200 and len(r.json()) == count


def test_branch_stock_composite_id(client):
    r = client.get("/branch-stock/UTG-KHI-001:ING001")
    assert r.status_code == 200 and r.json()["ingredient_id"] == "ING001"
    r = client.patch("/branch-stock/UTG-KHI-001:ING001", json={"available": 12.5, "stock_status": "REORDER"})
    assert r.json()["available"] == 12.5
    low = client.get("/branch-stock?stock_status=REORDER&branch_id=UTG-KHI-001").json()
    assert any(s["ingredient_id"] == "ING001" for s in low)
    # create a new stock row
    body = {"branch_id": "UTG-KHI-001", "ingredient_id": "ING002", "available": 1}
    assert client.post("/branch-stock", json=body).status_code == 409        # already exists
    assert client.put("/branch-stock/UTG-KHI-001:ING001", json={"branch_id": "UTG-KHI-001", "ingredient_id": "ING001",
                                                                "available": 5}).status_code == 200


def test_cannot_delete_something_that_is_still_referenced(client):
    for url in ("/branches/UTG-KHI-001", "/categories/CAT-01", "/suppliers/SUP001", "/ingredients/ING001"):
        r = client.get(url)
        if r.status_code == 200:
            assert client.delete(url).status_code == 409, url
    # an unreferenced record can be deleted
    client.post("/suppliers", json={"supplier_id": "SUP999", "supplier_name": "Temp"})
    assert client.delete("/suppliers/SUP999").status_code == 204


def test_live_availability_matches_the_excel_snapshot(client):
    for branch in ("UTG-KHI-001", "UTG-KHI-002"):
        rows = client.get(f"/availability/{branch}").json()
        computed = [r for r in rows if r.get("live_max_portions") is not None and r.get("snapshot_max_portions") is not None]
        assert computed, "expected recipe-linked items"
        mismatches = [r for r in computed if r["live_max_portions"] != r["snapshot_max_portions"]]
        assert not mismatches, mismatches[:3]
    assert client.get("/availability/NOPE").status_code == 404


def test_availability_reacts_to_stock_changes(client):
    row = next(r for r in client.get("/availability/UTG-KHI-001").json() if r.get("live_max_portions"))
    item = client.get(f"/menu-items/{row['menu_id']}").json()
    ing = item["recipe"][0]["ingredient_id"]
    client.patch(f"/branch-stock/UTG-KHI-001:{ing}", json={"available": 0})
    after = client.get(f"/availability/UTG-KHI-001?menu_id={row['menu_id']}").json()[0]
    assert after["live_max_portions"] == 0 and after["availability"] in ("OUT_OF_STOCK", "DISABLED")


def test_semantic_search_finds_relevant_documents(client):
    r = client.get("/search?q=chicken biryani&k=3&doc_type=menu_item")
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "python" and len(body["results"]) == 3
    assert "biryani" in body["results"][0]["content"].lower()
    assert all(h["doc_type"] == "menu_item" for h in body["results"])
    assert client.get("/search?q=x").status_code == 422


def test_api_write_triggers_background_reembedding(client, db):
    before = db.documents.find_one({"_id": "menu_item:MENU001"})["content_hash"]
    client.patch("/menu-items/MENU001", json={"description": "Brand new description with saffron"})
    doc = db.documents.find_one({"_id": "menu_item:MENU001"})
    assert doc["content_hash"] != before and "saffron" in doc["content"]
    # new and deleted items are reflected too
    client.post("/menu-items", json=NEW_ITEM)
    assert db.documents.find_one({"_id": "menu_item:MENU900"})
    client.delete("/menu-items/MENU900")
    assert db.documents.find_one({"_id": "menu_item:MENU900"}) is None


def test_admin_reindex(client):
    assert client.post("/admin/reindex").json()["embedded"] == 0
    assert client.post("/admin/reindex?force=true").json()["embedded"] == 199
