"""
ORCHESTRATION - the one function that runs the whole pipeline in the right order:

   discover -> (skip unchanged) -> extract -> validate -> load (1 transaction) -> archive
            -> build documents -> embed (incremental) -> record run
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from psycopg2 import sql

from . import config
from .db import ensure_schema, get_conn
from .documents import build_documents
from .embeddings import get_embedder, sync_documents
from .extract import discover, read_workbook, sha256_file
from .load import prune, upsert
from .validate import validate_batch

log = logging.getLogger(__name__)


def _make_parent_lookup(conn):
    def lookup(table, cols):
        try:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT {} FROM {}").format(
                    sql.SQL(", ").join(map(sql.Identifier, cols)), sql.Identifier(table)))
                return {tuple(r) for r in cur.fetchall()}
        except Exception:
            conn.rollback()
            return None
    return lookup


def run_pipeline(incoming: Path = config.INCOMING_DIR, menu_file: Path | None = None,
                 inventory_file: Path | None = None, force: bool = False, prune_missing: bool = False,
                 dry_run: bool = False, skip_embeddings: bool = False, trigger: str = "manual") -> int:
    """Returns a process exit code: 0 = success, 1 = failure."""
    # ---------- 1. find source files ----------
    files = discover(Path(incoming))
    if menu_file:
        files["menu"] = Path(menu_file)
    if inventory_file:
        files["inventory"] = Path(inventory_file)
    if not files:
        log.error("No workbooks found in %s (expected sheets %s)", incoming, list(config.SIGNATURES.values()))
        return 1
    for kind, path in files.items():
        log.info("Source %-9s -> %s", kind, path.name)

    conn = get_conn() if not dry_run else _try_conn()
    run_id = None
    try:
        if conn is not None and not dry_run:
            ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("INSERT INTO etl_runs (trigger, files) VALUES (%s, %s) RETURNING run_id",
                            (trigger, json.dumps({k: v.name for k, v in files.items()})))
                run_id = cur.fetchone()[0]
            conn.commit()

        # ---------- 2. skip workbooks that have not changed ----------
        hashes = {k: sha256_file(p) for k, p in files.items()}
        to_load = dict(files)
        if conn is not None and not (force or dry_run):
            with conn.cursor() as cur:
                cur.execute("SELECT workbook, sha256 FROM source_files")
                known = dict(cur.fetchall())
            for kind in list(to_load):
                if known.get(kind) == hashes[kind]:
                    log.info("Workbook '%s' unchanged since last load -> skipping tables (use --force to reload)", kind)
                    del to_load[kind]

        stats: dict = {"tables": {}}

        # ---------- 3. extract + clean ----------
        dfs, issues = {}, []
        for kind, path in to_load.items():
            log.info("Reading %s", path.name)
            specs = [s for s in config.SPECS if s.workbook == kind]
            part, iss = read_workbook(path, specs)
            dfs.update(part)
            issues.extend(iss)

        # ---------- 4. validate ----------
        if dfs:
            issues.extend(validate_batch(dfs, _make_parent_lookup(conn) if conn is not None else None))
        for i in issues:
            (log.error if i.level == "ERROR" else log.warning)("%s", i)
        errors = [i for i in issues if i.level == "ERROR"]
        if errors:
            raise ValueError(f"{len(errors)} data-quality error(s); nothing was written. Fix the Excel file and re-run.")

        if dry_run:
            log.info("DRY RUN OK: %d table(s) validated, nothing written.", len(dfs))
            return 0

        # ---------- 5. load (single transaction: all-or-nothing) ----------
        if dfs:
            ordered = [s for s in config.SPECS if s.table in dfs]
            with conn:
                with conn.cursor() as cur:
                    for spec in ordered:
                        r = upsert(cur, spec, dfs[spec.table])
                        stats["tables"][spec.table] = r
                        log.info("  loaded %-16s rows=%-4d inserted=%-4d updated=%-4d unchanged=%d",
                                 spec.table, r["rows"], r["inserted"], r["updated"], r["unchanged"])
                    if prune_missing:
                        for spec in reversed(ordered):         # children first
                            n = prune(cur, spec, dfs[spec.table])
                            if n:
                                stats["tables"][spec.table]["pruned"] = n
                                log.info("  pruned %-16s %d row(s) no longer in source", spec.table, n)
                    for kind in to_load:
                        cur.execute("""INSERT INTO source_files (workbook, file_name, sha256) VALUES (%s,%s,%s)
                                       ON CONFLICT (workbook) DO UPDATE SET file_name = EXCLUDED.file_name,
                                       sha256 = EXCLUDED.sha256, loaded_at = now()""",
                                    (kind, files[kind].name, hashes[kind]))
            _archive(to_load, hashes)
        else:
            log.info("Nothing new to load.")

        # ---------- 6. documents + embeddings (always cheap: only changed docs are embedded) ----------
        if skip_embeddings:
            log.info("Embeddings skipped (--skip-embeddings).")
        else:
            docs = build_documents(conn)
            stats["embeddings"] = sync_documents(conn, docs, get_embedder(), force=force)

        _finish_run(conn, run_id, "success", stats)
        log.info("Pipeline finished successfully (run_id=%s).", run_id)
        return 0

    except Exception as exc:
        log.error("Pipeline FAILED: %s", exc)
        if conn is not None:
            conn.rollback()
            _finish_run(conn, run_id, "failed", None, str(exc))
        return 1
    finally:
        if conn is not None:
            conn.close()


def _try_conn():
    try:
        return get_conn()
    except Exception as exc:
        log.warning("No database connection (%s); dry run will skip foreign-key checks against the DB.", exc)
        return None


def _finish_run(conn, run_id, status, stats, error=None):
    if run_id is None:
        return
    with conn.cursor() as cur:
        cur.execute("UPDATE etl_runs SET finished_at = now(), status = %s, stats = %s, error = %s WHERE run_id = %s",
                    (status, json.dumps(stats, default=str) if stats else None, error, run_id))
    conn.commit()


def _archive(to_load: dict, hashes: dict) -> None:
    """Keep an immutable copy of every file we loaded (audit trail / re-load / debugging)."""
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for kind, path in to_load.items():
        dest = config.ARCHIVE_DIR / f"{stamp}_{hashes[kind][:8]}_{path.name}"
        shutil.copy2(path, dest)
        log.info("  archived %s", dest.name)
