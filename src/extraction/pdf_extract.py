"""Adaptive PDF ledger extraction with text-layer preference and OCR fallback."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

from .document_extract import transactions_from_lines
from .ocr_extract import detect_ledger_format, pdf_to_lines


def _embedded_text_lines(path: str | Path) -> list[tuple[int, int, str, float]]:
    """Read positioned-looking rows from a PDF text layer when one exists."""
    lines: list[tuple[int, int, str, float]] = []
    reader = PdfReader(str(path))
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except TypeError:
            text = page.extract_text() or ""
        for row_number, value in enumerate(text.splitlines()):
            value = value.strip()
            if value:
                lines.append((page_number, row_number, value, 100.0))
    return lines


def detect_pdf_format(lines) -> str:
    """Backward-compatible alias used by existing callers."""
    return detect_ledger_format(lines)


def extract_pdf(path: str | Path) -> pd.DataFrame:
    """Extract a PDF ledger, preferring exact embedded text over OCR."""
    text_error: Exception | None = None
    try:
        text_lines = _embedded_text_lines(path)
        if text_lines:
            return transactions_from_lines(
                text_lines,
                path,
                extraction_method="embedded PDF text",
            )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        text_error = exc

    poppler_path = os.getenv("POPPLER_PATH") or None
    ocr_lines = pdf_to_lines(path, poppler_path=poppler_path)
    try:
        result = transactions_from_lines(ocr_lines, path, extraction_method="PDF OCR")
    except ValueError as exc:
        if text_error is not None:
            raise ValueError(f"PDF text extraction failed ({text_error}); OCR also failed ({exc}).") from exc
        raise
    result.attrs["used_ocr_fallback"] = True
    return result
