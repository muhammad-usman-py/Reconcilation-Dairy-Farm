"""Direct PNG/JPEG ledger extraction."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .document_extract import transactions_from_lines
from .ocr_extract import image_file_to_lines


def extract_image(path: str | Path) -> pd.DataFrame:
    """OCR a ledger image and return canonical transactions with review metadata."""
    lines = image_file_to_lines(path)
    if not lines:
        raise ValueError("No readable text was found in this image.")
    return transactions_from_lines(lines, path, extraction_method="image OCR")
