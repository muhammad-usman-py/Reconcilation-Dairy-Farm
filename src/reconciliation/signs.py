"""Explicit sign-convention handling for counterparty ledgers."""

from __future__ import annotations

import pandas as pd


SIGN_OPTIONS = (
    "Auto-align counterpart signs",
    "Keep recorded signs",
    "Reverse first ledger signs",
    "Reverse second ledger signs",
)


def _overlap_count(first: pd.Series, second: pd.Series, tolerance: float) -> int:
    left = sorted(float(value) for value in first if pd.notna(value) and float(value) != 0)
    right = sorted(float(value) for value in second if pd.notna(value) and float(value) != 0)
    i = j = count = 0
    while i < len(left) and j < len(right):
        difference = left[i] - right[j]
        if abs(difference) <= tolerance:
            count += 1
            i += 1
            j += 1
        elif difference < 0:
            i += 1
        else:
            j += 1
    return count


def _reverse(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.attrs = dict(frame.attrs)
    result["_recorded_amount"] = result["amount"]
    result["amount"] = -result["amount"]
    return result


def align_signs(
    left: pd.DataFrame,
    right: pd.DataFrame,
    mode: str,
    *,
    tolerance: float = 1.0,
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Return comparison copies and a human-readable sign decision."""
    if mode == "Reverse first ledger signs":
        return _reverse(left), right.copy(), "First ledger signs reversed for comparison."
    if mode == "Reverse second ledger signs":
        return left.copy(), _reverse(right), "Second ledger signs reversed for comparison."
    if mode == "Keep recorded signs":
        return left.copy(), right.copy(), "Recorded debit/credit signs kept unchanged."
    if mode != "Auto-align counterpart signs":
        raise ValueError(f"Unknown sign-handling mode: {mode}")

    same = _overlap_count(left["amount"], right["amount"], tolerance)
    opposite = _overlap_count(left["amount"], -right["amount"], tolerance)
    if opposite >= 2 and opposite > same:
        return (
            left.copy(),
            _reverse(right),
            f"Second ledger signs auto-reversed: {opposite} opposite-sign value overlaps versus {same} same-sign overlaps.",
        )
    return (
        left.copy(),
        right.copy(),
        f"Recorded signs kept: {same} same-sign value overlaps versus {opposite} opposite-sign overlaps.",
    )
