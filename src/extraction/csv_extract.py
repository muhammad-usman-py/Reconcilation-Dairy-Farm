"""CSV ledger extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd

from .structured_extract import extract_structured_frame


def extract_csv(path: str | Path, column_mapping: Mapping[str, str] | None = None) -> pd.DataFrame:
    """Read a CSV ledger and return canonical transactions."""
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        raw = pd.read_csv(path, encoding="latin-1")
    return extract_structured_frame(raw, path, column_mapping)
