"""Simple Streamlit UI for Khwaja Dairy Farm ledger reconciliation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import streamlit as st

from src.extraction.dispatch import extract_path
from src.extraction.schema import normalise_transactions
from src.reconciliation.matcher import ReconciliationSettings
from src.reconciliation.detailed_reconciliation import reconcile_detailed
from src.reconciliation.history import get_reviews, list_runs, load_run, save_review, save_run
from src.reconciliation.report import make_detailed_excel_report
from src.reconciliation.dashboard import reviewed_differences
from src.reconciliation.dashboard_ui import render_dashboard
from src.reconciliation.signs import SIGN_OPTIONS, align_signs


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
if "extracted_ledgers" not in st.session_state:
    st.session_state.extracted_ledgers = None


def clear_report() -> None:
    st.session_state.reconciliation_report = None


def extract_upload(upload, folder: Path):
    path = folder / upload.name
    path.write_bytes(upload.getvalue())
    return extract_path(path)


def upload_signature(upload) -> str:
    """Identify the exact uploaded bytes so stale previews are never reused."""
    digest = hashlib.sha256(upload.getvalue()).hexdigest()
    return f"{upload.name}:{digest}"


def approved_transactions(edited: pd.DataFrame, original: pd.DataFrame, source_name: str) -> pd.DataFrame:
    """Validate an edited extraction preview before it reaches matching."""
    required = edited.reindex(columns=["date", "reference", "description", "amount"])
    result = normalise_transactions(required, source_name)
    result.attrs = dict(original.attrs)
    if result.empty:
        raise ValueError(f"{source_name} has no approved transaction rows.")
    return result


def extraction_label(position: str, frame: pd.DataFrame) -> str:
    layout = str(frame.attrs.get("detected_format", "structured table")).replace("_", " ").title()
    method = str(frame.attrs.get("extraction_method", "structured file"))
    return f"{position}: {layout} via {method}"


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
    supported_types = ["csv", "xlsx", "xls", "pdf", "png", "jpg", "jpeg"]

    with first:
        first_upload = st.file_uploader("Source table 1", type=supported_types, key="first")
    with second:
        second_upload = st.file_uploader("Source table 2", type=supported_types, key="second")

    st.caption("Each side may use a different format. Images and scanned PDFs are OCR-checked before reconciliation.")
    setting_columns = st.columns(3)
    with setting_columns[0]:
        amount_tolerance = st.number_input("Amount tolerance", min_value=0.0, value=1.0, step=0.5)
    with setting_columns[1]:
        date_tolerance = st.number_input("Date tolerance (days)", min_value=0, value=3, step=1)
    with setting_columns[2]:
        sign_mode = st.selectbox(
            "Counterparty sign handling",
            SIGN_OPTIONS,
            help="Auto mode reverses the second ledger only when at least two matching absolute values provide stronger opposite-sign evidence.",
        )

    current_signature = None
    if first_upload and second_upload:
        current_signature = (upload_signature(first_upload), upload_signature(second_upload))

    if st.button("Extract ledgers for review", type="primary", use_container_width=True):
        if not first_upload or not second_upload:
            st.error("Please upload both ledgers.")
        else:
            try:
                with st.status("Extracting ledger rows", expanded=True) as extraction_status:
                    with TemporaryDirectory() as directory:
                        extraction_status.write("Reading the first ledger")
                        extracted_left = extract_upload(first_upload, Path(directory))
                        extraction_status.write("Reading the second ledger")
                        extracted_right = extract_upload(second_upload, Path(directory))
                    extraction_status.update(label="Extraction ready for review", state="complete", expanded=False)
                st.session_state.extracted_ledgers = {
                    "signature": current_signature,
                    "left": extracted_left,
                    "right": extracted_right,
                    "first_name": first_upload.name,
                    "second_name": second_upload.name,
                }
            except ValueError as exc:
                st.session_state.extracted_ledgers = None
                st.error("We could not extract one of the ledgers safely. No reconciliation was run.")
                with st.expander("Technical details"):
                    st.code(str(exc))
            except Exception as exc:
                st.session_state.extracted_ledgers = None
                st.error("Ledger extraction failed. Technical details:")
                st.exception(exc)

    pending = st.session_state.extracted_ledgers
    if pending and current_signature == pending.get("signature"):
        st.divider()
        st.subheader("Review extracted transactions")
        st.info(
            f"{extraction_label('First', pending['left'])} | "
            f"{extraction_label('Second', pending['right'])}"
        )

        for source_name, frame in ((pending["first_name"], pending["left"]), (pending["second_name"], pending["right"])):
            warnings = frame.attrs.get("extraction_warnings", [])
            if frame.attrs.get("review_required") and not warnings:
                st.warning(f"{source_name}: OCR was used. Compare the editable rows with the source before approval.")
            for warning in warnings:
                st.warning(f"{source_name}: {warning}")

        preview_columns = ["date", "reference", "description", "amount"]
        review_columns = st.columns(2)
        editor_key = hashlib.sha256("|".join(current_signature).encode()).hexdigest()[:12]
        with review_columns[0]:
            st.markdown(f"**{pending['first_name']}** — {len(pending['left'])} rows")
            edited_left = st.data_editor(
                pending["left"][preview_columns],
                num_rows="dynamic",
                use_container_width=True,
                key=f"left_review_{editor_key}",
            )
            evidence = pending["left"].attrs.get("review_rows")
            if evidence:
                with st.expander("Show first-ledger OCR/debit-credit evidence"):
                    st.dataframe(pd.DataFrame(evidence), use_container_width=True, hide_index=True)
        with review_columns[1]:
            st.markdown(f"**{pending['second_name']}** — {len(pending['right'])} rows")
            edited_right = st.data_editor(
                pending["right"][preview_columns],
                num_rows="dynamic",
                use_container_width=True,
                key=f"right_review_{editor_key}",
            )
            evidence = pending["right"].attrs.get("review_rows")
            if evidence:
                with st.expander("Show second-ledger OCR/debit-credit evidence"):
                    st.dataframe(pd.DataFrame(evidence), use_container_width=True, hide_index=True)

        if st.button("Approve tables and reconcile", type="primary", use_container_width=True):
            try:
                approved_left = approved_transactions(edited_left, pending["left"], pending["first_name"])
                approved_right = approved_transactions(edited_right, pending["right"], pending["second_name"])
                left, right, sign_note = align_signs(
                    approved_left,
                    approved_right,
                    sign_mode,
                    tolerance=amount_tolerance,
                )

                progress_bar = st.progress(0, text="Starting reconciliation")
                with st.status("Reconciling approved rows", expanded=True) as status:
                    def update_progress(message: str) -> None:
                        steps = {
                            "Grouping the first ledger": 20,
                            "Grouping the second ledger": 35,
                            "Finding likely transaction counterparts": 60,
                            "Selecting one-to-one matches": 78,
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
                    first_report_name = pending["first_name"] + (" [signs reversed]" if sign_note.startswith("First") else "")
                    second_report_name = pending["second_name"] + (" [signs reversed]" if sign_note.startswith("Second") else "")
                    excel_report = make_detailed_excel_report(detailed, first_report_name, second_report_name)
                    status.update(label="Reconciliation complete", state="complete", expanded=False)
                progress_bar.progress(100, text="Reconciliation complete")

                detected = [
                    extraction_label("First", approved_left),
                    extraction_label("Second", approved_right),
                    sign_note,
                ]
                run_id = save_run(detailed, pending["first_name"], pending["second_name"], excel_report)
                st.session_state.reconciliation_report = {
                    "run_id": run_id,
                    "detailed": detailed,
                    "first_name": pending["first_name"],
                    "second_name": pending["second_name"],
                    "left_rows": len(left),
                    "right_rows": len(right),
                    "detected": detected,
                    "excel_report": excel_report,
                }
                st.rerun()
            except ValueError as exc:
                st.error("The reviewed rows could not be reconciled safely.")
                with st.expander("Technical details"):
                    st.code(str(exc))
            except Exception as exc:
                st.error("Reconciliation failed. Technical details:")
                st.exception(exc)
    elif pending and current_signature != pending.get("signature"):
        st.info("The selected files changed. Extract them again before reconciliation.")


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
