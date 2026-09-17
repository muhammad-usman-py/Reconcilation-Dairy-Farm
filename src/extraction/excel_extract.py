"""Excel ledger extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd

from .structured_extract import extract_structured_frame


def extract_excel(
    path: str | Path,
    column_mapping: Mapping[str, str] | None = None,
    sheet_name: int | str = 0,
) -> pd.DataFrame:
    """Read the first (or selected) Excel worksheet into the canonical schema."""
    raw = pd.read_excel(path, sheet_name=sheet_name)
    return extract_structured_frame(raw, path, column_mapping)
