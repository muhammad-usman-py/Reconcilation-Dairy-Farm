"""Synthetic extraction fixtures; no client ledger data is stored here."""

from pathlib import Path

import pandas as pd
import pytest

from src.extraction import dispatch
from src.extraction.document_extract import transactions_from_lines
from src.extraction.schema import normalise_transactions
from src.reconciliation.detailed_reconciliation import reconcile_detailed
from src.reconciliation.matcher import ReconciliationSettings
from src.reconciliation.signs import align_signs


def test_generic_debit_credit_lines_become_signed_transactions():
    lines = [
        (1, 10, "Date Reference Narration Debit Credit Balance", 100.0),
        (1, 20, "01/09/2026 V-1 Feed production 1,000.00 0.00 1,000.00", 94.0),
        (1, 30, "02/09/2026 V-2 Balance transfer 0.00 1,000.00 0.00", 93.0),
    ]
    result = transactions_from_lines(lines, "ledger.png", extraction_method="image OCR")
    assert result["amount"].tolist() == [-1000.0, 1000.0]
    assert result["reference"].tolist() == ["V-1", "V-2"]
    assert result.attrs["detected_format"] == "generic_debit_credit"
    assert result.attrs["review_required"]


@pytest.mark.parametrize(
    ("suffixes", "expected"),
    [
        ((".png", ".pdf"), ("image", "pdf")),
        ((".pdf", ".pdf"), ("pdf", "pdf")),
        ((".jpg", ".png"), ("image", "image")),
    ],
)
def test_each_ledger_format_is_dispatched_independently(tmp_path, monkeypatch, suffixes, expected):
    monkeypatch.setattr(dispatch, "extract_image", lambda path: f"image:{Path(path).suffix}")
    monkeypatch.setattr(dispatch, "extract_pdf", lambda path: f"pdf:{Path(path).suffix}")
    paths = []
    for number, suffix in enumerate(suffixes):
        path = tmp_path / f"ledger-{number}{suffix}"
        path.write_bytes(b"synthetic")
        paths.append(path)
    observed = tuple(dispatch.extract_path(path).split(":", 1)[0] for path in paths)
    assert observed == expected


def _ledger(date: str, amount: float, source: str) -> pd.DataFrame:
    return normalise_transactions(pd.DataFrame([{
        "date": date,
        "reference": "",
        "description": "FBR tax payment",
        "amount": amount,
    }]), source)


def test_detailed_matching_respects_configured_date_tolerance():
    left = _ledger("01/09/2026", 1000, "left.csv")
    right = _ledger("11/09/2026", 1000, "right.csv")
    strict = reconcile_detailed(left, right, ReconciliationSettings(date_tolerance_days=3))
    allowed = reconcile_detailed(left, right, ReconciliationSettings(date_tolerance_days=10))
    assert strict.matched_comparison.empty
    assert len(strict.difference_register) == 2
    assert len(allowed.matched_comparison) == 1


def test_auto_sign_alignment_requires_repeated_opposite_sign_evidence():
    left = normalise_transactions(pd.DataFrame([
        {"date": "01/09/2026", "reference": "1", "description": "A", "amount": -100},
        {"date": "02/09/2026", "reference": "2", "description": "B", "amount": 250},
    ]), "left.png")
    right = normalise_transactions(pd.DataFrame([
        {"date": "01/09/2026", "reference": "1", "description": "A", "amount": 100},
        {"date": "02/09/2026", "reference": "2", "description": "B", "amount": -250},
    ]), "right.pdf")
    _, aligned, note = align_signs(left, right, "Auto-align counterpart signs")
    assert aligned["amount"].tolist() == [-100.0, 250.0]
    assert "auto-reversed" in note
    assert "_recorded_amount" in aligned
