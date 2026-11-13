"""
VALIDATE step - data-quality gates that run BEFORE anything touches the database.
If any ERROR is found the pipeline stops and nothing is written ("fail fast").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd

from .config import SPEC_BY_TABLE, TableSpec


@dataclass
class Issue:
    level: str      # ERROR | WARNING
    table: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.table}: {self.message}"


ParentLookup = Callable[[str, tuple], Optional[set]]


def _keys(df: pd.DataFrame, cols: tuple) -> list:
    return list(df[list(cols)].itertuples(index=False, name=None))


def validate_batch(dfs: dict, parent_lookup: Optional[ParentLookup] = None) -> list:
    """
    dfs: {table_name: DataFrame (lowercase columns)} for everything being loaded in this run.
    parent_lookup(table, cols) -> set of existing keys in the DB for tables NOT in this batch
    (used when e.g. only the inventory workbook changed). May return None if unavailable.
    """
    issues: list = []

    for table, df in dfs.items():
        spec: TableSpec = SPEC_BY_TABLE[table]

        if df.empty:
            issues.append(Issue("WARNING", table, "sheet has no data rows"))
            continue

        # 1. primary key present
        null_pk = df[list(spec.pk)].isna().any(axis=1)
        if null_pk.any():
            issues.append(Issue("ERROR", table, f"{int(null_pk.sum())} row(s) have an empty primary key {spec.pk}"
                                                f" (source rows {[int(i) + df.attrs.get('row_base', 2) for i in df.index[null_pk][:5]]})"))

        # 2. primary key unique
        keys = df.loc[~null_pk, list(spec.pk)]
        dup = keys.duplicated(keep=False)
        if dup.any():
            sample = _keys(keys[dup].drop_duplicates(), spec.pk)[:5]
            issues.append(Issue("ERROR", table, f"duplicate primary keys {spec.pk}: {sample}"))

        # 3. value ranges
        for col in spec.non_negative:
            vals = pd.to_numeric(df[col], errors="coerce")
            if (vals < 0).any():
                issues.append(Issue("ERROR", table, f"negative values in '{col}'"))

    # 4. foreign keys (referential integrity)
    for table, df in dfs.items():
        spec = SPEC_BY_TABLE[table]
        for child_cols, parent_table, parent_cols in spec.fks:
            if parent_table in dfs:
                parent_keys = set(_keys(dfs[parent_table], parent_cols))
            else:
                parent_keys = parent_lookup(parent_table, parent_cols) if parent_lookup else None
                if parent_keys is None:
                    issues.append(Issue("WARNING", table,
                                        f"cannot verify {child_cols} -> {parent_table}: parent table unavailable"))
                    continue
            child = df[list(child_cols)].dropna()
            orphans = [k for k in child.itertuples(index=False, name=None) if k not in parent_keys]
            if orphans:
                uniq = sorted(set(orphans))[:5]
                issues.append(Issue("ERROR", table,
                                    f"{len(orphans)} row(s) reference missing {parent_table}{parent_cols}: {uniq}"))
    return issues
