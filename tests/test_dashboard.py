"""Synthetic fixtures: never client financial data."""
from types import SimpleNamespace

import pandas as pd
import pytest

from src.reconciliation.dashboard import DIFFERENCE, build_dashboard, reviewed_differences


def sample_report(pair_differences=(0, 300, -200, .5), unmatched=(50,), states=None):
    pairs, exceptions, left, right = [], [], [], []
    for i, delta in enumerate(pair_differences, 1):
        ref = f"R-{i:03d}"
        state = states[i - 1] if states else ("MATCH" if abs(delta) <= 1 else "DIFFERENCE")
        pairs.append({"Ref": ref, DIFFERENCE: delta, "Match?": state})
        left.append({"Amount": 1000., "Recon ref": ref})
        right.append({"Amount": 1000. + delta, "Recon ref": ref})
        if state != "MATCH":
            exceptions.append({"Ref": ref, DIFFERENCE: delta})
    for i, delta in enumerate(unmatched, len(pairs) + 1):
        ref = f"R-{i:03d}"
        exceptions.append({"Ref": ref, DIFFERENCE: delta})
        (right if delta >= 0 else left).append({"Amount": abs(delta), "Recon ref": ref})
    for row in exceptions:
        row.update({"Priority": "HIGH" if abs(row[DIFFERENCE]) >= 100000 else "MEDIUM", "Category": "Rate dispute",
                    "Subject": "Veh 195-NAD / invoice 123", "Owner": "BOTH", "Action required": "Confirm the agreed rate.", "Evidence to obtain": "Signed invoice"})
    lc, rc = pd.DataFrame(left, columns=["Amount", "Recon ref"]), pd.DataFrame(right, columns=["Amount", "Recon ref"])
    return SimpleNamespace(
        summary=pd.DataFrame({"Section": ["Balances", "Balances"], "Amount": [lc.Amount.sum(), rc.Amount.sum()]}),
        matched_comparison=pd.DataFrame(pairs, columns=["Ref", DIFFERENCE, "Match?"]),
        difference_register=pd.DataFrame(exceptions, columns=["Ref", DIFFERENCE, "Priority", "Category", "Subject", "Owner", "Action required", "Evidence to obtain"]),
        action_list=pd.DataFrame(exceptions), non_value_differences=pd.DataFrame(), left_source=lc, right_source=rc,
    )


def test_kpis_and_proof_account_for_accepted_tolerance():
    snap = build_dashboard(sample_report())
    m = snap.metrics
    assert m["gap"] == 150.5
    assert m["gross_open"] == 550
    assert (m["paired_groups"], m["total_groups"], m["unpaired_groups"]) == (4, 5, 1)
    assert m["match_rate"] == 80
    assert m["exact_groups"] == 1 and m["within_tolerance"] == 1
    assert m["register_total"] == 150 and m["tolerance_total"] == .5
    assert m["register_remainder"] == m["remainder"] == 0
    assert m["paired_rows"] == 8 and m["source_rows"] == 9


def test_zero_net_gap_does_not_hide_open_risk():
    snap = build_dashboard(sample_report((100000, -100000), ()))
    assert snap.metrics["gap"] == 0
    assert snap.metrics["gross_open"] == 200000
    assert snap.health == "Critical"


def test_review_decisions_reduce_workload_not_original_gap():
    report = sample_report((100000, 300, -200), ())
    reviews = pd.DataFrame({"Ref": ["R-001", "R-002", "STALE"], "Review status": ["RESOLVED", "WAIVED", "RESOLVED"]})
    snap = build_dashboard(report, reviews)
    assert snap.metrics["outstanding"] == 1 and snap.metrics["gross_open"] == 200
    assert snap.metrics["high_open"] == 0
    assert snap.metrics["gap"] == 100100
    assert snap.metrics["resolved"] == snap.metrics["waived"] == 1
    assert snap.health == "Needs Review"


def test_unknown_status_is_open():
    snap = build_dashboard(sample_report((100,), ()), pd.DataFrame({"Ref": ["R-001"], "Review status": ["unknown"]}))
    assert snap.metrics["outstanding"] == 1


def test_no_matches_and_empty_ledgers():
    unmatched = build_dashboard(sample_report((), (100, -200)))
    assert unmatched.metrics["match_rate"] == 0
    assert unmatched.metrics["register_remainder"] == 0
    empty = build_dashboard(sample_report((), ()))
    assert empty.health == "No data" and empty.metrics["match_rate"] is None


def test_all_equal_and_control_findings():
    report = sample_report((0, 0), ())
    assert build_dashboard(report).health == "Good"
    report.non_value_differences = pd.DataFrame({"Ref": ["R-001"]})
    assert build_dashboard(report).health == "Needs Review"


def test_all_closed_with_gap_does_not_become_good():
    report = sample_report((200000,), ())
    reviews = pd.DataFrame({"Ref": ["R-001"], "Review status": ["RESOLVED"]})
    snapshot = build_dashboard(report, reviews)
    assert snapshot.health == "Needs Review"
    assert snapshot.metrics["outstanding"] == 0


def test_missing_difference_detail_and_changed_balance_raise_health_alert():
    report = sample_report((100,), ())
    report.summary.loc[1, "Amount"] += 50
    assert build_dashboard(report).health == "Critical"


def test_source_totals_are_cross_checked():
    report = sample_report((0,), ())
    report.left_source.loc[0, "Amount"] += 1
    assert build_dashboard(report).metrics["source_total_mismatch"]
    assert build_dashboard(report).health == "Critical"


def test_duplicate_and_invalid_amounts_rejected():
    report = sample_report((10,), ())
    with pytest.raises(ValueError, match="unique"):
        reviewed_differences(pd.concat([report.difference_register] * 2))
    report.summary.loc[1, "Amount"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        build_dashboard(report)


def test_negative_gap_and_cent_precision():
    snap = build_dashboard(sample_report((-4293744,), ()))
    assert snap.metrics["gap"] == -4293744
    snap = build_dashboard(sample_report((.01, 0), ()))
    assert snap.metrics["exact_groups"] == 1
    assert snap.metrics["within_tolerance"] == 1
    assert snap.metrics["gap"] == .01


def test_dashboard_ui_filters_and_empty_state():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string('''
from tests.test_dashboard import sample_report
from src.reconciliation.dashboard_ui import render_dashboard
render_dashboard(sample_report(), "First.xlsx", "Second.xlsx")
''').run(timeout=30)
    assert not at.exception
    assert any(m.label == "Group match rate" and m.value == "80.0%" for m in at.metric)
    at.multiselect[0].set_value([]).run()
    assert not at.exception
    assert any("No items match" in i.value for i in at.info)
    at = AppTest.from_string('''
from tests.test_dashboard import sample_report
from src.reconciliation.dashboard_ui import render_dashboard
render_dashboard(sample_report((), ()), "First.xlsx", "Second.xlsx")
''').run(timeout=30)
    assert not at.exception


def test_main_app_history_and_review_refresh(tmp_path, monkeypatch):
    # The change bundle does not include the unchanged file extractors.
    # Stub only those imports: real history, report state, UI and database run below.
    import sys
    import types
    from src.reconciliation.history import save_run, get_reviews
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    for name in ("csv", "excel", "pdf"):
        module = types.ModuleType(f"src.extraction.{name}_extract")
        setattr(module, f"extract_{name}", lambda path: None)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("RECONCILIATION_DB_PATH", str(tmp_path / "history.db"))
    report = sample_report((200000,), ())
    run_id = save_run(report, "First.xlsx", "Second.xlsx", b"test workbook placeholder")
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
    assert not at.exception
    at.button(key="history_open_sidebar").click().run(timeout=30)
    assert not at.exception
    assert any(m.label == "High-priority items still open" and m.value == "1" for m in at.metric)
    at.selectbox(key=f"status_{run_id}_R-001").select("RESOLVED")
    at.text_input(key=f"reviewer_{run_id}_R-001").input("Test reviewer")
    at.text_area(key=f"notes_{run_id}_R-001").input("Evidence checked")
    next(b for b in at.button if b.label == "Save review decision").click().run(timeout=30)
    assert not at.exception
    assert get_reviews(run_id).iloc[0]["Review status"] == "RESOLVED"
    assert any(m.label == "High-priority items still open" and m.value == "0" for m in at.metric)
    assert any(m.label == "Required adjustment (2 − 1)" and m.value == "PKR 200,000.00" for m in at.metric)
