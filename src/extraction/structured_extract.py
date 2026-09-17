"""Column discovery and normalisation for structured ledger files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

import pandas as pd

from .schema import normalise_transactions

ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "transaction date", "entry date", "posting date", "doc date"),
    "reference": ("reference", "ref", "voucher", "voucher no", "voucher number", "document no", "invoice no", "invoice number", "bill no", "num"),
    "description": ("description", "memo", "narration", "particulars", "details", "item", "product", "remarks"),
    "amount": ("amount", "net amount", "transaction amount", "value", "total"),
}

DEBIT_ALIASES = ("debit", "dr", "debit amount", "withdrawal", "paid out")
CREDIT_ALIASES = ("credit", "cr", "credit amount", "deposit", "received")


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def discover_column_mapping(columns: list[object]) -> dict[str, str | None]:
    """Find common ledger column names; returns None for unknown fields."""
    normalised = {_key(column): str(column) for column in columns}
    mapping: dict[str, str | None] = {}
    for target, aliases in ALIASES.items():
        mapping[target] = next((normalised[alias] for alias in aliases if alias in normalised), None)
    mapping["debit"] = next((normalised[alias] for alias in DEBIT_ALIASES if alias in normalised), None)
    mapping["credit"] = next((normalised[alias] for alias in CREDIT_ALIASES if alias in normalised), None)
    return mapping


def extract_structured_frame(
    raw: pd.DataFrame,
    source_file: str | Path,
    column_mapping: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Map a CSV/Excel ledger dataframe to the format-neutral schema.

    Mapping is optional for normal files. Pass it when a counterparty uses an
    unusual heading, for example ``{'description': 'Goods Detail'}``.
    """
    if raw.empty:
        raise ValueError("The uploaded file contains no rows.")
    inferred = discover_column_mapping(list(raw.columns))
    mapping = {**inferred, **(column_mapping or {})}
    has_split_amounts = bool(mapping.get("debit") or mapping.get("credit"))
    if not mapping["date"] or (not mapping["amount"] and not has_split_amounts):
        available = ", ".join(map(str, raw.columns))
        raise ValueError(
            "Could not identify a Date column and either an Amount column or Debit/Credit columns. "
            f"Available columns: {available}. Add an explicit mapping for this ledger format."
        )

    if mapping["amount"]:
        amounts = raw[mapping["amount"]]
    else:
        debit = raw[mapping["debit"]].map(_parse_amount_or_zero) if mapping.get("debit") else 0.0
        credit = raw[mapping["credit"]].map(_parse_amount_or_zero) if mapping.get("credit") else 0.0
        amounts = credit - debit

    result = pd.DataFrame({
        "date": raw[mapping["date"]],
        "reference": raw[mapping["reference"]] if mapping["reference"] else "",
        "description": raw[mapping["description"]] if mapping["description"] else "",
        "amount": amounts,
    })
    return normalise_transactions(result, source_file)


def _parse_amount_or_zero(value: object) -> float:
    """Parse a debit/credit cell; blank ledger cells represent zero."""
    from .schema import parse_amount

    return parse_amount(value) or 0.0
