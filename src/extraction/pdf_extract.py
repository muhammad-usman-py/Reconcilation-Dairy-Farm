"""Automatic extraction for supported image-only PDF ledger layouts."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from .ocr_extract import pdf_to_lines, parse_kd_feeds, parse_quickbooks
from .schema import normalise_transactions, parse_amount


def detect_pdf_format(lines) -> str:
    """Identify a supported ledger layout from its first-page heading."""
    header_text = " ".join(
        line.upper()
        for _, _, line in lines[:40]
    )

    if "ACCOUNT QUICKREPORT" in header_text:
        return "quickbooks"

    if (
        "BUSINESS PARTNER LEDGER" in header_text
        or "GENERAL LEDGER" in header_text
    ):
        return "kd_feeds"

    raise ValueError(
        "This PDF ledger format is not recognized yet. "
        "Please use a supported QuickBooks or KD Feeds ledger."
    )


def extract_pdf(path: str | Path) -> pd.DataFrame:
    """
    Extract a supported PDF ledger and convert it to the common schema.

    Format is detected automatically; the client never chooses it manually.
    """
    poppler_path = os.getenv("POPPLER_PATH") or None
    lines = pdf_to_lines(path, poppler_path=poppler_path)
    ledger_format = detect_pdf_format(lines)

    if ledger_format == "quickbooks":
        raw = parse_quickbooks(lines)

        mapped = pd.DataFrame({
            "date": raw["date"],
            "reference": "",
            "description": raw["line"],
            "amount": raw["amount"],
        })

    else:
        raw = parse_kd_feeds(lines)

        debit = raw["debit"].map(parse_amount).fillna(0)
        credit = raw["credit"].map(parse_amount).fillna(0)

        mapped = pd.DataFrame({
            "date": raw["date"],
            "reference": raw["voucher"],
            "description": raw["narration"],
            "amount": credit - debit,
        })

    result = normalise_transactions(mapped, path)
    result.attrs["detected_format"] = ledger_format

    return result