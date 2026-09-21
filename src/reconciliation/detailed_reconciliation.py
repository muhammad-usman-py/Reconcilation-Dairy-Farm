"""Business-transaction grouping for audit-style ledger reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from difflib import SequenceMatcher
from bisect import bisect_left, bisect_right
from collections.abc import Callable

import pandas as pd

from .matcher import ReconciliationSettings, _normalise_text, token_set_ratio


@dataclass
class DetailedReconciliationResult:
    """Tables used by the detailed client-style reconciliation workbook."""

    summary: pd.DataFrame
    difference_register: pd.DataFrame
    action_list: pd.DataFrame
    non_value_differences: pd.DataFrame
    matched_comparison: pd.DataFrame
    left_source: pd.DataFrame
    right_source: pd.DataFrame


def _first(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _vehicle(text: str) -> str:
    explicit = _first(r"\bVEH(?:ICLE)?\s*(?:NO\.?|#)?\s*[:#-]?\s*([A-Z0-9]+(?:\s*[-/]\s*[A-Z0-9]+)?)", text)
    fallback = _first(r"\b(\d{2,5}\s*[-/]\s*[A-Z]{1,4})\b", text)
    return re.sub(r"[^A-Z0-9]", "", explicit or fallback)


def _number(text: str, pattern: str) -> str:
    return _first(pattern, text).lstrip("0") or ""


def _number_value(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return float(match.group(1).replace(",", "")) if match else None


def _attributes(row: pd.Series) -> dict[str, object]:
    description = str(row["description"])
    reference = str(row["reference"])
    text = f"{description} {reference}".upper()
    vehicle = _vehicle(text)
    product = ""
    if re.search(r"SOYA\s+B[EA]EN\s+MEAL", text):
        product = "SOYA BEAN MEAL"
    elif re.search(r"(?:CANOLA|KENOLA)\s+MEAL", text):
        product = "CANOLA MEAL"
    elif re.search(r"(?:RAP|RAPE|SARSOON)\s+(?:SEED\s+)?MEAL", text):
        product = "RAP SEED MEAL"
    elif "GUAR MEAL" in text:
        product = "GUAR MEAL"
    return {
        "vehicle": vehicle,
        "purchase_number": _number(text, r"\bPUR(?:CHASE|INV)?\s*#?\s*(\d+)"),
        "invoice_number": _number(reference, r"\b(\d{1,8})\b"),
        "weight_kg": _number_value(r"\b(?:PARTY\s*)?WT\s*(\d{3,6})\b", text)
        or _number_value(r"\b(\d{3,6})\s*KG\b", text)
        or _number_value(r"\b(?:MEAL|MEEN)\s+(\d{3,6})\s+\d+(?:\.\d+)?\s*/", text),
        "rate": _number_value(r"(?:@\s*)?(\d+(?:\.\d+)?)\s*/\s*1\b", text),
        "bags": _number_value(r"\bBAGS?\s*(\d{1,5})\b", text)
        or _number_value(r"\b(\d{1,5})\s*BAGS?\b", text),
        "has_brokery": bool(re.search(r"\b(?:BROKERY|BROKER|COMMISSION|MISC)\b", text)),
        "has_freight": bool(re.search(r"\b(?:FREIGHT|TRANSP(?:ORT)?)\b", text)),
        "is_tax": bool(re.search(r"\b(?:FBR\s+)?TAX\b", text)),
        "is_opening": bool(re.search(r"\b(?:BALANCE\s+TRANSFER|OPENING\s+BALANCE|REMAINING\s+PAYMENT|WEIGHT\s*&\s*RATE\s+DIFFER)\b", text)),
        "product": product,
        "is_payment": bool(re.search(r"\b(?:PAID|ONLINE|CASH|CHEQUE)\b", text)) and not bool(re.search(r"\b(?:MEAL|SEED)\b", text)),
    }


def _group_key(side: str, row: pd.Series, attrs: dict[str, object], period_start: pd.Timestamp | None = None) -> str:
    purchase = str(attrs["purchase_number"])
    vehicle = str(attrs["vehicle"])
    reference = _normalise_text(row["reference"])
    date = pd.to_datetime(row["date"]).strftime("%Y%m%d")
    if bool(attrs["is_opening"]) or (side == "right" and period_start is not None and pd.to_datetime(row["date"]) < period_start):
        return "opening_balance"
    if bool(attrs["is_tax"]):
        return f"tax:{date}"
    if side == "left" and purchase:
        return f"purchase:{purchase}"
    if side == "right" and reference and not bool(attrs["is_payment"]):
        return f"invoice:{reference}"
    if bool(attrs["is_payment"]):
        return f"payment:{date}:{abs(float(row['amount'])):.2f}"
    product = str(attrs["product"])
    weight = attrs["weight_kg"]
    if product and pd.notna(weight):
        return f"business:{date}:{product}:{int(weight)}"
    if vehicle:
        return f"vehicle:{vehicle}:{date}"
    return f"row:{row.name}"


def _assign_left_ancillary_rows(work: pd.DataFrame) -> pd.DataFrame:
    """Attach freight/brokery lines to the closest purchase sharing a vehicle."""
    purchases = work[work["purchase_number"].astype(bool)].copy()
    if purchases.empty:
        return work
    for index, row in work.loc[work["purchase_number"].eq("") & work["vehicle"].astype(bool)].iterrows():
        vehicle = str(row["vehicle"])
        candidates = purchases[purchases["vehicle"].map(lambda candidate: _vehicle_matches(vehicle, str(candidate)))]
        if candidates.empty:
            continue
        gaps = (pd.to_datetime(candidates["date"]) - pd.to_datetime(row["date"])).abs().dt.days
        nearest_index = gaps.idxmin()
        if int(gaps.loc[nearest_index]) <= 10:
            work.loc[index, "group_key"] = work.loc[nearest_index, "group_key"]
    return work


def _vehicle_matches(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right or (min(len(left), len(right)) >= 3 and (left in right or right in left)):
        return True
    left_digits = re.match(r"\d+", left)
    right_digits = re.match(r"\d+", right)
    if not left_digits or not right_digits or left_digits.group() != right_digits.group():
        return False
    return SequenceMatcher(None, left, right).ratio() >= 0.70


def _build_groups(frame: pd.DataFrame, side: str, period_start: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = frame.copy().reset_index(names="source_row")
    attributes = work.apply(_attributes, axis=1, result_type="expand")
    work = pd.concat([work, attributes], axis=1)
    work["group_key"] = work.apply(lambda row: _group_key(side, row, row, period_start), axis=1)
    if side == "left":
        work = _assign_left_ancillary_rows(work)

    groups = []
    for key, rows in work.groupby("group_key", sort=False):
        dates = pd.to_datetime(rows["date"])
        descriptions = " + ".join(dict.fromkeys(rows["description"].astype(str)))
        references = ", ".join(dict.fromkeys(reference for reference in rows["reference"].astype(str) if reference))
        groups.append({
            "group_key": key,
            "date": dates.min(),
            "reference": references,
            "description": descriptions,
            "amount": float(rows["amount"].sum()),
            "source_file": rows["source_file"].iloc[0],
            "vehicle": next((value for value in rows["vehicle"] if value), ""),
            "purchase_number": next((value for value in rows["purchase_number"] if value), ""),
            "invoice_number": next((value for value in rows["invoice_number"] if value), ""),
            "product": next((value for value in rows["product"] if value), ""),
            "weight_kg": next((value for value in rows["weight_kg"] if pd.notna(value)), None),
            "rate": next((value for value in rows["rate"] if pd.notna(value)), None),
            "bags": next((value for value in rows["bags"] if pd.notna(value)), None),
            "has_brokery": bool(rows["has_brokery"].any()),
            "has_freight": bool(rows["has_freight"].any()),
            "is_payment": bool(rows["is_payment"].all()),
        })
    return pd.DataFrame(groups), work


def _candidate(left: pd.Series, right: pd.Series, settings: ReconciliationSettings) -> tuple[bool, float, int, float, float]:
    amount_difference = float(right["amount"]) - float(left["amount"])
    amount_match = abs(amount_difference) <= settings.amount_tolerance
    day_gap = abs((pd.to_datetime(left["date"]) - pd.to_datetime(right["date"])).days)
    left_vehicle, right_vehicle = str(left["vehicle"]), str(right["vehicle"])
    vehicle_match = _vehicle_matches(left_vehicle, right_vehicle)
    business_match = bool(left["product"] and left["product"] == right["product"] and pd.notna(left["weight_kg"]) and pd.notna(right["weight_kg"]) and abs(float(left["weight_kg"]) - float(right["weight_kg"])) <= 100)
    description_score = token_set_ratio(_normalise_text(left["description"]), _normalise_text(right["description"]))
    payment_pair = bool(left["is_payment"] and right["is_payment"])
    opening_match = left["group_key"] == right["group_key"] == "opening_balance"
    tax_match = str(left["group_key"]).startswith("tax:") and str(right["group_key"]).startswith("tax:")
    date_within_tolerance = day_gap <= settings.date_tolerance_days
    plausible = opening_match or (
        date_within_tolerance
        and (tax_match or vehicle_match or business_match or amount_match or description_score >= 82 or payment_pair)
    )
    score = (60 if vehicle_match or business_match or opening_match or tax_match else 0) + (25 if amount_match else max(0, 20 - abs(amount_difference) / max(abs(float(left["amount"])), abs(float(right["amount"])), 1) * 20)) + max(0, 10 - day_gap) + description_score * 0.05
    return plausible, score, day_gap, amount_difference, description_score


def _vehicle_number(value: object) -> str:
    """Return the stable numeric part of a vehicle number for tolerant matching."""
    match = re.match(r"\d+", str(value))
    return match.group() if match else ""


def _candidate_group_pairs(left_groups: pd.DataFrame, right_groups: pd.DataFrame, settings: ReconciliationSettings):
    """Yield only group pairs that can satisfy the detailed matching rules.

    The former implementation checked every left group against every right group.
    These indexes retain all valid rule paths while avoiding most fuzzy-text work.
    """
    by_vehicle: dict[str, set[int]] = {}
    by_vehicle_number: dict[str, set[int]] = {}
    by_product_weight: dict[tuple[str, int], set[int]] = {}
    by_date: dict[pd.Timestamp, set[int]] = {}
    payment_groups: set[int] = set()
    opening_groups: set[int] = set()
    tax_groups: set[int] = set()
    amounts: list[tuple[float, int]] = []

    for index, row in right_groups.iterrows():
        vehicle = str(row["vehicle"])
        if vehicle:
            by_vehicle.setdefault(vehicle, set()).add(index)
            number = _vehicle_number(vehicle)
            if number:
                by_vehicle_number.setdefault(number, set()).add(index)
        product, weight = str(row["product"]), row["weight_kg"]
        if product and pd.notna(weight):
            by_product_weight.setdefault((product, int(float(weight) // 100)), set()).add(index)
        date = pd.to_datetime(row["date"]).normalize()
        by_date.setdefault(date, set()).add(index)
        if bool(row["is_payment"]):
            payment_groups.add(index)
        if row["group_key"] == "opening_balance":
            opening_groups.add(index)
        if str(row["group_key"]).startswith("tax:"):
            tax_groups.add(index)
        amounts.append((float(row["amount"]), index))

    amounts.sort()
    amount_values = [value for value, _ in amounts]
    for left_index, left_row in left_groups.iterrows():
        candidates: set[int] = set()
        vehicle = str(left_row["vehicle"])
        if vehicle:
            candidates.update(by_vehicle.get(vehicle, ()))
            number = _vehicle_number(vehicle)
            if number:
                candidates.update(by_vehicle_number.get(number, ()))
        product, weight = str(left_row["product"]), left_row["weight_kg"]
        if product and pd.notna(weight):
            bucket = int(float(weight) // 100)
            for candidate_bucket in range(bucket - 1, bucket + 2):
                candidates.update(by_product_weight.get((product, candidate_bucket), ()))
        date = pd.to_datetime(left_row["date"]).normalize()
        for candidate_date in pd.date_range(
            date - pd.Timedelta(days=settings.date_tolerance_days),
            date + pd.Timedelta(days=settings.date_tolerance_days),
        ):
            candidates.update(by_date.get(candidate_date, ()))
        amount = float(left_row["amount"])
        start = bisect_left(amount_values, amount - settings.amount_tolerance)
        end = bisect_right(amount_values, amount + settings.amount_tolerance)
        candidates.update(index for _, index in amounts[start:end])
        if bool(left_row["is_payment"]):
            candidates.update(payment_groups)
        if left_row["group_key"] == "opening_balance":
            candidates.update(opening_groups)
        if str(left_row["group_key"]).startswith("tax:"):
            candidates.update(tax_groups)
        for right_index in sorted(candidates):
            yield left_index, right_index


def _category(left: pd.Series | None, right: pd.Series | None, difference: float) -> str:
    if left is None or right is None:
        return "Unmatched payment / expense" if (left is not None and bool(left["is_payment"])) or (right is not None and bool(right["is_payment"])) else "Unmatched transaction"
    if bool(left["has_freight"]) != bool(right["has_freight"]):
        return "Freight treatment"
    if bool(left["has_brokery"]) != bool(right["has_brokery"]):
        return "Brokery not recorded"
    if pd.notna(left["rate"]) and pd.notna(right["rate"]) and left["rate"] != right["rate"]:
        return "Rate dispute"
    if pd.notna(left["weight_kg"]) and pd.notna(right["weight_kg"]) and left["weight_kg"] != right["weight_kg"]:
        return "Weight discrepancy"
    return "Value difference"


def _subject(left: pd.Series | None, right: pd.Series | None) -> str:
    row = left if left is not None else right
    assert row is not None
    parts = []
    if row["vehicle"]:
        parts.append(f"Veh {row['vehicle']}")
    if row["purchase_number"]:
        parts.append(f"Pur#{row['purchase_number']}")
    if row["invoice_number"]:
        parts.append(f"Inv {row['invoice_number']}")
    return " / ".join(parts) or str(row["description"])[:110]


def _action(category: str, difference: float) -> tuple[str, str, str]:
    amount = f"{abs(difference):,.0f}"
    if category == "Rate dispute":
        return "BOTH", f"Confirm the agreed purchase rate and pass the required adjustment of {amount} in the book proved wrong.", "Signed purchase contract; weighbridge slip"
    if category == "Freight treatment":
        return "BOTH", f"Confirm whether freight is recoverable from the counterparty, then adjust {amount} accordingly.", "Freight bill; delivery contract"
    if category == "Brokery not recorded":
        return "KD FEEDS", f"Confirm brokery terms and post or obtain a credit note for {amount}.", "Brokery agreement; invoice"
    if category == "Weight discrepancy":
        return "BOTH", f"Verify the final weight and correct the overstatement/understatement of {amount}.", "Weighbridge slip; delivery note"
    if category == "Unmatched payment / expense":
        return "BOTH", f"Trace the payment and post or reverse the unrecorded amount of {amount}.", "Bank advice; bank statement"
    if category == "Unmatched transaction":
        return "BOTH", f"Trace the unmatched transaction and agree how {amount} should be posted.", "Voucher; invoice; supporting ledger"
    return "BOTH", f"Review source documents and agree the adjustment of {amount}.", "Voucher; invoice; supporting evidence"


def _source_table(work: pd.DataFrame, table_name: str) -> pd.DataFrame:
    columns = ["source_row", "date", "reference", "description"]
    if "_recorded_amount" in work:
        columns.append("_recorded_amount")
    columns.extend(["amount", "group_key", "recon_ref"])
    result = work.reindex(columns=columns).rename(columns={
        "source_row": "#", "date": "Date", "reference": "Reference", "description": "Description",
        "_recorded_amount": "Recorded amount", "amount": "Amount",
        "group_key": "Transaction group", "recon_ref": "Recon ref",
    })
    return result


def reconcile_detailed(
    left: pd.DataFrame,
    right: pd.DataFrame,
    settings: ReconciliationSettings | None = None,
    progress: Callable[[str], None] | None = None,
) -> DetailedReconciliationResult:
    """Reconcile business transaction groups and produce audit-ready tables."""
    settings = settings or ReconciliationSettings()
    period_start = pd.to_datetime(left["date"]).min()
    if progress:
        progress("Grouping the first ledger")
    left_groups, left_work = _build_groups(left, "left", period_start)
    if progress:
        progress("Grouping the second ledger")
    right_groups, right_work = _build_groups(right, "right", period_start)

    candidates: list[tuple[float, int, int, int, float, float]] = []
    if progress:
        progress("Finding likely transaction counterparts")
    for left_index, right_index in _candidate_group_pairs(left_groups, right_groups, settings):
        left_row, right_row = left_groups.loc[left_index], right_groups.loc[right_index]
        plausible, score, gap, difference, description_score = _candidate(left_row, right_row, settings)
        if plausible:
            candidates.append((score, left_index, right_index, gap, difference, description_score))

    used_left, used_right, pairs = set(), set(), []
    if progress:
        progress("Selecting one-to-one matches")
    for score, left_index, right_index, gap, difference, description_score in sorted(candidates, key=lambda item: (-item[0], item[1], item[2])):
        if left_index in used_left or right_index in used_right:
            continue
        used_left.add(left_index)
        used_right.add(right_index)
        pairs.append((left_index, right_index, gap, difference, description_score, score))

    comparison_rows, difference_rows = [], []
    recon_number = 1
    for left_index, right_index, gap, difference, description_score, score in pairs:
        left_row, right_row = left_groups.loc[left_index], right_groups.loc[right_index]
        ref = f"R-{recon_number:03d}"
        recon_number += 1
        left_work.loc[left_work["group_key"].eq(left_row["group_key"]), "recon_ref"] = ref
        right_work.loc[right_work["group_key"].eq(right_row["group_key"]), "recon_ref"] = ref
        match = abs(difference) <= settings.amount_tolerance
        category = _category(left_row, right_row, difference) if not match else ""
        comparison_rows.append({
            "Ref": ref, "Subject": _subject(left_row, right_row), "Date (left)": left_row["date"], "Date (right)": right_row["date"],
            "Day gap": gap, "Value per left": left_row["amount"], "Value per right": right_row["amount"],
            "Difference (right - left)": difference, "Match?": "MATCH" if match else "DIFFERENCE",
            "Left narration": left_row["description"], "Right narration": right_row["description"], "Match score": round(score, 1),
        })
        if not match:
            owner, action, evidence = _action(category, difference)
            difference_rows.append({
                "Ref": ref, "Subject": _subject(left_row, right_row), "Date (left)": left_row["date"], "Date (right)": right_row["date"],
                "Day gap": gap, "Value per left": left_row["amount"], "Value per right": right_row["amount"],
                "Difference (right - left)": difference, "Priority": "HIGH" if abs(difference) >= 100_000 else "MEDIUM" if abs(difference) >= 10_000 else "LOW",
                "Category": category, "Explanation": f"Matched as {_subject(left_row, right_row)}. Group totals differ by {abs(difference):,.0f}.", "Action required": action,
                "Owner": owner, "Evidence to obtain": evidence,
            })

    for side, groups, used in (("left", left_groups, used_left), ("right", right_groups, used_right)):
        for group_index, row in groups.loc[~groups.index.isin(used)].iterrows():
            ref = f"R-{recon_number:03d}"
            recon_number += 1
            work = left_work if side == "left" else right_work
            work.loc[work["group_key"].eq(row["group_key"]), "recon_ref"] = ref
            difference = -float(row["amount"]) if side == "left" else float(row["amount"])
            category = _category(row if side == "left" else None, row if side == "right" else None, difference)
            owner, action, evidence = _action(category, difference)
            difference_rows.append({
                "Ref": ref, "Subject": _subject(row, None) if side == "left" else _subject(None, row), "Date (left)": row["date"] if side == "left" else None, "Date (right)": row["date"] if side == "right" else None,
                "Day gap": None, "Value per left": row["amount"] if side == "left" else 0.0, "Value per right": row["amount"] if side == "right" else 0.0,
                "Difference (right - left)": difference, "Priority": "HIGH" if abs(difference) >= 100_000 else "MEDIUM" if abs(difference) >= 10_000 else "LOW",
                "Category": category, "Explanation": f"Present only in the {side} ledger; no reliable counterpart was found.", "Action required": action,
                "Owner": owner, "Evidence to obtain": evidence,
            })

    if progress:
        progress("Preparing audit tables")
    comparison = pd.DataFrame(comparison_rows)
    differences = pd.DataFrame(difference_rows).sort_values(["Priority", "Difference (right - left)"], ascending=[True, False], key=lambda series: series.map({"HIGH": 0, "MEDIUM": 1, "LOW": 2}).fillna(series) if series.name == "Priority" else series.abs(), na_position="last") if difference_rows else pd.DataFrame(columns=["Ref", "Subject", "Date (left)", "Date (right)", "Day gap", "Value per left", "Value per right", "Difference (right - left)", "Priority", "Category", "Explanation", "Action required", "Owner", "Evidence to obtain"])
    actions = differences.loc[:, ["Ref", "Priority", "Owner", "Difference (right - left)", "Subject", "Action required", "Evidence to obtain"]].copy()
    actions.insert(0, "#", range(1, len(actions) + 1))
    actions["Status"] = "OPEN"
    actions["Target date"] = ""

    non_value_rows = []
    for row in comparison_rows:
        if row["Match?"] != "MATCH":
            continue
        left_bank = _first(r"\bONLINE\s+([A-Z]+)", row["Left narration"])
        right_bank = _first(r"\bONLINE\s+([A-Z]+)", row["Right narration"])
        if left_bank and right_bank and left_bank != right_bank:
            non_value_rows.append({"Ref": row["Ref"], "Type": "Bank name", "Date": row["Date (left)"], "Item": f"{abs(row['Value per left']):,.0f}", "What differs": f"Left: {left_bank}; right: {right_bank}.", "Action required": "Confirm the remitting bank from the bank advice and correct the wrong narration."})

    total_left, total_right = float(left["amount"].sum()), float(right["amount"].sum())
    summary_rows = [
        {"Section": "Balances", "Metric": "Closing balance per left ledger", "Items": len(left), "Amount": total_left},
        {"Section": "Balances", "Metric": "Closing balance per right ledger", "Items": len(right), "Amount": total_right},
        {"Section": "Balances", "Metric": "Unreconciled difference (right - left)", "Items": "", "Amount": total_right - total_left},
    ]
    if not differences.empty:
        category_summary = differences.groupby("Category", dropna=False).agg(Items=("Ref", "count"), Amount=("Difference (right - left)", "sum")).reset_index()
        for _, row in category_summary.iterrows():
            summary_rows.append({"Section": "Difference reconciliation", "Metric": row["Category"], "Items": int(row["Items"]), "Amount": float(row["Amount"])})

    return DetailedReconciliationResult(
        summary=pd.DataFrame(summary_rows), difference_register=differences, action_list=actions,
        non_value_differences=pd.DataFrame(non_value_rows, columns=["Ref", "Type", "Date", "Item", "What differs", "Action required"]),
        matched_comparison=comparison, left_source=_source_table(left_work, "left"), right_source=_source_table(right_work, "right"),
    )
