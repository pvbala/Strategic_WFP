import streamlit as st

import database as db
from common import bootstrap, fmt_date

sys_cfg, plan, L = bootstrap("Strategic Workforce Planning")

st.title("Strategic Workforce Planning")
st.write("Use the sidebar to navigate between System Configuration, Application "
         "Configuration, and Functions 1–4.")

st.divider()
st.subheader("Status")

c1, c2, c3 = st.columns(3)
if sys_cfg.locked:
    c1.metric("System configuration", "Locked")
else:
    c1.metric("System configuration", "Not set")
c2.metric("Active workforce plan", plan.plan_name if plan else "None yet")
c3.metric("Plan window", f"{fmt_date(plan.start_date)} to {fmt_date(plan.end_date)}" if plan and plan.start_date else "Not set")

if sys_cfg.locked:
    st.info("System Configuration parameters are locked and are not displayed.")

if plan is not None:
    rows = db.all_rows(plan.id)
    demand_count = sum(1 for r in rows if r.row_type == db.ROW_TYPE_DEMAND)
    rampdown_count = sum(1 for r in rows if r.row_type == db.ROW_TYPE_RAMPDOWN)
    c4, c5 = st.columns(2)
    c4.metric("Demand rows in active plan", demand_count)
    c5.metric("Rampdown rows in active plan", rampdown_count)

all_plans = db.get_all_plans()
if len(all_plans) > 1:
    st.caption(f"{len(all_plans)} Workforce Plans exist in total. See Application Configuration for the full history.")

if not sys_cfg.locked:
    st.info("Start with **System Configuration** in the sidebar.")
elif plan is None:
    st.info("Next, create a Workforce Plan in **Application Configuration**.")
elif not (plan.start_date and plan.end_date):
    st.info("Set the active plan's Start/End Date window in **Application Configuration**.")
