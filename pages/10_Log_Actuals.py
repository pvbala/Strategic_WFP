import streamlit as st

import alerts_db as adb
import database as db
import master_data as md
from common import bootstrap, fmt_date, plan_banner

sys_cfg, plan, L = bootstrap("Log Actuals")

st.header("Log Actuals")
plan_banner(plan)
st.caption(
    "Enter what actually happened for a metric that was planned in Function 1/2/3 — this "
    "is the only place actual outcomes get captured, and it's what the Estimation Basis "
    "insights (attrition, EP, onsite, etc.) on Customer Insights are computed from."
)

METRIC_TO_ROW_FIELD = {
    "Attrition": "attrition",
    "Rampdown": "rampdown",
    "Release to Other Accounts": "release_to_other_accounts",
    "EP": "ep",
    "BA": "ba",
    "Internal": "internal",
    "Trainee": "trainee",
}

if plan is None:
    st.warning("No active Workforce Plan. Set one up in Application Configuration first.")
else:
    customers = db.customers_with_demand(plan.id)
    if not customers:
        st.info("No demand has been entered yet for this plan. Use Function 1 first.")
    else:
        customer = st.selectbox(L["customer"], [""] + customers, key="f10_customer")
        if customer:
            locations = db.locations_with_demand(plan.id, customer)
            location = st.selectbox(L["location"], [""] + locations, key="f10_location")
            if location:
                dates = db.start_dates_with_demand(plan.id, customer, location)
                start_date = st.selectbox(
                    L["start_date"], [""] + dates, format_func=lambda d: fmt_date(d) if d else "", key="f10_date"
                )
                if start_date:
                    rows = db.demand_rows_for(plan.id, customer, location, start_date)
                    if not rows:
                        st.error("No rows exist for this key.")
                    else:
                        st.caption(
                            "Select which row and which metric to log an actual for. "
                            "Onsite Demand is tracked separately below, based on the Onsite flag "
                            "in master_data.xlsx for this Location."
                        )
                        for row in rows:
                            label = row.service_line or row.primary_technology
                            with st.container(border=True):
                                st.write(f"**{label}**  ·  Row Type: {row.row_type}")
                                m1, m2, m3 = st.columns(3)
                                metric = m1.selectbox(
                                    "Metric", list(METRIC_TO_ROW_FIELD.keys()), key=f"f10_metric_{row.id}"
                                )
                                planned_value = getattr(row, METRIC_TO_ROW_FIELD[metric])
                                m2.text_input("Planned Value", value=str(planned_value), disabled=True, key=f"f10_planned_{row.id}")
                                actual_value = m3.number_input(
                                    "Actual Value", min_value=0, value=planned_value, step=1, key=f"f10_actual_{row.id}"
                                )
                                logged_by = st.text_input("Logged By", key=f"f10_logged_by_{row.id}")
                                if st.button("Save Actual", key=f"f10_save_{row.id}"):
                                    adb.add_actual_outcome(
                                        linked_wfp_row_id=row.id,
                                        customer=customer,
                                        primary_technology=row.primary_technology,
                                        location=location,
                                        metric=metric,
                                        planned_value=int(planned_value),
                                        actual_value=int(actual_value),
                                        logged_by=logged_by or None,
                                    )
                                    st.success(f"Actual {metric} logged for {label}.")
                                    st.rerun()

        st.divider()
        st.subheader("Onsite Demand — actuals")
        st.caption(
            "Onsite Demand isn't a per-row field — it's derived from Demand Count on rows "
            "whose Location is flagged Onsite in master_data.xlsx. Enter the actual total "
            "onsite demand realized for a given Customer + Start Date."
        )
        if customer:
            onsite_planned = sum(
                r.demand_count for r in db.all_rows(plan.id)
                if r.customer == customer and r.row_type == db.ROW_TYPE_DEMAND and md.is_onsite(r.location)
            )
            oc1, oc2 = st.columns(2)
            oc1.text_input("Planned Onsite Demand (this plan, this customer)", value=str(onsite_planned), disabled=True)
            actual_onsite = oc2.number_input("Actual Onsite Demand", min_value=0, value=onsite_planned, step=1, key="f10_onsite_actual")
            logged_by_onsite = st.text_input("Logged By", key="f10_onsite_logged_by")
            if st.button("Save Onsite Actual", disabled=onsite_planned == 0):
                adb.add_actual_outcome(
                    linked_wfp_row_id=None,
                    customer=customer,
                    primary_technology=None,
                    location=None,
                    metric="Onsite Demand",
                    planned_value=int(onsite_planned),
                    actual_value=int(actual_onsite),
                    logged_by=logged_by_onsite or None,
                )
                st.success(f"Onsite Demand actual logged for {customer}.")
                st.rerun()
            if onsite_planned == 0:
                st.caption("No onsite demand planned for this customer in this plan — nothing to compare against.")
