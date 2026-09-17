"""Plain-language dashboard calculations; no Streamlit or database side effects."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import math

import pandas as pd

DIFFERENCE = "Difference (right - left)"
STATUSES = ["OPEN", "UNDER REVIEW", "RESOLVED", "WAIVED"]
PRIORITIES = ["HIGH", "MEDIUM", "LOW"]


def money(value: float) -> str:
    return f"PKR {value:,.2f}"


def _cents(value) -> int:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("A ledger amount is missing or is not a finite number.")
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _numbers(frame, column):
    if frame.empty:
        return pd.Series(dtype="float64", index=frame.index)
    values = pd.to_numeric(frame[column], errors="raise")
    if not values.map(lambda value: pd.notna(value) and math.isfinite(float(value))).all():
        raise ValueError(f"Invalid or missing amounts in {column}.")
    return values


def reviewed_differences(differences, reviews=None):
    """Unknown/missing decisions stay open; stale review refs are ignored."""
    result = differences.drop(columns=["Review status", "Reviewer", "Notes"], errors="ignore").copy()
    if "Ref" not in result:
        result["Ref"] = pd.Series(dtype=str)
    if result["Ref"].isna().any() or result["Ref"].duplicated().any():
        raise ValueError("Difference references must be present and unique.")
    if reviews is not None and not reviews.empty:
        selected = reviews.reindex(columns=["Ref", "Review status", "Reviewer", "Notes"])
        result = result.merge(selected.drop_duplicates("Ref", keep="last"), on="Ref", how="left", validate="one_to_one")
    for column, default in [("Review status", "OPEN"), ("Reviewer", ""), ("Notes", "")]:
        if column not in result:
            result[column] = default
        result[column] = result[column].fillna(default)
    result["Review status"] = result["Review status"].astype(str).str.upper().str.strip()
    result.loc[~result["Review status"].isin(STATUSES), "Review status"] = "OPEN"
    result["Absolute difference (PKR)"] = _numbers(result, DIFFERENCE).abs()
    return result


@dataclass
class Dashboard:
    metrics: dict
    differences: pd.DataFrame
    categories: pd.DataFrame
    priorities: pd.DataFrame
    statuses: pd.DataFrame
    health: str
    health_reason: str


def build_dashboard(detailed, reviews=None) -> Dashboard:
    balances = detailed.summary.loc[detailed.summary["Section"].eq("Balances"), "Amount"]
    if len(balances) < 2:
        raise ValueError("Both ledger totals are required to build the dashboard.")
    first, second = (_cents(balances.iloc[i]) for i in range(2))
    gap = second - first
    pairs = detailed.matched_comparison
    if not pairs.empty and (pairs["Ref"].isna().any() or pairs["Ref"].duplicated().any()):
        raise ValueError("Matched group references must be present and unique.")
    differences = reviewed_differences(detailed.difference_register, reviews)
    paired_refs = set(pairs["Ref"]) if not pairs.empty else set()
    unpaired = differences.loc[~differences["Ref"].isin(paired_refs)]
    pair_values = _numbers(pairs, DIFFERENCE).map(_cents)
    unpaired_values = _numbers(unpaired, DIFFERENCE).map(_cents)
    exact = int(pair_values.eq(0).sum())
    accepted = pairs["Match?"].eq("MATCH") if not pairs.empty else pd.Series(dtype=bool)
    within_tolerance = int((accepted & pair_values.ne(0)).sum())
    total_groups = len(pairs) + len(unpaired)
    outstanding = differences["Review status"].isin(["OPEN", "UNDER REVIEW"])
    open_items = differences.loc[outstanding]
    high_open = int(open_items.get("Priority", pd.Series(dtype=str)).eq("HIGH").sum())
    explained = int(pair_values.sum() + unpaired_values.sum())
    remainder = gap - explained
    register_total = int(_numbers(differences, DIFFERENCE).map(_cents).sum())
    # Small accepted differences are excluded from the exception register, not from proof.
    outside_register = int(pair_values.loc[~pairs["Ref"].isin(set(differences["Ref"]))].sum()) if not pairs.empty else 0
    register_remainder = gap - register_total - outside_register
    source_total_mismatch = any(
        _cents(_numbers(frame, "Amount").sum()) != balance
        for frame, balance in [(detailed.left_source, first), (detailed.right_source, second)]
    )
    source_rows = len(detailed.left_source) + len(detailed.right_source)
    paired_rows = sum(int(frame.get("Recon ref", pd.Series(dtype=str)).isin(paired_refs).sum())
                      for frame in [detailed.left_source, detailed.right_source])
    metrics = {
        "first_balance": first / 100, "second_balance": second / 100, "gap": gap / 100,
        "paired_groups": len(pairs), "total_groups": total_groups, "exact_groups": exact,
        "within_tolerance": within_tolerance, "unpaired_groups": len(unpaired),
        "match_rate": 100 * len(pairs) / total_groups if total_groups else None,
        "outstanding": int(outstanding.sum()), "high_open": high_open,
        "gross_open": float(open_items["Absolute difference (PKR)"].sum()),
        "gross_all": float(differences["Absolute difference (PKR)"].sum()),
        "resolved": int(differences["Review status"].eq("RESOLVED").sum()),
        "waived": int(differences["Review status"].eq("WAIVED").sum()),
        "control_issues": len(detailed.non_value_differences), "source_rows": source_rows,
        "paired_rows": paired_rows, "register_total": register_total / 100,
        "tolerance_total": outside_register / 100, "explained": explained / 100,
        "remainder": remainder / 100, "register_remainder": register_remainder / 100,
        "source_total_mismatch": source_total_mismatch,
    }
    if not source_rows or not total_groups:
        health, reason = "No data", "No transaction groups are available to assess."
    elif remainder or register_remainder or source_total_mismatch:
        health, reason = "Critical", "The report totals do not fully tie out. Check extraction and group coverage before sign-off."
    elif high_open:
        health, reason = "Critical", f"{high_open} high-priority difference(s) still need a decision."
    elif outstanding.any() or gap or len(unpaired) or len(detailed.non_value_differences):
        health, reason = "Needs Review", "There are open items, unmatched groups, control issues, or a remaining ledger gap."
    else:
        health, reason = "Good", "No open value differences or detected control issues; extracted totals agree. Verify source completeness before sign-off."
    if differences.empty:
        categories = pd.DataFrame(columns=["Category", "Items", "Gross difference (PKR)", "Net change (PKR)"])
    else:
        categories = differences.groupby("Category", dropna=False).agg(
            Items=("Ref", "count"),
            **{"Gross difference (PKR)": ("Absolute difference (PKR)", "sum"), "Net change (PKR)": (DIFFERENCE, "sum")},
        ).reset_index().sort_values("Gross difference (PKR)", ascending=False, kind="stable")
    priorities = open_items.get("Priority", pd.Series(dtype=str)).value_counts().reindex(PRIORITIES, fill_value=0).rename_axis("Priority").reset_index(name="Items")
    statuses = differences["Review status"].value_counts().reindex(STATUSES, fill_value=0).rename_axis("Status").reset_index(name="Items")
    return Dashboard(metrics, differences, categories, priorities, statuses, health, reason)
