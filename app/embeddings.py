"""
EMBED step - turn text into vectors, store them in the `documents` collection.

Incremental: each document has a content_hash. Only new/changed documents are sent to the embedding
API; the rest are skipped. That keeps you inside Gemini's free tier and makes re-runs instant.
"""
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
from . import config
from .documents import build_documents

log = logging.getLogger(__name__)


def _normalize(vec) -> list:
    arr = np.asarray(vec, dtype=np.float64)
    norm = np.linalg.norm(arr)
    return (arr / norm if norm else arr).tolist()


class GeminiEmbedder:
    """Gemini embeddings through the google-genai SDK (free tier available)."""

    def __init__(self):
        if not config.GEMINI_API_KEY or config.GEMINI_API_KEY.startswith("your-"):
            raise RuntimeError("GEMINI_API_KEY is not set. Put it in .env (or set EMBEDDING_PROVIDER=fake to test offline).")
        from google import genai                       # imported lazily so offline tests don't need it
        from google.genai import types
        self._types = types
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.model = config.GEMINI_EMBED_MODEL
        self.dim = config.EMBEDDING_DIM
        self.name = f"gemini:{self.model}:{self.dim}"

    def _embed(self, texts: list, task_type: str) -> list:
        cfg = self._types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim)
        for attempt in range(6):
            try:
                resp = self.client.models.embed_content(model=self.model, contents=texts, config=cfg)
                # Sizes below the native 3072 are not pre-normalised, so we normalise ourselves.
                return [_normalize(e.values) for e in resp.embeddings]
            except Exception as exc:                   # rate limit / transient network error
                wait = 2 ** attempt * 2
                log.warning("Embedding call failed (%s). Retry %d/6 in %ds", exc, attempt + 1, wait)
                time.sleep(wait)
        raise RuntimeError("Embedding API kept failing; progress is saved - run again to continue.")

    def embed_documents(self, texts: list) -> list:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


class FakeEmbedder:
    """Offline bag-of-words hashing embedder: deterministic and free, but not semantically smart."""

    def __init__(self):
        self.dim = config.EMBEDDING_DIM
        self.name = f"fake:hash:{self.dim}"

    def _vec(self, text: str) -> list:
        total = np.zeros(self.dim)
        for tok in re.findall(r"\w+", text.lower()):
            seed = int(hashlib.sha256(tok.encode()).hexdigest()[:16], 16)
            total += np.random.default_rng(seed).normal(size=self.dim)
        return _normalize(total)

    def embed_documents(self, texts: list) -> list:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list:
        return self._vec(text)


@lru_cache(maxsize=1)
def get_embedder():
    return FakeEmbedder() if config.EMBEDDING_PROVIDER == "fake" else GeminiEmbedder()


def _doc_hash(doc, model_name: str) -> str:
    payload = f"{model_name}|{doc.content}|{json.dumps(doc.metadata, sort_keys=True, default=str)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sync_documents(db, embedder, force: bool = False) -> dict:
    """Embed new/changed documents, upsert them, delete documents whose source no longer exists."""
    docs = build_documents(db)
    coll = db["documents"]
    existing = {d["_id"]: d.get("content_hash") for d in coll.find({}, {"content_hash": 1})}

    hashes = {d.doc_id: _doc_hash(d, embedder.name) for d in docs}
    todo = [d for d in docs if force or existing.get(d.doc_id) != hashes[d.doc_id]]
    stats = {"documents": len(docs), "embedded": 0, "unchanged": len(docs) - len(todo), "deleted": 0}
    log.info("Documents: %d total, %d to embed, %d unchanged", len(docs), len(todo), stats["unchanged"])

    batch = config.EMBED_BATCH_SIZE
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        vectors = embedder.embed_documents([d.content for d in chunk])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for d, v in zip(chunk, vectors):
            coll.replace_one({"_id": d.doc_id}, {
                "_id": d.doc_id, "doc_type": d.doc_type, "source_collection": d.source_collection,
                "source_id": d.source_id, "content": d.content, "metadata": d.metadata,
                "content_hash": hashes[d.doc_id], "embedding": [round(x, 7) for x in v],
                "embedding_model": embedder.name, "updated_at": now}, upsert=True)
        stats["embedded"] += len(chunk)                 # saved per batch -> safe to resume after a failure
        log.info("  embedded %d/%d", stats["embedded"], len(todo))
        if i + batch < len(todo):
            time.sleep(config.EMBED_SLEEP_SECONDS)       # be polite to free-tier rate limits

    stale = [k for k in existing if k not in hashes]
    if stale:
        coll.delete_many({"_id": {"$in": stale}})
        stats["deleted"] = len(stale)
    return stats


def reindex_in_background(db) -> None:
    """Used after API writes. Never raises: a failed embedding must not break the write that triggered it."""
    try:
        sync_documents(db, get_embedder())
    except Exception as exc:
        log.error("Background re-embedding failed (run `python -m app.ingest --embed-only` later): %s", exc)
