"""Simple Streamlit UI for Khwaja Dairy Farm ledger reconciliation."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import streamlit as st

from src.extraction.csv_extract import extract_csv
from src.extraction.excel_extract import extract_excel
from src.extraction.pdf_extract import extract_pdf
from src.reconciliation.matcher import ReconciliationSettings
from src.reconciliation.detailed_reconciliation import reconcile_detailed
from src.reconciliation.history import get_reviews, list_runs, load_run, save_review, save_run
from src.reconciliation.report import make_detailed_excel_report
from src.reconciliation.dashboard import reviewed_differences
from src.reconciliation.dashboard_ui import render_dashboard


st.set_page_config(
    page_title="Khwaja Dairy Farm | Ledger Reconciliation",
    page_icon="🐄",
    layout="wide",
)

st.title("Khwaja Dairy Farm — Ledger Reconciliation")
st.caption(
    "Upload two ledgers to create a detailed reconciliation report for review "
    "with source references, differences, and an action list."
)

if "reconciliation_report" not in st.session_state:
    st.session_state.reconciliation_report = None


def clear_report() -> None:
    st.session_state.reconciliation_report = None


def extract_upload(upload, folder: Path):
    suffix = Path(upload.name).suffix.lower()
    path = folder / upload.name
    path.write_bytes(upload.getvalue())

    if suffix == ".csv":
        return extract_csv(path)

    if suffix in {".xlsx", ".xls"}:
        return extract_excel(path)

    if suffix == ".pdf":
        return extract_pdf(path)

    raise ValueError("Unsupported file format.")


def render_history(key: str) -> None:
    """History must also be reachable on a fresh session before reconciling."""
    try:
        history = list_runs()
    except Exception:
        st.warning("Saved history is unavailable. Check the database path and permissions.")
        return
    if history.empty:
        st.info("No saved reconciliations yet.")
        return
    st.dataframe(history, use_container_width=True, hide_index=True)
    labels = {
        int(row["Run ID"]): f"Run {row['Run ID']} · {row['First ledger']} vs {row['Second ledger']} · {row['Created (UTC)']}"
        for _, row in history.iterrows()
    }
    selected = st.selectbox("Choose saved reconciliation", list(labels), format_func=labels.get, key=f"history_select_{key}")
    if st.button("Open saved reconciliation", key=f"history_open_{key}"):
        loaded = load_run(int(selected))
        if loaded is not None:
            st.session_state.reconciliation_report = loaded
            st.rerun()


with st.sidebar:
    st.subheader("Saved reconciliations")
    st.caption("Open previous results without uploading or matching again.")
    render_history("sidebar")
    st.caption("History is shared within this local app. User login and per-client access control are not implemented.")


with st.expander("Upload ledgers / start a new reconciliation", expanded=st.session_state.reconciliation_report is None):
    first, second = st.columns(2)

    with first:
        first_upload = st.file_uploader(
            "Source table 1",
            type=["csv", "xlsx", "xls", "pdf"],
            key="first",
        )

    with second:
        second_upload = st.file_uploader(
            "Source table 2",
            type=["csv", "xlsx", "xls", "pdf"],
            key="second",
        )

    with st.container():
        st.caption("Matching settings")
        amount_tolerance = st.number_input(
            "Amount tolerance",
            min_value=0.0,
            value=1.0,
            step=0.5,
        )

        date_tolerance = st.number_input(
            "Date tolerance (days)",
            min_value=0,
            value=3,
            step=1,
        )

    if st.button("Reconcile ledgers", type="primary", use_container_width=True):
        if not first_upload or not second_upload:
            st.error("Please upload both ledgers.")

        else:
            try:
                progress_bar = st.progress(0, text="Starting reconciliation")
                with st.status("Reconciling ledgers", expanded=True) as status:
                    with TemporaryDirectory() as directory:
                        status.write("Extracting the first ledger")
                        progress_bar.progress(15, text="Extracting the first ledger")
                        left = extract_upload(first_upload, Path(directory))
                        status.write("Extracting the second ledger")
                        progress_bar.progress(30, text="Extracting the second ledger")
                        right = extract_upload(second_upload, Path(directory))

                        def update_progress(message: str) -> None:
                            steps = {
                                "Grouping the first ledger": 45,
                                "Grouping the second ledger": 55,
                                "Finding likely transaction counterparts": 70,
                                "Selecting one-to-one matches": 82,
                                "Preparing audit tables": 90,
                            }
                            status.write(message)
                            progress_bar.progress(steps.get(message, 90), text=message)

                        detailed = reconcile_detailed(
                            left,
                            right,
                            ReconciliationSettings(
                                amount_tolerance=amount_tolerance,
                                date_tolerance_days=date_tolerance,
                            ),
                            progress=update_progress,
                        )
                        status.write("Creating the Excel report")
                        progress_bar.progress(96, text="Creating the Excel report")
                        excel_report = make_detailed_excel_report(detailed, first_upload.name, second_upload.name)
                    status.update(label="Reconciliation complete", state="complete", expanded=False)
                progress_bar.progress(100, text="Reconciliation complete")

                detected = []
                for position, source in (("First", left), ("Second", right)):
                    detected_format = source.attrs.get("detected_format")
                    if detected_format:
                        detected.append(f"{position} PDF: {detected_format.replace('_', ' ').title()}")

                run_id = save_run(detailed, first_upload.name, second_upload.name, excel_report)
                st.session_state.reconciliation_report = {
                    "run_id": run_id,
                    "detailed": detailed,
                    "first_name": first_upload.name,
                    "second_name": second_upload.name,
                    "left_rows": len(left),
                    "right_rows": len(right),
                    "detected": detected,
                    "excel_report": excel_report,
                }

            except ValueError as exc:
                st.error("We could not read one of the source tables. Please check that it contains a date and an amount (or debit/credit) column.")
                with st.expander("Technical details"):
                    st.code(str(exc))
            except Exception as exc:
                st.error("Reconciliation failed. Technical details:")
                st.exception(exc)


saved_report = st.session_state.reconciliation_report
if saved_report:
    if saved_report["detected"]:
        st.info(" | ".join(saved_report["detected"]))

    source_columns = st.columns([5, 1])
    with source_columns[0]:
        st.caption("Viewing saved results. Dashboard figures describe this run, not any newly selected uploads.")
    with source_columns[1]:
        st.button("Clear report", use_container_width=True, on_click=clear_report)

    detailed = saved_report["detailed"]
    tabs = st.tabs([
        "Dashboard",
        "Summary",
        "Difference Register",
        "Action List",
        "Manual Review",
        "Matched Comparison",
        "Non-Value Differences",
        "History",
    ])
    run_id = saved_report.get("run_id")
    review_error = False
    try:
        reviews = get_reviews(run_id) if run_id is not None else pd.DataFrame()
    except Exception:
        reviews = pd.DataFrame()
        review_error = True
    with tabs[0]:
        if st.session_state.pop("review_saved_message", False):
            st.success("Review decision saved. Dashboard counts are updated.")
        render_dashboard(
            detailed, saved_report["first_name"], saved_report["second_name"],
            reviews=reviews, run_key=run_id or "current", review_error=review_error,
        )

    tab_data = [
        detailed.summary,
        reviewed_differences(detailed.difference_register, reviews),
        detailed.action_list,
        detailed.matched_comparison,
        detailed.non_value_differences,
    ]
    for tab, table in zip((tabs[1], tabs[2], tabs[3], tabs[5], tabs[6]), tab_data):
        with tab:
            st.caption(f"Comparing `{saved_report['first_name']}` against `{saved_report['second_name']}`")
            st.dataframe(table, use_container_width=True, hide_index=True)

    with tabs[4]:
        st.caption("Record a human decision for an exception. These notes stay attached to this saved reconciliation run.")
        reviewable = detailed.difference_register
        if reviewable.empty:
            st.success("There are no value differences to review.")
        elif review_error:
            st.warning("Review decisions could not be loaded. Restore database access before editing them.")
        elif saved_report.get("run_id") is None:
            st.info("Run a new reconciliation to save review decisions.")
        else:
            selected_ref = st.selectbox("Reconciliation reference", reviewable["Ref"].tolist(), key=f"review_ref_{run_id}")
            selected = reviewable.loc[reviewable["Ref"].eq(selected_ref)].iloc[0]
            st.write(f"**{selected['Subject']}** — difference: PKR {selected['Difference (right - left)']:,.2f}")
            existing = reviews
            existing_row = existing.loc[existing["Ref"].eq(selected_ref)] if not existing.empty else pd.DataFrame()
            current_status = existing_row["Review status"].iloc[0] if not existing_row.empty else "OPEN"
            reviewer = existing_row["Reviewer"].iloc[0] if not existing_row.empty else ""
            notes = existing_row["Notes"].iloc[0] if not existing_row.empty else ""
            review_status = st.selectbox("Review status", ["OPEN", "UNDER REVIEW", "RESOLVED", "WAIVED"], index=["OPEN", "UNDER REVIEW", "RESOLVED", "WAIVED"].index(current_status) if current_status in {"OPEN", "UNDER REVIEW", "RESOLVED", "WAIVED"} else 0, key=f"status_{run_id}_{selected_ref}")
            reviewer = st.text_input("Reviewer", value=reviewer, key=f"reviewer_{run_id}_{selected_ref}")
            notes = st.text_area("Review notes / evidence", value=notes, key=f"notes_{run_id}_{selected_ref}")
            if st.button("Save review decision", type="primary"):
                save_review(saved_report["run_id"], selected_ref, review_status, reviewer, notes)
                st.session_state.review_saved_message = True
                st.rerun()

    with tabs[7]:
        st.caption("Saved runs remain available as long as the database file is preserved.")
        render_history("tab")

    with st.expander("Source ledger rows and reconciliation references"):
        source_tabs = st.tabs([saved_report["first_name"], saved_report["second_name"]])
        with source_tabs[0]:
            st.dataframe(detailed.left_source, use_container_width=True, hide_index=True)
        with source_tabs[1]:
            st.dataframe(detailed.right_source, use_container_width=True, hide_index=True)

    st.caption("Excel contains the original reconciliation; dashboard review decisions are stored separately and do not rewrite its values.")
    st.download_button(
        "Download detailed Excel report",
        saved_report["excel_report"],
        "detailed_ledger_reconciliation.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
