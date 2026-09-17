"""Canonical transaction schema used by every input extractor."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

CANONICAL_COLUMNS = ["date", "reference", "description", "amount", "source_file"]


def parse_dates(values: pd.Series) -> pd.Series:
    """Parse ISO dates exactly, then interpret common ledger dates as day-first."""
    raw = values.astype(str).str.strip()
    parsed = pd.to_datetime(raw.where(raw.str.match(r"^\d{4}-\d{2}-\d{2}$")), format="%Y-%m-%d", errors="coerce")
    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(raw.loc[missing], dayfirst=True, errors="coerce")
    return parsed


def parse_amount(value: object) -> float | None:
    """Convert typical ledger amount text to a signed float."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "")
    if not text or text.lower() in {"nan", "none", "-"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "")
    # Preserve decimal digits, a leading sign, and nothing else.
    text = re.sub(r"[^0-9.+-]", "", text)
    try:
        amount = float(text)
    except ValueError:
        return None
    return -abs(amount) if negative else amount


def normalise_transactions(frame: pd.DataFrame, source_file: str | Path) -> pd.DataFrame:
    """Validate and normalise a dataframe into the shared internal schema."""
    missing = set(CANONICAL_COLUMNS[:-1]) - set(frame.columns)
    if missing:
        raise ValueError(f"Extractor did not produce required columns: {', '.join(sorted(missing))}")

    result = frame.copy()
    result["date"] = parse_dates(result["date"])
    result["reference"] = result["reference"].fillna("").astype(str).str.strip()
    result["description"] = result["description"].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    result["amount"] = result["amount"].map(parse_amount)
    result["source_file"] = Path(source_file).name

    # Empty trailing rows are not transactions. Rows missing either a usable
    # date or amount cannot be reconciled reliably, so exclude them instead of
    # letting them fail later inside the matching engine.
    result = result.dropna(subset=["date", "amount"], how="any")
    return result[CANONICAL_COLUMNS].reset_index(drop=True)
