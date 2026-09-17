"""Deterministic, one-to-one ledger reconciliation."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path
import re

import pandas as pd
try:
    from rapidfuzz.fuzz import token_set_ratio
except ImportError:  # keeps core reconciliation usable before dependencies are installed
    from difflib import SequenceMatcher

    def token_set_ratio(left: str, right: str) -> float:
        """Small dependency-free fallback; production installs rapidfuzz."""
        left_tokens, right_tokens = set(left.lower().split()), set(right.lower().split())
        shared = " ".join(sorted(left_tokens & right_tokens))
        left_joined = " ".join(sorted(left_tokens))
        right_joined = " ".join(sorted(right_tokens))
        return 100 * max(
            SequenceMatcher(None, left_joined, right_joined).ratio(),
            SequenceMatcher(None, shared, left_joined).ratio() if shared else 0,
            SequenceMatcher(None, shared, right_joined).ratio() if shared else 0,
        )


@dataclass(frozen=True)
class ReconciliationSettings:
    amount_tolerance: float = 1.0
    date_tolerance_days: int = 3
    description_threshold: float = 75.0


def _as_date(value: object) -> pd.Timestamp | None:
    result = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(result) else result.normalize()


def _normalise_text(value: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))


def _comparison(left: pd.Series, right: pd.Series, settings: ReconciliationSettings) -> dict[str, float | bool | int]:
    left_amount, right_amount = float(left["amount"]), float(right["amount"])
    amount_difference = left_amount - right_amount
    amount_match = abs(amount_difference) <= settings.amount_tolerance

    left_date, right_date = _as_date(left["date"]), _as_date(right["date"])
    date_difference = abs((left_date - right_date).days) if left_date is not None and right_date is not None else 9999
    date_match = date_difference <= settings.date_tolerance_days

    description_score = token_set_ratio(_normalise_text(left["description"]), _normalise_text(right["description"]))
    description_match = description_score >= settings.description_threshold

    left_reference = _normalise_text(left["reference"])
    right_reference = _normalise_text(right["reference"])
    reference_match = bool(left_reference and left_reference == right_reference)

    date_score = max(0.0, 35.0 * (1 - min(date_difference, 30) / 30))
    amount_score = 40.0 if amount_match else max(0.0, 40.0 * (1 - abs(amount_difference) / max(abs(left_amount), abs(right_amount), 1.0)))
    score = date_score + amount_score + (description_score * 0.20) + (5.0 if reference_match else 0.0)
    return {
        "score": round(score, 2), "amount_difference": round(amount_difference, 2),
        "date_difference_days": date_difference, "description_score": round(description_score, 1),
        "amount_match": amount_match, "date_match": date_match, "description_match": description_match,
        "reference_match": reference_match,
    }


def _plausible(values: dict[str, float | bool | int]) -> bool:
    """Require an objective ledger anchor before pairing two rows.

    Description alone is never enough: generic ledger text can otherwise turn
    unrelated entries into a false discrepancy.  A matching reference, an
    amount/date pair, or one quantitative field plus meaningful text is needed.
    """
    return bool(
        values["reference_match"]
        or (values["date_match"] and values["amount_match"])
        or (values["date_match"] and values["description_score"] >= 70)
        or (values["amount_match"] and values["description_score"] >= 60)
    )


def _reason(values: dict[str, float | bool | int]) -> tuple[str, str]:
    failures = []
    if not values["date_match"]:
        failures.append("date mismatch")
    if not values["amount_match"]:
        failures.append("amount mismatch")
    if not values["description_match"]:
        failures.append("description mismatch")
    return ("matched", "All comparison checks passed.") if not failures else ("discrepancy", ", ".join(failures).capitalize() + ".")


def _record(
    status: str,
    reason: str,
    left: pd.Series | None,
    right: pd.Series | None,
    left_label: str,
    right_label: str,
    values: dict | None = None,
) -> dict:
    values = values or {}
    def value(row: pd.Series | None, column: str):
        return None if row is None else row.get(column)
    return {
        "status": status, "reason": reason,
        f"{left_label}_date": value(left, "date"), f"{right_label}_date": value(right, "date"),
        f"{left_label}_reference": value(left, "reference"), f"{right_label}_reference": value(right, "reference"),
        f"{left_label}_description": value(left, "description"), f"{right_label}_description": value(right, "description"),
        f"{left_label}_amount": value(left, "amount"), f"{right_label}_amount": value(right, "amount"),
        "amount_difference": values.get("amount_difference"),
        "date_difference_days": values.get("date_difference_days"),
        "description_similarity": values.get("description_score"),
        "match_score": values.get("score"),
    }


def _table_label(frame: pd.DataFrame, fallback: str) -> str:
    source = str(frame["source_file"].iloc[0]) if not frame.empty else fallback
    label = re.sub(r"[^a-z0-9]+", "_", Path(source).stem.lower()).strip("_")
    return label or fallback


def _candidate_pairs(left: pd.DataFrame, right: pd.DataFrame, settings: ReconciliationSettings):
    """Yield only rows that share a date, amount, or non-empty reference.

    This replaces the full left×right scan.  It remains deterministic and is
    especially faster for large ledgers spread across many dates.
    """
    date_index: dict[pd.Timestamp, list[int]] = {}
    reference_index: dict[str, list[int]] = {}
    amount_index: list[tuple[float, int]] = []
    for j, row in right.iterrows():
        date = _as_date(row["date"])
        if date is not None:
            date_index.setdefault(date, []).append(j)
        reference = _normalise_text(row["reference"])
        if reference:
            reference_index.setdefault(reference, []).append(j)
        amount_index.append((float(row["amount"]), j))

    amount_index.sort()
    sorted_amounts = [amount for amount, _ in amount_index]
    for i, row in left.iterrows():
        candidates: set[int] = set()
        date = _as_date(row["date"])
        if date is not None:
            for day in pd.date_range(date - pd.Timedelta(days=settings.date_tolerance_days), date + pd.Timedelta(days=settings.date_tolerance_days)):
                candidates.update(date_index.get(day, ()))

        amount = float(row["amount"])
        start = bisect_left(sorted_amounts, amount - settings.amount_tolerance)
        end = bisect_right(sorted_amounts, amount + settings.amount_tolerance)
        candidates.update(j for _, j in amount_index[start:end])

        reference = _normalise_text(row["reference"])
        if reference:
            candidates.update(reference_index.get(reference, ()))
        for j in sorted(candidates):
            yield i, j


def reconcile(left: pd.DataFrame, right: pd.DataFrame, settings: ReconciliationSettings | None = None) -> pd.DataFrame:
    """Create an auditable, one-to-one reconciliation report.

    Exact and close candidates are scored deterministically. Highest-confidence
    pairs are assigned first, so a transaction cannot match multiple rows.
    """
    settings = settings or ReconciliationSettings()
    required = {"date", "reference", "description", "amount", "source_file"}
    for name, frame in (("left", left), ("right", right)):
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{name} ledger is missing: {', '.join(sorted(missing))}")
    left, right = left.reset_index(drop=True), right.reset_index(drop=True)
    left_label, right_label = _table_label(left, "first_table"), _table_label(right, "second_table")
    if left_label == right_label:
        right_label = f"{right_label}_2"
    candidates: list[tuple[float, int, int, dict]] = []
    for i, j in _candidate_pairs(left, right, settings):
        values = _comparison(left.iloc[i], right.iloc[j], settings)
        if _plausible(values):
            candidates.append((float(values["score"]), i, j, values))

    used_left, used_right, records = set(), set(), []
    for _score, i, j, values in sorted(candidates, key=lambda item: (-item[0], item[1], item[2])):
        if i in used_left or j in used_right:
            continue
        used_left.add(i)
        used_right.add(j)
        status, reason = _reason(values)
        records.append(_record(status, reason, left.iloc[i], right.iloc[j], left_label, right_label, values))

    for i, row in left.iterrows():
        if i not in used_left:
            records.append(_record("unmatched_left", "Only present in the first ledger.", row, None, left_label, right_label))
    for j, row in right.iterrows():
        if j not in used_right:
            records.append(_record("unmatched_right", "Only present in the second ledger.", None, row, left_label, right_label))
    return pd.DataFrame(records).sort_values(["status", f"{left_label}_date", f"{right_label}_date"], na_position="last").reset_index(drop=True)
