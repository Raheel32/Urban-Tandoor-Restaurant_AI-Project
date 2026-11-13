"""Command line interface:  python -m pipeline <command> [options]"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config
from .db import ensure_schema, get_conn
from .embeddings import get_embedder, search, sync_documents
from .documents import build_documents
from .runner import run_pipeline


def _cmd_init_db(_a) -> int:
    conn = get_conn()
    ensure_schema(conn)
    conn.close()
    return 0


def _cmd_run(a) -> int:
    return run_pipeline(Path(a.incoming), a.menu_file, a.inventory_file, force=a.force,
                        prune_missing=a.prune, dry_run=a.dry_run, skip_embeddings=a.skip_embeddings,
                        trigger="cli")


def _cmd_embed(a) -> int:
    conn = get_conn()
    stats = sync_documents(conn, build_documents(conn), get_embedder(), force=a.force)
    conn.close()
    print(stats)
    return 0


def _cmd_search(a) -> int:
    conn = get_conn()
    for doc_id, doc_type, score, content in search(conn, get_embedder(), a.query, a.k, a.type):
        print(f"\n[{score:.3f}] {doc_id}\n{content[:300]}{'...' if len(content) > 300 else ''}")
    conn.close()
    return 0


def _cmd_status(_a) -> int:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT run_id, started_at::timestamp(0), status, trigger, left(coalesce(error,''), 60) "
                    "FROM etl_runs ORDER BY run_id DESC LIMIT 5")
        print("Last runs:")
        for r in cur.fetchall():
            print("  ", r)
        print("\nRow counts:")
        for spec in config.SPECS:
            cur.execute(f"SELECT count(*) FROM {spec.table}")
            print(f"  {spec.table:<16} {cur.fetchone()[0]}")
        cur.execute("SELECT doc_type, count(*) FROM documents GROUP BY 1 ORDER BY 1")
        print("\nDocuments (embedded):")
        for r in cur.fetchall():
            print(f"  {r[0]:<16} {r[1]}")
    conn.close()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pipeline", description="Urban Tandoor data pipeline")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="create/upgrade tables (idempotent)").set_defaults(fn=_cmd_init_db)

    r = sub.add_parser("run", help="full pipeline: extract -> validate -> load -> embed")
    r.add_argument("--incoming", default=str(config.INCOMING_DIR), help="folder with the .xlsx files")
    r.add_argument("--menu-file"), r.add_argument("--inventory-file")
    r.add_argument("--force", action="store_true", help="reload even if the file is unchanged; re-embed everything")
    r.add_argument("--prune", action="store_true", help="delete DB rows that disappeared from the Excel file")
    r.add_argument("--dry-run", action="store_true", help="extract + validate only, write nothing")
    r.add_argument("--skip-embeddings", action="store_true")
    r.set_defaults(fn=_cmd_run)

    e = sub.add_parser("embed", help="(re)build document embeddings only")
    e.add_argument("--force", action="store_true")
    e.set_defaults(fn=_cmd_embed)

    s = sub.add_parser("search", help="semantic search test")
    s.add_argument("query"), s.add_argument("-k", type=int, default=5), s.add_argument("--type")
    s.set_defaults(fn=_cmd_search)

    sub.add_parser("status", help="recent runs and row counts").set_defaults(fn=_cmd_status)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
