"""Client-facing Streamlit dashboard. Calculation rules live in dashboard.py."""
from __future__ import annotations

from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from .dashboard import DIFFERENCE, PRIORITIES, STATUSES, build_dashboard, money


def _card(column, label, value, explanation):
    with column:
        st.metric(label, value, help=explanation)
        st.caption(explanation)


def _bars(frame, label, value, title, color="#168C83", order=None, currency=False):
    """Accessible horizontal bars, zero baseline, explicit units and hover detail."""
    spec = {
        "height": max(120, 36 * len(frame)),
        "mark": {"type": "bar", "color": color, "cornerRadiusEnd": 4},
        "encoding": {
            "y": {"field": label, "type": "nominal", "sort": order or "-x",
                  "axis": {"title": None, "labelLimit": 260, "labelFontSize": 12}},
            "x": {"field": value, "type": "quantitative", "scale": {"zero": True},
                  "axis": {"title": title, "format": ",.0f", "tickMinStep": 1}},
            "tooltip": [{"field": label, "type": "nominal"},
                        {"field": value, "type": "quantitative", "format": ",.2f" if currency else ",.0f"}],
        },
        "config": {"view": {"stroke": None}},
    }
    st.vega_lite_chart(frame, spec, use_container_width=True)


def render_dashboard(detailed, first_name, second_name, reviews=None, run_key="current", review_error=False):
    snapshot = build_dashboard(detailed, reviews)
    m, differences = snapshot.metrics, snapshot.differences
    first, second = Path(first_name).stem, Path(second_name).stem
    if first == second:
        first, second = f"{first} (table 1)", f"{second} (table 2)"
    st.markdown("""<style>
        .recon-hero {background:#102d3b;color:#fff;border-radius:16px;padding:24px 28px;margin:8px 0 22px;}
        .recon-kicker {font-size:12px;letter-spacing:1.7px;text-transform:uppercase;color:#a0ced0;}
        .recon-hero h2 {color:#fff!important;font-size:clamp(23px,3vw,34px);margin:8px 0;line-height:1.25;}
        .recon-hero p {color:#e2ebf0;margin:9px 0 0;font-size:16px;}
        .recon-pill {display:inline-block;border:1px solid #89b5bf;border-radius:30px;padding:4px 12px;margin-top:12px;color:#fff;font-size:13px;}
        [data-testid="stMetric"] {border:1px solid rgba(128,128,128,.25);border-radius:12px;padding:14px;}
        [data-testid="stMetricValue"] {font-size:clamp(20px,2.15vw,32px);}
        </style>""", unsafe_allow_html=True)
    st.caption(f"RECONCILIATION OVERVIEW · {first_name} vs {second_name}")
    if m["source_rows"] == 0:
        headline, direction = "No transactions to compare", "Upload two ledgers to begin."
    elif abs(m["gap"]) < .005:
        headline = "Books have the same extracted total"
        direction = "The net gap is zero. Individual differences may still cancel each other out."
    else:
        headline = f"Books differ by {money(abs(m['gap']))}"
        direction = f"{second} is {money(abs(m['gap']))} {'lower' if m['gap'] < 0 else 'higher'} than {first}."
    pill = "Review status unavailable" if review_error else snapshot.health
    st.markdown(
        f'<section class="recon-hero"><div class="recon-kicker">Your reconciliation in 10 seconds</div>'
        f'<h2>{escape(headline)}</h2><p>{escape(direction)}</p>'
        f'<span class="recon-pill">{escape(pill)}</span><p>{escape(snapshot.health_reason)}</p></section>',
        unsafe_allow_html=True,
    )
    if review_error:
        st.warning("Saved review decisions could not be loaded. All differences are shown as OPEN for safety; review totals may be overstated.")
    st.caption("Based on extracted transactions, not independently verified closing balances. Confirm opening balances, date coverage and sign conventions. The gap is not a payment instruction.")

    st.subheader("1 · Where do the two books stand?")
    cards = st.columns(3)
    _card(cards[0], "First ledger balance*", money(m["first_balance"]), f"{first}: sum of extracted signed amounts.")
    _card(cards[1], "Second ledger balance*", money(m["second_balance"]), f"{second}: sum of extracted signed amounts.")
    _card(cards[2], "Required adjustment (2 − 1)", money(m["gap"]), "Signed gap to bridge the first total to the second; not an approved journal entry.")
    with st.expander("How is this difference calculated?", expanded=True):
        st.write("**Second ledger − First ledger = Required adjustment**")
        st.code(f"({money(m['second_balance'])}) − ({money(m['first_balance'])}) = {money(m['gap'])}", language=None)
        st.write("**First ledger + Required adjustment = Second ledger**")
        st.code(f"({money(m['first_balance'])}) + ({money(m['gap'])}) = {money(m['second_balance'])}", language=None)
        st.caption("Negative adjustment = second ledger lower. Positive adjustment = second ledger higher. Who owes whom cannot be inferred without an agreed accounting direction.")

    st.subheader("2 · How much matched, and what still needs attention?")
    cards = st.columns(4)
    _card(cards[0], "Group match rate", f"{m['match_rate']:.1f}%" if m["match_rate"] is not None else "N/A",
          f"{m['paired_groups']} paired groups ÷ {m['total_groups']} total groups. A pair may still differ in value.")
    _card(cards[1], "Exact-value matches", f"{m['exact_groups']:,}", "Paired groups with zero value difference to the paisa; does not certify descriptions or dates.")
    _card(cards[2], "Accepted within tolerance", f"{m['within_tolerance']:,}", "Paired groups marked MATCH by the engine but carrying a non-zero difference.")
    _card(cards[3], "Groups without a counterpart", f"{m['unpaired_groups']:,}", "Groups appearing on only one side; each counts once in total groups.")
    if m["match_rate"] is not None:
        st.progress(m["match_rate"] / 100, text=f"{m['paired_groups']} of {m['total_groups']} groups paired · {m['paired_rows']} of {m['source_rows']} source rows in paired groups")
    cards = st.columns(3)
    _card(cards[0], "High-priority items still open", f"{m['high_open']:,}", "HIGH-priority differences whose review status is OPEN or UNDER REVIEW.")
    _card(cards[1], "Unresolved review items", f"{m['outstanding']:,}", "Value differences marked OPEN or UNDER REVIEW. Non-value checks are tracked separately.")
    _card(cards[2], "Unresolved difference value", money(m["gross_open"]), "Sum of absolute unresolved differences. A review-exposure indicator, not confirmed loss, debt or net balance.")
    st.caption(f"{m['resolved']} resolved · {m['waived']} waived · {m['control_issues']} non-value control finding(s). Changing review status does not change original ledger amounts.")

    st.subheader("3 · What should happen next?")
    queue = differences.loc[differences["Review status"].isin(["OPEN", "UNDER REVIEW"])].copy()
    if not queue.empty:
        queue["_rank"] = queue["Priority"].map({"HIGH": 0, "MEDIUM": 1, "LOW": 2}).fillna(3)
        queue = queue.sort_values(["_rank", "Absolute difference (PKR)"], ascending=[True, False], kind="stable")
        for _, row in queue.head(3).iterrows():
            with st.container(border=True):
                st.write(f"**{row['Ref']} · {row['Subject']}**")
                st.write(f"{money(row['Absolute difference (PKR)'])} · {row['Priority']} priority · {row['Review status']}")
                st.write(str(row.get("Action required", "Check supporting documents and agree the correct amount.")))
                st.caption(f"Suggested owner: {row.get('Owner', 'Unassigned')} | Evidence: {row.get('Evidence to obtain', 'Voucher or invoice')}")
        st.info("Open Manual Review, select the reference above, and record the decision with evidence. Resolved or waived does not mean an accounting entry has been posted.")
    else:
        st.info("No open value-review items. Check source completeness and obtain sign-off before settlement.")
    if m["remainder"] or m["register_remainder"] or m["source_total_mismatch"]:
        st.error("First fix the report tie-out: source totals or transaction differences are inconsistent. Do not use this report for settlement yet.")
    if m["control_issues"]:
        st.warning(f"Also review {m['control_issues']} non-value finding(s) in Non-Value Differences; these do not appear in the monetary exposure total.")

    st.subheader("4 · What is driving the differences?")
    st.caption("Charts below cover the whole saved run. Categories and owners are engine suggestions, not confirmed causes or assignments.")
    if snapshot.categories.empty:
        st.info("No value-difference categories were detected.")
    else:
        top = snapshot.categories.head(5)
        chart, table = st.columns([3, 2])
        with chart:
            st.markdown("**Top 5 causes by gross difference value**")
            _bars(top, "Category", "Gross difference (PKR)", "Gross difference (PKR)", currency=True)
        with table:
            st.dataframe(top, hide_index=True, use_container_width=True)
        other = max(0.0, m["gross_all"] - float(top["Gross difference (PKR)"].sum()))
        st.caption(f"Gross = sum of magnitudes, so positive and negative items cannot hide each other. Net = signed contribution. Remaining categories: {money(other)}. Only detected categories are shown.")
        chart, review_chart = st.columns(2)
        with chart:
            st.markdown("**Unresolved items by priority**")
            _bars(snapshot.priorities, "Priority", "Items", "Unresolved items", "#CF8540", PRIORITIES)
        with review_chart:
            st.markdown("**Review progress: open to resolved**")
            _bars(snapshot.statuses, "Status", "Items", "Value-difference items", "#4185B5", STATUSES)
            st.caption("WAIVED means accepted without further follow-up; it is not the same as RESOLVED.")

    st.subheader("5 · Which individual items matter most?")
    st.caption("Filters affect only this table, not the KPI cards or charts above. Ranked by absolute difference.")
    if differences.empty:
        st.info("No value differences to display.")
    else:
        choices = st.columns(3)
        with choices[0]:
            selected_statuses = st.multiselect("Review status filter", STATUSES, default=STATUSES, key=f"dash_status_{run_key}")
        with choices[1]:
            selected_priorities = st.multiselect("Priority filter", PRIORITIES, default=PRIORITIES, key=f"dash_priority_{run_key}")
        with choices[2]:
            categories = sorted(differences["Category"].dropna().unique())
            selected_categories = st.multiselect("Cause filter", categories, default=categories, key=f"dash_category_{run_key}")
        filtered = differences.loc[differences["Review status"].isin(selected_statuses) & differences["Priority"].isin(selected_priorities) & differences["Category"].isin(selected_categories)]
        if filtered.empty:
            st.info("No items match your filters. Select a status, priority and category to restore the list.")
        else:
            largest = filtered.nlargest(10, "Absolute difference (PKR)").copy()
            largest["Change to first ledger"] = largest[DIFFERENCE].map(lambda n: "Increase" if n > 0 else "Decrease" if n < 0 else "None")
            largest = largest.rename(columns={"Subject": "Vehicle / reference / item", "Owner": "Suggested owner", DIFFERENCE: "Signed difference (PKR)"})
            columns = ["Ref", "Vehicle / reference / item", "Absolute difference (PKR)", "Signed difference (PKR)", "Change to first ledger", "Category", "Priority", "Suggested owner", "Review status"]
            st.dataframe(largest.reindex(columns=columns), hide_index=True, use_container_width=True)
            st.caption(f"Showing {len(largest)} of {len(filtered)} filtered items. Gross filtered difference: {money(filtered['Absolute difference (PKR)'].sum())}.")

    with st.expander("Reconciliation proof and KPI definitions"):
        st.write("**Does the detail explain the whole gap?**")
        st.dataframe(pd.DataFrame({"Proof step": ["First extracted total", "Add: signed differences in the register", "Add: paired differences outside the register (including tolerance)", "Calculated second total", "Actual second extracted total", "Unexplained gap (should be zero)"],
                                   "PKR": [m["first_balance"], m["register_total"], m["tolerance_total"], m["first_balance"] + m["register_total"] + m["tolerance_total"], m["second_balance"], m["register_remainder"]]}), hide_index=True, use_container_width=True)
        if not m["remainder"] and not m["register_remainder"] and not m["source_total_mismatch"]:
            st.success("The detail ties to the extracted totals. This is an arithmetic check, not verification of the source books or evidence.")
        else:
            st.error("The detail does not tie to the totals. Investigate before relying on this result.")
        st.markdown("""
**Health rules (not an audit opinion or invented percentage score)**

- **Critical:** an inconsistent proof/source total, or at least one unresolved HIGH-priority item.
- **Needs Review:** other unresolved items, a non-zero net gap, unmatched groups, or non-value findings.
- **Good:** extracted totals agree and no such issues remain. Source completeness still needs checking.
- **No data:** no source rows or comparison groups to assess.

**Counting rules:** one paired business group counts once, even when it contains several source rows. Each unpaired group also counts once. Match rate measures pairing coverage, not correctness or approval.

**Money rules:** amounts use the signed values already extracted by the engine, displayed to two decimals. Neither net difference nor gross unresolved value establishes liability. Resolved/waived decisions do not rewrite the ledgers.
""")
