"""
EXTRACT + TRANSFORM step - read Excel (.xlsx) OR JSON workbooks and turn every sheet into a clean,
typed DataFrame.  Both formats are wrapped behind the same tiny interface (sheet_names + parse), so
everything after this file (validate, load, documents, embeddings) is format-independent.

JSON layout expected:  {"Sheet_Name": [ {"Column": value, ...}, ... ], ...}

Cleaning rules (applied to every cell):
  * strip whitespace, blank string -> NULL
  * numeric / date columns are coerced; values that cannot be coerced become ERRORS (never silent NULLs)
  * column headers are matched by their Excel name and renamed to lowercase DB names
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pandas as pd

from .config import SIGNATURES, TableSpec
from .validate import Issue

log = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class JsonWorkbook:
    """Makes a JSON file look like pd.ExcelFile: .sheet_names and .parse(sheet)."""

    def __init__(self, path: Path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not all(isinstance(v, list) for v in data.values()):
            raise ValueError(f"{Path(path).name}: expected an object of sheets, each a list of row objects")
        self._data = data
        self.sheet_names = list(data.keys())

    def parse(self, sheet: str, dtype=None) -> pd.DataFrame:
        rows = self._data[sheet]
        if not all(isinstance(r, dict) for r in rows):
            raise ValueError(f"sheet '{sheet}': every row must be a JSON object")
        # columns = union of keys, in first-seen order (robust if some rows omit a field)
        cols: list = []
        for r in rows:
            cols.extend(k for k in r if k not in cols)
        return pd.DataFrame(rows, columns=cols, dtype=object)


SOURCE_PATTERNS = ("*.xlsx", "*.json")


def open_workbook(path: Path):
    """Open an .xlsx or .json workbook behind one common interface."""
    return JsonWorkbook(path) if Path(path).suffix.lower() == ".json" else pd.ExcelFile(path)


def discover(folder: Path) -> dict:
    """Find the newest workbook of each kind in `folder`, identified by its sheet names."""
    found: dict = {}
    files = [p for pat in SOURCE_PATTERNS for p in folder.glob(pat)]
    for path in sorted(files, key=lambda p: p.stat().st_mtime):
        if path.name.startswith("~$"):          # Excel temp/lock files
            continue
        try:
            sheets = open_workbook(path).sheet_names
        except Exception as exc:                 # corrupt / not really an xlsx or json workbook
            log.warning("Skipping %s (cannot open: %s)", path.name, exc)
            continue
        for kind, signature in SIGNATURES.items():
            if signature in sheets:
                found[kind] = path               # newer file overrides older one
    return found


def _clean(v):
    if isinstance(v, str):
        v = v.strip()
        return None if v == "" else v
    try:
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return v


def _to_text(v):
    if v is None or v is pd.NA or (isinstance(v, float) and v != v):    # None / <NA> / NaN -> NULL
        return None
    if isinstance(v, float) and v.is_integer():      # Excel stores 5 as 5.0
        return str(int(v))
    return str(v)


def read_table(xl, spec: TableSpec):
    """Return (DataFrame | None, [Issue])."""
    issues: list = []
    if spec.sheet not in xl.sheet_names:
        return None, [Issue("ERROR", spec.table, f"sheet '{spec.sheet}' not found in workbook")]

    raw = xl.parse(spec.sheet, dtype=object)
    raw.columns = [str(c).strip() for c in raw.columns]
    missing = [c for c in spec.columns if c not in raw.columns]
    if missing:
        return None, [Issue("ERROR", spec.table, f"missing column(s) {missing} in sheet '{spec.sheet}'")]

    # Position -> what a human sees: Excel row number (header is row 1) or JSON record number (1-based).
    base = 1 if isinstance(xl, JsonWorkbook) else 2
    label = "record(s)" if base == 1 else "Excel row(s)"
    df = raw[list(spec.columns)].copy()
    df = df.apply(lambda col: col.map(_clean)).dropna(how="all")

    for col, typ in spec.columns.items():
        s = df[col]
        if typ == "text":
            df[col] = s.map(_to_text)
        elif typ in ("int", "float"):
            num = pd.to_numeric(s, errors="coerce")
            bad = s.notna() & num.isna()
            if bad.any():
                rows = [int(i) + base for i in df.index[bad][:5]]
                issues.append(Issue("ERROR", spec.table,
                                    f"column '{col}' has non-numeric values {list(s[bad][:3])} ({label} {rows})"))
            df[col] = num.round().astype("Int64") if typ == "int" else num
        elif typ == "date":
            dt = pd.to_datetime(s, errors="coerce")
            bad = s.notna() & dt.isna()
            if bad.any():
                rows = [int(i) + base for i in df.index[bad][:5]]
                issues.append(Issue("ERROR", spec.table,
                                    f"column '{col}' has invalid dates {list(s[bad][:3])} ({label} {rows})"))
            df[col] = dt.dt.date

    df = df.astype(object).where(df.notna(), None)      # NaN / NaT / <NA>  ->  None  (SQL NULL)
    df.columns = [c.lower() for c in df.columns]
    df.attrs["row_base"] = base                         # used by validate.py for friendly messages
    return df, issues


def read_workbook(path: Path, specs: list):
    """Read all given specs from one workbook. Returns ({table: df}, [Issue])."""
    xl = open_workbook(path)
    out, all_issues = {}, []
    for spec in specs:
        df, issues = read_table(xl, spec)
        all_issues.extend(issues)
        if df is not None:
            out[spec.table] = df
            log.info("  extracted %-16s %5d rows  (sheet %s)", spec.table, len(df), spec.sheet)
    return out, all_issues