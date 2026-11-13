"""
LOAD step - idempotent UPSERT into PostgreSQL.

Why upsert?  Running the pipeline twice with the same file must give the same result (idempotency).
  * new key        -> INSERT
  * existing key   -> UPDATE, but ONLY if some value really changed (IS DISTINCT FROM)
  * unchanged row  -> untouched (etl_updated_at stays the same, so you can see what truly changed)
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from psycopg2 import sql
from psycopg2.extras import execute_values

from .config import TableSpec

log = logging.getLogger(__name__)


def _py(v):
    """Convert numpy / pandas scalars into plain Python values psycopg2 understands."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, np.generic):
        return v.item()
    return v


def _rows(df: pd.DataFrame, cols: list) -> list:
    return [tuple(_py(v) for v in r) for r in df[cols].itertuples(index=False, name=None)]


def upsert(cur, spec: TableSpec, df: pd.DataFrame) -> dict:
    cols = spec.db_columns
    non_key = [c for c in cols if c not in spec.pk]
    ident = sql.Identifier
    tbl = ident(spec.table)

    col_list = sql.SQL(", ").join(map(ident, cols))
    pk_list = sql.SQL(", ").join(map(ident, spec.pk))
    sets = sql.SQL(", ").join(
        sql.SQL("{c} = EXCLUDED.{c}").format(c=ident(c)) for c in non_key
    )
    old = sql.SQL(", ").join(sql.SQL("{}.{}").format(tbl, ident(c)) for c in non_key)
    new = sql.SQL(", ").join(sql.SQL("EXCLUDED.{}").format(ident(c)) for c in non_key)

    query = sql.SQL(
        "INSERT INTO {t} ({cols}) VALUES %s "
        "ON CONFLICT ({pk}) DO UPDATE SET {sets}, etl_updated_at = now() "
        "WHERE ({old}) IS DISTINCT FROM ({new}) "
        "RETURNING (xmax = 0) AS inserted"
    ).format(t=tbl, cols=col_list, pk=pk_list, sets=sets, old=old, new=new)

    rows = _rows(df, cols)
    result = execute_values(cur, query.as_string(cur), rows, page_size=500, fetch=True)
    inserted = sum(1 for (flag,) in result if flag)
    updated = len(result) - inserted
    return {"rows": len(rows), "inserted": inserted, "updated": updated,
            "unchanged": len(rows) - len(result)}


def prune(cur, spec: TableSpec, df: pd.DataFrame) -> int:
    """Delete DB rows whose key no longer exists in the source file (only with --prune)."""
    cur.execute(sql.SQL("SELECT {} FROM {}").format(
        sql.SQL(", ").join(map(sql.Identifier, spec.pk)), sql.Identifier(spec.table)))
    in_db = {tuple(r) for r in cur.fetchall()}
    in_src = {tuple(r) for r in df[list(spec.pk)].itertuples(index=False, name=None)}
    stale = list(in_db - in_src)
    if not stale:
        return 0
    cond = sql.SQL(" AND ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in spec.pk)
    q = sql.SQL("DELETE FROM {} WHERE {}").format(sql.Identifier(spec.table), cond)
    for key in stale:
        cur.execute(q, key)
    return len(stale)
