"""
Semantic search over the `documents` collection.

Engine 1 (Atlas):  $vectorSearch - approximate nearest neighbours over an HNSW index (scales to millions).
Engine 2 (Python): exact cosine similarity computed in memory. For ~200 documents this takes a few
                   milliseconds, and it works immediately (the Atlas index needs ~1 minute to build).
VECTOR_SEARCH_MODE=auto tries Atlas first and falls back to Python on error or empty results.
"""
import logging

import numpy as np

from . import config

log = logging.getLogger(__name__)


def _hit(doc: dict, score: float) -> dict:
    return {"doc_id": doc["_id"], "doc_type": doc["doc_type"], "score": round(float(score), 4),
            "content": doc["content"], "metadata": doc.get("metadata", {})}


def _atlas_search(db, qv: list, k: int, doc_type: str | None) -> list:
    stage = {"index": config.VECTOR_INDEX_NAME, "path": "embedding", "queryVector": qv,
             "numCandidates": max(100, k * 20), "limit": k}
    if doc_type:
        stage["filter"] = {"doc_type": doc_type}
    pipeline = [{"$vectorSearch": stage},
                {"$addFields": {"_score": {"$meta": "vectorSearchScore"}}},
                {"$project": {"embedding": 0}}]
    return [_hit(d, d["_score"]) for d in db["documents"].aggregate(pipeline)]


def _python_search(db, qv: list, k: int, doc_type: str | None) -> list:
    query = {"doc_type": doc_type} if doc_type else {}
    docs = list(db["documents"].find(query))
    if not docs:
        return []
    matrix = np.array([d["embedding"] for d in docs], dtype=np.float64)       # vectors are unit length,
    scores = matrix @ np.asarray(qv, dtype=np.float64)                         # so dot product == cosine
    best = np.argsort(-scores)[:k]
    return [_hit(docs[i], scores[i]) for i in best]


def search_documents(db, embedder, query: str, k: int = 5, doc_type: str | None = None):
    """Returns (hits, engine_used)."""
    qv = embedder.embed_query(query)
    mode = config.VECTOR_SEARCH_MODE
    if mode in ("auto", "atlas"):
        try:
            hits = _atlas_search(db, qv, k, doc_type)
            if hits or mode == "atlas":
                return hits, "atlas"
            log.info("Atlas returned no hits (index still building?) -> using Python search")
        except Exception as exc:
            if mode == "atlas":
                raise
            log.warning("Atlas $vectorSearch unavailable (%s) -> using Python search", str(exc)[:120])
    return _python_search(db, qv, k, doc_type), "python"
