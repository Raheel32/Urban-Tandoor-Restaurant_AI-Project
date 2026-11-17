"""
INGEST command:  python -m app.ingest [options]

  JSON files -> transform to documents -> validate -> upsert into MongoDB -> indexes -> embeddings

Idempotent: running it twice changes nothing the second time (documents are compared before writing).
The JSON files are the source of truth for ingestion: documents edited through the API are overwritten
by the file's version if they differ (use --embed-only to skip loading).
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .db import get_db
from .embeddings import get_embedder, sync_documents
from .indexes import ensure_indexes, ensure_vector_index
from .transform import build_collections

log = logging.getLogger("ingest")
SIGNATURES = {"menu": "Menu_Items", "inventory": "Branch_Stock"}


def load_workbooks(folder: Path):
    """Find the menu + inventory JSON files by their sheet names (file names may change)."""
    found: dict = {}
    for path in sorted(Path(folder).glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Skipping %s (not valid JSON: %s)", path.name, exc)
            continue
        if not isinstance(data, dict):
            continue
        for kind, sheet in SIGNATURES.items():
            if sheet in data:
                found[kind] = (path, data)
    missing = [k for k in SIGNATURES if k not in found]
    if missing:
        raise FileNotFoundError(f"No JSON workbook for {missing} in {folder} (looking for sheets {list(SIGNATURES.values())})")
    for kind, (path, _) in found.items():
        log.info("Source %-9s -> %s", kind, path.name)
    return found["menu"][1], found["inventory"][1]


def upsert_collection(coll, docs: list, prune: bool) -> dict:
    """Insert new documents, replace changed ones, leave identical ones alone."""
    ids = [d["_id"] for d in docs]
    existing = {d["_id"]: d for d in coll.find({"_id": {"$in": ids}})}
    new_docs, changed_docs = [], []
    for d in docs:
        old = existing.get(d["_id"])
        if old is None:
            new_docs.append(d)
        elif old != d:
            changed_docs.append(d)
    if new_docs:
        coll.insert_many(new_docs, ordered=False)          # one round trip for all new documents
    for d in changed_docs:                                  # changed documents are few; replace one by one
        coll.replace_one({"_id": d["_id"]}, d)
    inserted, updated = len(new_docs), len(changed_docs)
    pruned = coll.delete_many({"_id": {"$nin": ids}}).deleted_count if prune else 0
    return {"docs": len(docs), "inserted": inserted, "updated": updated,
            "unchanged": len(docs) - inserted - updated, "pruned": pruned}


def ingest(db, menu: dict, inventory: dict, *, prune=False, dry_run=False, skip_embeddings=False,
           force=False, vector_index=True) -> dict:
    collections, errors = build_collections(menu, inventory)
    if errors:
        for e in errors[:25]:
            log.error("%s", e)
        raise ValueError(f"{len(errors)} data problem(s) found; nothing was written. Fix the JSON and run again.")
    log.info("Validated %d collections, %d documents.", len(collections), sum(len(v) for v in collections.values()))
    if dry_run:
        return {"dry_run": True, "collections": {k: len(v) for k, v in collections.items()}}

    run = db["ingest_runs"].insert_one({"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                        "status": "running"})
    stats: dict = {"collections": {}}
    try:
        for name, docs in collections.items():
            r = upsert_collection(db[name], docs, prune)
            stats["collections"][name] = r
            log.info("  %-16s docs=%-4d inserted=%-4d updated=%-4d unchanged=%-4d%s", name, r["docs"], r["inserted"],
                     r["updated"], r["unchanged"], f" pruned={r['pruned']}" if prune else "")
        ensure_indexes(db)
        if not skip_embeddings:
            stats["embeddings"] = sync_documents(db, get_embedder(), force=force)
            if vector_index and config.VECTOR_SEARCH_MODE != "python":
                stats["vector_index"] = ensure_vector_index(db)
                log.info("Atlas vector index: %s", stats["vector_index"])
        status, error = "success", None
    except Exception as exc:
        status, error = "failed", str(exc)
        raise
    finally:
        db["ingest_runs"].update_one({"_id": run.inserted_id}, {"$set": {
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": status, "stats": stats, "error": error}})
    return stats


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.ingest", description="Load the JSON files into MongoDB")
    p.add_argument("--json-dir", default=str(config.JSON_DIR), help="folder containing the two JSON files")
    p.add_argument("--dry-run", action="store_true", help="validate only, write nothing")
    p.add_argument("--prune", action="store_true", help="delete documents that are no longer in the JSON files")
    p.add_argument("--skip-embeddings", action="store_true")
    p.add_argument("--force", action="store_true", help="re-embed every document")
    p.add_argument("--embed-only", action="store_true", help="skip loading; only (re)build embeddings")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    try:
        db = get_db()
        if a.embed_only:
            print(sync_documents(db, get_embedder(), force=a.force))
            if config.VECTOR_SEARCH_MODE != "python":
                print("Atlas vector index:", ensure_vector_index(db))
            return 0
        menu, inventory = load_workbooks(Path(a.json_dir))
        ingest(db, menu, inventory, prune=a.prune, dry_run=a.dry_run, skip_embeddings=a.skip_embeddings, force=a.force)
        log.info("Done.")
        return 0
    except Exception as exc:
        log.error("FAILED: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
