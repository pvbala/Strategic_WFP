import streamlit as st

import alert_engine as ae
import alerts_db as adb
import master_data as md
from common import bootstrap, fmt_date, plan_banner

sys_cfg, plan, L = bootstrap("Customer Insights")

st.header("Customer Insights")
plan_banner(plan)
st.caption(
    "Read-only view of everything the contextual alerts are built from: customer behavior "
    "history, estimation bias by metric, and the opportunity pipeline."
)

tab_behavior, tab_bias, tab_pipeline = st.tabs(
    ["Customer Behavior", "Estimation Bias", "Pipeline Summary"]
)

# ---------------------------------------------------------------- Tab 1: Customer Behavior
with tab_behavior:
    st.subheader("Customer behavior profile")
    customers = adb.distinct_event_customers()
    if not customers:
        st.info("No demand events logged yet. Use Log a Demand Event to start building history.")
    else:
        customer = st.selectbox(L["customer"], customers, key="f11_customer")
        events = adb.get_events_for_customer(customer)

        techs = sorted({e.primary_technology for e in events if e.primary_technology})
        alerts = ae.get_customer_behavior_alerts(customer, primary_technology=techs[0] if techs else None)

        c1, c2, c3 = st.columns(3)
        c1.metric("Total events logged", len(events))
        adverse = sum(1 for e in events if e.event_type in adb.ADVERSE_EVENT_TYPES)
        c2.metric("Postponed / Reduced / Cancelled", adverse)
        c3.metric(
            "Adverse rate",
            f"{round(adverse / len(events) * 100)}%" if events else "—",
        )

        st.caption(
            f"Alert threshold: fires at ≥{round(ae.ADVERSE_RATE_THRESHOLD*100)}% adverse rate "
            f"with at least {ae.MIN_SAMPLE_SIZE} logged events."
        )

        if alerts:
            st.warning(f"{len(alerts)} alert(s) would currently trigger for this customer.")
            for a in alerts:
                st.write(f"- **{a['headline']}** ({a['risk_level']}, {a['confidence']} confidence)")
        else:
            st.success("No alert currently triggers for this customer, based on logged history.")

        st.divider()
        st.write("**Event history**")
        rows = [{
            "Date": e.event_date.strftime("%d-%b-%y"),
            "Type": e.event_type,
            "Technology": e.primary_technology or "",
            "Location": e.location or "",
            "Shift (days)": str(e.lead_time_days) if e.lead_time_days is not None else "",
            "Qty Δ": str(e.qty_delta) if e.qty_delta is not None else "",
            "Reason": e.reason_category or "",
        } for e in events]
        st.dataframe(rows, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------- Tab 2: Estimation Bias
with tab_bias:
    st.subheader("Estimation bias by metric")
    st.caption(
        "Average variance between planned and actual values, across everything logged in "
        "Log Actuals. Negative variance = the team tends to over-estimate this metric; "
        "positive = under-estimate."
    )

    scope = st.radio("Scope", ["All customers", "Single customer"], horizontal=True, key="f11_bias_scope")
    scope_customer = None
    if scope == "Single customer":
        actuals = adb.get_all_actuals()
        cust_options = sorted({r.customer for r in actuals})
        if cust_options:
            scope_customer = st.selectbox(L["customer"], cust_options, key="f11_bias_customer")

    bias_rows = []
    for metric in adb.ESTIMATION_METRICS:
        result = ae.compute_estimation_bias(metric, customer=scope_customer)
        if result:
            bias_rows.append({
                "Metric": metric,
                "Sample Size": result["sample_size"],
                "Avg Variance %": result["avg_variance_pct"],
                "Direction": result["direction"],
            })

    if not bias_rows:
        st.info("No actuals logged yet. Use Log Actuals to start building this view.")
    else:
        st.dataframe(bias_rows, use_container_width=True, hide_index=True)
        flagged = [r for r in bias_rows if abs(r["Avg Variance %"]) >= 15]
        if flagged:
            st.warning(
                "Metrics with notable bias (≥15% average variance): "
                + ", ".join(f"{r['Metric']} ({r['Direction']}, {r['Avg Variance %']}%)" for r in flagged)
            )

# ---------------------------------------------------------------- Tab 3: Pipeline Summary
with tab_pipeline:
    st.subheader("Opportunity pipeline summary")
    opps = adb.get_all_opportunities()
    if not opps:
        st.info("No opportunities logged yet. Use Opportunity Pipeline to start tracking.")
    else:
        by_stage = {}
        for o in opps:
            by_stage.setdefault(o.stage, {"count": 0, "weighted_qty": 0})
            by_stage[o.stage]["count"] += 1
            if o.estimated_qty and o.win_probability is not None:
                by_stage[o.stage]["weighted_qty"] += o.estimated_qty * o.win_probability / 100

        summary_rows = [
            {"Stage": stage, "Count": v["count"], "Weighted Qty": round(v["weighted_qty"], 1)}
            for stage, v in by_stage.items()
        ]
        st.dataframe(summary_rows, use_container_width=True, hide_index=True)

        suspecting_count = by_stage.get("Suspecting", {}).get("count", 0)
        if suspecting_count:
            st.caption(
                f"{suspecting_count} opportunity(ies) are still in Suspecting stage with no "
                "probability — these aren't included in the weighted total. Surfacing patterns "
                "across informal, pre-probability signals like these needs the semantic/RAG "
                "layer, which is a planned future addition."
            )
