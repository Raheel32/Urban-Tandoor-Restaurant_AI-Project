"""
Generic CRUD router. One function builds the same 6 endpoints for every collection:

  POST   /prefix            create            (409 if the id already exists)
  GET    /prefix            list              (?skip= &limit= &q= + collection-specific filters)
  GET    /prefix/{id}       read one          (404 if missing)
  PUT    /prefix/{id}       replace whole document
  PATCH  /prefix/{id}       change only the fields you send (null removes a field)
  DELETE /prefix/{id}       delete            (204)

NOTE: no `from __future__ import annotations` in this file on purpose - FastAPI needs real type objects
for the request/response models that are created inside the factory function.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from .. import config
from ..db import get_db
from ..embeddings import reindex_in_background
from ..schemas import COLLECTIONS, make_patch_model


@dataclass(frozen=True)
class CrudConfig:
    collection: str
    prefix: str
    tag: str
    filters: dict = field(default_factory=dict)      # query-parameter name -> field path in the document
    search_field: str | None = None                  # field searched by ?q= (case-insensitive "contains")
    reindex: bool = False                            # does a change here affect the embedded documents?
    used_by: tuple = ()                              # ((collection, field path), ...) that reference this id


CONFIGS = [
    CrudConfig("menu_items", "/menu-items", "Menu items",
               {"category_id": "category_id", "catalog_status": "catalog_status",
                "branch_id": "branches.branch_id", "dietary_hint": "dietary_hint"}, "item_name", True,
               used_by=(("deals", "items.menu_id"),)),
    CrudConfig("categories", "/categories", "Categories", {"status": "status"}, "category_name",
               used_by=(("menu_items", "category_id"),)),
    CrudConfig("deals", "/deals", "Deals", {"approval_status": "approval_status"}, "deal_name", True),
    CrudConfig("ingredients", "/ingredients", "Ingredients",
               {"category": "category", "supplier_id": "supplier_id", "status": "status"}, "ingredient_name", True,
               used_by=(("menu_items", "recipe.ingredient_id"), ("menu_items", "addons.ingredient_id"),
                        ("branch_stock", "ingredient_id"), ("stock_movements", "ingredient_id"))),
    CrudConfig("suppliers", "/suppliers", "Suppliers", {"supply_category": "supply_category"}, "supplier_name",
               used_by=(("ingredients", "supplier_id"),)),
    CrudConfig("branches", "/branches", "Branches", {"status": "status"}, "branch_name",
               used_by=(("menu_items", "branches.branch_id"), ("branch_stock", "branch_id"),
                        ("stock_movements", "branch_id"))),
    CrudConfig("branch_stock", "/branch-stock", "Branch stock",
               {"branch_id": "branch_id", "ingredient_id": "ingredient_id", "stock_status": "stock_status"}),
    CrudConfig("stock_movements", "/stock-movements", "Stock movements",
               {"branch_id": "branch_id", "ingredient_id": "ingredient_id", "movement_type": "movement_type",
                "movement_date": "movement_date"}),
    CrudConfig("allergen_guide", "/allergens", "Allergens", {}, "display_name", True),
    CrudConfig("service_rules", "/service-rules", "Service rules", {}, "rule_name", True),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validation_detail(exc: ValidationError):
    return json.loads(exc.json(include_url=False, include_context=False))


def build_crud_router(cfg: CrudConfig) -> APIRouter:
    coll_def = COLLECTIONS[cfg.collection]
    Model = coll_def.model
    Patch = make_patch_model(coll_def)
    name = cfg.collection
    router = APIRouter(prefix=cfg.prefix, tags=[cfg.tag])
    id_hint = ("Composite id: " + ":".join(coll_def.keys)) if len(coll_def.keys) > 1 else coll_def.keys[0]

    def _after_write(background: BackgroundTasks, db: Database):
        if cfg.reindex and config.AUTO_REINDEX:
            background.add_task(reindex_in_background, db)      # runs AFTER the response is sent

    filter_params = [{"name": q, "in": "query", "required": False, "schema": {"type": "string"},
                      "description": f"Exact match on `{path}`"} for q, path in cfg.filters.items()]

    # ------------------------------------------------------------------ CREATE
    @router.post("", response_model=Model, response_model_exclude_none=True, status_code=201,
                 name=f"create_{name}", summary=f"Create ({id_hint})")
    def create(item: Model, background: BackgroundTasks, db: Database = Depends(get_db)):
        doc = item.model_dump(exclude_none=True)
        doc["_id"] = coll_def.make_id(doc)
        doc["created_at"] = doc["updated_at"] = _now()
        try:
            db[name].insert_one(doc)
        except DuplicateKeyError:
            raise HTTPException(409, f"{name}: id '{doc['_id']}' already exists")
        _after_write(background, db)
        doc.pop("_id")
        return doc

    # ------------------------------------------------------------------ LIST
    @router.get("", response_model=list[Model], response_model_exclude_none=True, name=f"list_{name}",
                summary="List (paginated)", openapi_extra={"parameters": filter_params})
    def list_items(request: Request,
                   skip: int = Query(0, ge=0),
                   limit: int = Query(50, ge=1, le=500),
                   q: str | None = Query(None, description=f"Case-insensitive 'contains' search on `{cfg.search_field}`"
                                         if cfg.search_field else "Not available for this collection"),
                   db: Database = Depends(get_db)):
        query: dict = {path: request.query_params[param] for param, path in cfg.filters.items()
                       if param in request.query_params}
        if q and cfg.search_field:
            query[cfg.search_field] = {"$regex": re.escape(q), "$options": "i"}
        return list(db[name].find(query, {"_id": 0}).sort("_id", 1).skip(skip).limit(limit))

    # ------------------------------------------------------------------ READ ONE
    @router.get("/{item_id}", response_model=Model, response_model_exclude_none=True, name=f"get_{name}",
                summary=f"Get one ({id_hint})")
    def get_one(item_id: str, db: Database = Depends(get_db)):
        doc = db[name].find_one({"_id": item_id}, {"_id": 0})
        if doc is None:
            raise HTTPException(404, f"{name}: '{item_id}' not found")
        return doc

    # ------------------------------------------------------------------ REPLACE
    @router.put("/{item_id}", response_model=Model, response_model_exclude_none=True, name=f"replace_{name}",
                summary="Replace the whole document")
    def replace(item_id: str, item: Model, background: BackgroundTasks, db: Database = Depends(get_db)):
        doc = item.model_dump(exclude_none=True)
        if coll_def.make_id(doc) != item_id:
            raise HTTPException(400, f"The id in the body ({coll_def.make_id(doc)}) does not match the URL ({item_id})")
        existing = db[name].find_one({"_id": item_id})
        if existing is None:
            raise HTTPException(404, f"{name}: '{item_id}' not found (use POST to create)")
        doc["_id"] = item_id
        doc["created_at"] = existing.get("created_at") or _now()
        doc["updated_at"] = _now()
        db[name].replace_one({"_id": item_id}, doc)
        _after_write(background, db)
        doc.pop("_id")
        return doc

    # ------------------------------------------------------------------ PARTIAL UPDATE
    @router.patch("/{item_id}", response_model=Model, response_model_exclude_none=True, name=f"update_{name}",
                  summary="Change only the fields you send (null removes a field; lists are replaced whole)")
    def update(item_id: str, changes: Patch, background: BackgroundTasks, db: Database = Depends(get_db)):
        data = changes.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(400, "Send at least one field to change")
        existing = db[name].find_one({"_id": item_id}, {"_id": 0})
        if existing is None:
            raise HTTPException(404, f"{name}: '{item_id}' not found")

        to_set = {k: v for k, v in data.items() if v is not None}
        to_unset = [k for k, v in data.items() if v is None]
        merged = {**existing, **to_set}
        for k in to_unset:
            merged.pop(k, None)
        try:
            Model.model_validate(merged)            # the result must still be a valid document
        except ValidationError as exc:
            raise HTTPException(422, _validation_detail(exc))

        to_set["updated_at"] = _now()
        update_doc: dict = {"$set": to_set}
        if to_unset:
            update_doc["$unset"] = {k: "" for k in to_unset}
        db[name].update_one({"_id": item_id}, update_doc)
        _after_write(background, db)
        return db[name].find_one({"_id": item_id}, {"_id": 0})

    # ------------------------------------------------------------------ DELETE
    @router.delete("/{item_id}", status_code=204, name=f"delete_{name}", summary="Delete")
    def delete(item_id: str, background: BackgroundTasks, db: Database = Depends(get_db)):
        # MongoDB has no foreign keys, so we protect references ourselves.
        for other, path in cfg.used_by:
            if db[other].find_one({path: item_id}, {"_id": 1}):
                raise HTTPException(409, f"Cannot delete {name} '{item_id}': it is still referenced by {other}.{path}")
        if db[name].delete_one({"_id": item_id}).deleted_count == 0:
            raise HTTPException(404, f"{name}: '{item_id}' not found")
        _after_write(background, db)
        return Response(status_code=204)

    return router


def all_crud_routers() -> list:
    return [build_crud_router(c) for c in CONFIGS]
