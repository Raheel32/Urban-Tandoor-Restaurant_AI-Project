"""Non-CRUD endpoints: semantic search, live availability, reindex, health."""
import math
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from pymongo.database import Database

from ..db import get_db
from ..embeddings import get_embedder, sync_documents
from ..search import search_documents

router = APIRouter()


class SearchHit(BaseModel):
    doc_id: str
    doc_type: str
    score: float
    content: str
    metadata: dict = {}


class SearchResponse(BaseModel):
    query: str
    engine: str
    results: list[SearchHit]


class AvailabilityItem(BaseModel):
    menu_id: str
    item_name: str
    price_pkr: float | None = None
    enabled_for_order: str | None = None
    snapshot_max_portions: float | None = None
    live_max_portions: int | None = None
    availability: str


@router.get("/search", response_model=SearchResponse, tags=["Search"], summary="Semantic (meaning-based) search")
def semantic_search(q: str = Query(..., min_length=2, description="e.g. 'spicy chicken with rice'"),
                    k: int = Query(5, ge=1, le=20),
                    doc_type: str | None = Query(None, description="menu_item | deal | service_rule | allergen | ingredient"),
                    db: Database = Depends(get_db)):
    hits, engine = search_documents(db, get_embedder(), q, k, doc_type)
    return {"query": q, "engine": engine, "results": hits}


def _max_portions(recipe: list, available: dict) -> int | None:
    """How many portions can be made from current stock? (min over ingredients; exact decimal maths)"""
    if not recipe:
        return None
    best = None
    for line in recipe:
        qty = Decimal(str(line["qty_per_portion"]))
        have = Decimal(str(available.get(line["ingredient_id"]) or 0))
        portions = 0 if qty == 0 else int(have // qty)
        best = portions if best is None else min(best, portions)
    return best


@router.get("/availability/{branch_id}", response_model=list[AvailabilityItem], response_model_exclude_none=True,
            tags=["Search"], summary="Live portions per menu item for one branch (computed from current stock)")
def availability(branch_id: str, menu_id: str | None = None, db: Database = Depends(get_db)):
    if db["branches"].find_one({"_id": branch_id}, {"_id": 1}) is None:
        raise HTTPException(404, f"branch '{branch_id}' not found")
    available = {s["ingredient_id"]: s.get("available") for s in db["branch_stock"].find({"branch_id": branch_id})}
    query = {"branches.branch_id": branch_id}
    if menu_id:
        query["_id"] = menu_id
    out = []
    for item in db["menu_items"].find(query).sort("_id"):
        offer = next(b for b in item["branches"] if b["branch_id"] == branch_id)
        live = _max_portions(item.get("recipe", []), available)
        if offer.get("enabled_for_order") != "Yes":
            status = "DISABLED"
        elif live is None:
            status = "NO_RECIPE"
        elif live <= 0:
            status = "OUT_OF_STOCK"
        elif live < 10:
            status = "LOW"
        else:
            status = "AVAILABLE"
        out.append({"menu_id": item["menu_id"], "item_name": item["item_name"], "price_pkr": offer.get("price_pkr"),
                    "enabled_for_order": offer.get("enabled_for_order"),
                    "snapshot_max_portions": offer.get("snapshot_max_portions"),
                    "live_max_portions": live, "availability": status})
    return out


@router.post("/admin/reindex", tags=["Admin"], summary="Re-embed changed documents now (force=true re-embeds all)")
def reindex(force: bool = False, db: Database = Depends(get_db)):
    return sync_documents(db, get_embedder(), force=force)


@router.get("/health", tags=["Admin"], summary="Is the API up and the database reachable?")
def health(db: Database = Depends(get_db)):
    db.command("ping")
    return {"status": "ok", "database": db.name,
            "documents": db["documents"].count_documents({}), "menu_items": db["menu_items"].count_documents({})}
