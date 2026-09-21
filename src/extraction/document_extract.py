"""Convert detected ledger lines into the application's canonical schema."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .ocr_extract import detect_ledger_format, parse_debit_credit, parse_kd_feeds, parse_quickbooks
from .schema import normalise_transactions, parse_amount


def transactions_from_lines(
    lines,
    source_file: str | Path,
    *,
    extraction_method: str,
) -> pd.DataFrame:
    """Detect, parse and annotate a visual/text ledger representation."""
    ledger_format = detect_ledger_format(lines)

    if ledger_format == "quickbooks":
        raw = parse_quickbooks(lines)
        mapped = pd.DataFrame({
            "date": raw.get("date"),
            "reference": "",
            "description": raw.get("line"),
            "amount": raw.get("amount"),
        })
    else:
        raw = parse_kd_feeds(lines) if ledger_format == "kd_feeds" else parse_debit_credit(lines)
        if raw.empty:
            raise ValueError("No dated financial rows could be extracted from this ledger.")
        debit = raw["debit"].map(parse_amount).fillna(0)
        credit = raw["credit"].map(parse_amount).fillna(0)
        mapped = pd.DataFrame({
            "date": raw["date"],
            "reference": raw["voucher"] if "voucher" in raw else raw["reference"],
            "description": raw["narration"],
            "amount": credit - debit,
        })

    if mapped.empty:
        raise ValueError("No transactions could be extracted from this ledger.")
    result = normalise_transactions(mapped, source_file)
    if result.empty:
        raise ValueError("The extracted rows did not contain usable dates and amounts.")

    warnings: list[str] = []
    rejected = int(raw.attrs.get("rejected_rows", 0))
    if rejected:
        warnings.append(f"{rejected} dated row(s) were not accepted because three monetary columns were not visible.")
    dropped = len(mapped) - len(result)
    if dropped:
        warnings.append(f"{dropped} extracted row(s) were excluded because their date or amount was invalid.")
    if "ocr_confidence" in raw and "OCR" in extraction_method.upper():
        low_confidence = int((pd.to_numeric(raw["ocr_confidence"], errors="coerce") < 75).sum())
        if low_confidence:
            warnings.append(f"{low_confidence} row(s) have low OCR confidence and should be checked against the source.")

    review = mapped[["date", "reference", "description", "amount"]].copy()
    review["date"] = review["date"].astype(str)
    if "debit" in raw:
        review["debit"] = raw["debit"].map(parse_amount).to_numpy()
        review["credit"] = raw["credit"].map(parse_amount).to_numpy()
        review["balance"] = raw["balance"].map(parse_amount).to_numpy()
    if "ocr_confidence" in raw:
        review["ocr_confidence"] = pd.to_numeric(raw["ocr_confidence"], errors="coerce").round(1).to_numpy()

    result.attrs.update({
        "detected_format": ledger_format,
        "extraction_method": extraction_method,
        "extraction_warnings": warnings,
        "review_rows": review.to_dict(orient="records"),
        "review_required": "OCR" in extraction_method.upper() or bool(warnings),
    })
    return result
