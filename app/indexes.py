"""Index management. Indexes make queries fast; all functions are idempotent (safe to re-run)."""
import logging

from pymongo import ASCENDING

from . import config

log = logging.getLogger(__name__)

# (collection, keys) - chosen for the filters the API exposes
REGULAR_INDEXES = [
    ("menu_items", [("category_id", ASCENDING)]),
    ("menu_items", [("catalog_status", ASCENDING)]),
    ("menu_items", [("branches.branch_id", ASCENDING)]),
    ("menu_items", [("aliases.customer_phrase", ASCENDING)]),
    ("ingredients", [("supplier_id", ASCENDING)]),
    ("branch_stock", [("branch_id", ASCENDING), ("stock_status", ASCENDING)]),
    ("branch_stock", [("ingredient_id", ASCENDING)]),
    ("stock_movements", [("branch_id", ASCENDING), ("movement_date", ASCENDING)]),
    ("documents", [("doc_type", ASCENDING)]),
]


def ensure_indexes(db) -> None:
    for coll, keys in REGULAR_INDEXES:
        db[coll].create_index(keys)


def ensure_vector_index(db) -> str:
    """Create the Atlas Vector Search index on documents.embedding. Returns a short status message."""
    from pymongo.operations import SearchIndexModel
    coll = db["documents"]
    try:
        if any(i["name"] == config.VECTOR_INDEX_NAME for i in coll.list_search_indexes()):
            return "exists"
        coll.create_search_index(SearchIndexModel(
            name=config.VECTOR_INDEX_NAME, type="vectorSearch",
            definition={"fields": [
                {"type": "vector", "path": "embedding", "numDimensions": config.EMBEDDING_DIM, "similarity": "cosine"},
                {"type": "filter", "path": "doc_type"}]}))
        return "created (Atlas needs about a minute to build it)"
    except Exception as exc:
        return f"could not create automatically ({str(exc)[:100]}). Create it in the Atlas UI - see README"
