"""
EMBED step - create vectors for documents and store them in pgvector.

Incremental by design: every document gets a content_hash. On each run only documents whose hash
changed (or that are new) are sent to the embedding API; the rest are skipped. This keeps you
inside Gemini's free-tier limits and makes re-runs fast.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time

import numpy as np
from psycopg2.extras import execute_values

from . import config

log = logging.getLogger(__name__)


def _normalize(vec) -> list:
    arr = np.asarray(vec, dtype=np.float64)
    norm = np.linalg.norm(arr)
    return (arr / norm if norm else arr).tolist()


class GeminiEmbedder:
    """Gemini embeddings via the google-genai SDK (free tier available)."""

    def __init__(self):
        if not config.GEMINI_API_KEY or config.GEMINI_API_KEY.startswith("your-"):
            raise RuntimeError("GEMINI_API_KEY is not set. Put it in .env "
                               "(or set EMBEDDING_PROVIDER=fake to test offline).")
        from google import genai            # imported lazily so offline tests don't need it
        self._genai_types = __import__("google.genai.types", fromlist=["types"])
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.model = config.GEMINI_EMBED_MODEL
        self.dim = config.EMBEDDING_DIM
        self.name = f"gemini:{self.model}:{self.dim}"

    def _embed(self, texts: list, task_type: str) -> list:
        cfg = self._genai_types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim)
        for attempt in range(6):
            try:
                resp = self.client.models.embed_content(model=self.model, contents=texts, config=cfg)
                # Dimensions below the native size are not pre-normalised, so we normalise ourselves.
                return [_normalize(e.values) for e in resp.embeddings]
            except Exception as exc:                       # rate limit / transient network error
                wait = 2 ** attempt * 2
                log.warning("Embedding call failed (%s). Retry %d/6 in %ds", exc, attempt + 1, wait)
                time.sleep(wait)
        raise RuntimeError("Embedding API kept failing; progress so far is saved - re-run to continue.")

    def embed_documents(self, texts: list) -> list:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


class FakeEmbedder:
    """
    Offline bag-of-words hashing embedder. NOT semantically smart (no synonyms / Roman Urdu),
    but deterministic and free - perfect for testing the pipeline and CI without any API key.
    """

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


def get_embedder():
    if config.EMBEDDING_PROVIDER == "fake":
        return FakeEmbedder()
    return GeminiEmbedder()


def _vec_literal(vec) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def _doc_hash(doc, model_name: str) -> str:
    payload = f"{model_name}|{doc.content}|{json.dumps(doc.metadata, sort_keys=True, default=str)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sync_documents(conn, docs: list, embedder, force: bool = False) -> dict:
    """Embed only new/changed documents, upsert them, delete documents that no longer exist."""
    with conn.cursor() as cur:
        cur.execute("SELECT doc_id, content_hash FROM documents")
        existing = dict(cur.fetchall())

    hashes = {d.doc_id: _doc_hash(d, embedder.name) for d in docs}
    todo = [d for d in docs if force or existing.get(d.doc_id) != hashes[d.doc_id]]
    stats = {"documents": len(docs), "embedded": 0, "skipped_unchanged": len(docs) - len(todo), "deleted": 0}
    log.info("Documents: %d total, %d to embed, %d unchanged", len(docs), len(todo), stats["skipped_unchanged"])

    batch = config.EMBED_BATCH_SIZE
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        vectors = embedder.embed_documents([d.content for d in chunk])
        rows = [(d.doc_id, d.doc_type, d.source_table, d.source_id, d.content,
                 json.dumps(d.metadata, default=str), hashes[d.doc_id], _vec_literal(v), embedder.name)
                for d, v in zip(chunk, vectors)]
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO documents (doc_id, doc_type, source_table, source_id, content, metadata,
                                       content_hash, embedding, embedding_model)
                VALUES %s
                ON CONFLICT (doc_id) DO UPDATE SET
                    doc_type = EXCLUDED.doc_type, source_table = EXCLUDED.source_table,
                    source_id = EXCLUDED.source_id, content = EXCLUDED.content,
                    metadata = EXCLUDED.metadata, content_hash = EXCLUDED.content_hash,
                    embedding = EXCLUDED.embedding, embedding_model = EXCLUDED.embedding_model,
                    etl_updated_at = now()
            """, rows, template="(%s,%s,%s,%s,%s,%s::jsonb,%s,%s::vector,%s)")
        conn.commit()                                   # commit per batch -> safe to resume after a failure
        stats["embedded"] += len(chunk)
        log.info("  embedded %d/%d", stats["embedded"], len(todo))
        if i + batch < len(todo):
            time.sleep(config.EMBED_SLEEP_SECONDS)       # be polite to free-tier rate limits

    stale = [k for k in existing if k not in hashes]
    if stale:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE doc_id = ANY(%s)", (stale,))
        conn.commit()
        stats["deleted"] = len(stale)
    return stats


def search(conn, embedder, query: str, k: int = 5, doc_type: str | None = None) -> list:
    qv = _vec_literal(embedder.embed_query(query))
    where, params = "", [qv, qv, k]
    if doc_type:
        where, params = "WHERE doc_type = %s", [qv, doc_type, qv, k]
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT doc_id, doc_type, 1 - (embedding <=> %s::vector) AS score, content
            FROM documents {where}
            ORDER BY embedding <=> %s::vector LIMIT %s
        """, params)
        return cur.fetchall()
