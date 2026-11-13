"""Database helpers."""
from __future__ import annotations

import logging

import psycopg2

from .config import DATABASE_URL, SCHEMA_FILE

log = logging.getLogger(__name__)


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def ensure_schema(conn) -> None:
    """Apply sql/schema.sql. Idempotent, so it is safe to call before every run."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.commit()
    log.info("Schema is up to date.")
