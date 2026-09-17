import pandas as pd

from src.extraction.schema import normalise_transactions
from src.extraction.structured_extract import extract_structured_frame
from src.reconciliation.detailed_reconciliation import reconcile_detailed
from src.reconciliation.matcher import ReconciliationSettings, reconcile


def ledger(rows, source):
    return normalise_transactions(pd.DataFrame(rows), source)


def test_reconcile_reports_match_discrepancy_and_unmatched():
    left = ledger([
        {"date": "01/09/2026", "reference": "V-1", "description": "Maize feed bags", "amount": "1,000.00"},
        {"date": "02/09/2026", "reference": "V-2", "description": "Transport charge", "amount": "500.00"},
        {"date": "03/09/2026", "reference": "V-3", "description": "Mineral mix", "amount": "250.00"},
    ], "farm.csv")
    right = ledger([
        {"date": "02/09/2026", "reference": "A-77", "description": "maize feed", "amount": "1,000.00"},
        {"date": "02/09/2026", "reference": "A-78", "description": "Transport charges", "amount": "550.00"},
        {"date": "04/09/2026", "reference": "A-79", "description": "Medicine", "amount": "99.00"},
    ], "supplier.csv")
    result = reconcile(left, right, ReconciliationSettings(date_tolerance_days=1))
    assert set(result["status"]) == {"matched", "discrepancy", "unmatched_left", "unmatched_right"}
    assert "Amount mismatch" in result.loc[result.status == "discrepancy", "reason"].iloc[0]
    assert "farm_date" in result.columns
    assert "supplier_amount" in result.columns


def test_structured_extraction_calculates_signed_amount_from_debit_and_credit():
    raw = pd.DataFrame({
        "Posting Date": ["01/09/2026", "02/09/2026"],
        "Debit": ["250.00", ""],
        "Credit": ["", "1,000.00"],
    })
    result = extract_structured_frame(raw, "bank_ledger.xlsx")
    assert result["amount"].tolist() == [-250.0, 1000.0]


def test_unrelated_same_description_is_not_forced_into_a_discrepancy():
    left = ledger([{"date": "01/09/2026", "reference": "", "description": "General Journal", "amount": "100"}], "a.csv")
    right = ledger([{"date": "20/09/2026", "reference": "", "description": "General Journal", "amount": "900"}], "b.csv")
    result = reconcile(left, right)
    assert set(result["status"]) == {"unmatched_left", "unmatched_right"}


def test_detailed_reconciliation_groups_goods_brokery_and_freight():
    left = ledger([
        {"date": "01/09/2026", "reference": "P-1", "description": "Soya bean meal Party Wt 1000 Rate 1/1 Kg Pur#1 Veh 195-NAD Bags 20", "amount": "1000"},
        {"date": "01/09/2026", "reference": "P-1", "description": "Brokery PurInv#1 Veh 195-NAD", "amount": "10"},
        {"date": "02/09/2026", "reference": "A-1", "description": "Freight paid Veh No 195-NAD", "amount": "-100"},
    ], "kd_feeds.xlsx")
    right = ledger([
        {"date": "02/09/2026", "reference": "330", "description": "195/NAD 20 Bags Soya Been Meal 1000 1/1 Brok 10 Transp 100", "amount": "910"},
    ], "shc.xlsx")
    stages = []
    detailed = reconcile_detailed(left, right, progress=stages.append)
    assert len(detailed.matched_comparison) == 1
    assert detailed.matched_comparison.iloc[0]["Match?"] == "MATCH"
    assert detailed.difference_register.empty
    assert detailed.left_source["Recon ref"].nunique() == 1
    assert "Finding likely transaction counterparts" in stages
