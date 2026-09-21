"""File-type routing shared by the UI and tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .csv_extract import extract_csv
from .excel_extract import extract_excel
from .image_extract import extract_image
from .pdf_extract import extract_pdf


SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".pdf", ".png", ".jpg", ".jpeg"}


def extract_path(path: str | Path) -> pd.DataFrame:
    """Extract one ledger independently of the other ledger's file type."""
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        return extract_csv(source)
    if suffix in {".xlsx", ".xls"}:
        return extract_excel(source)
    if suffix == ".pdf":
        return extract_pdf(source)
    if suffix in {".png", ".jpg", ".jpeg"}:
        return extract_image(source)
    raise ValueError(
        f"Unsupported file format '{suffix or 'none'}'. Supported formats: "
        "CSV, XLSX, XLS, PDF, PNG, JPG and JPEG."
    )
