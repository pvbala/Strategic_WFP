import streamlit as st

import database as db
from common import bootstrap, fmt_date

sys_cfg, plan, L = bootstrap("Application Configuration")

st.header("Application configuration")
st.caption("Name the Workforce Plan and set the window Start Date must fall within across "
           "Functions 1 and 3. Once a WFP has been generated for a plan (Function 4), that "
           "plan's details lock — but a new plan can then be created to continue working.")

all_plans = db.get_all_plans()

# --- Plan switcher: choose which plan is active, whether or not it's locked ---
if len(all_plans) > 1:
    st.subheader("Select active plan")
    st.caption("Choose which Workforce Plan Functions 1–4 currently operate against.")

    def _plan_label(p):
        status = "Locked" if p.locked else "Unlocked"
        tag = " (currently active)" if p.is_active else ""
        return f"{p.plan_name} — {status}{tag}"

    current_index = next((i for i, p in enumerate(all_plans) if p.is_active), 0)
    selected_plan = st.selectbox(
        "Workforce Plan", all_plans, index=current_index, format_func=_plan_label, key="plan_switcher",
    )
    if plan is None or selected_plan.id != plan.id:
        if st.button("Switch to this plan", type="primary"):
            db.set_active_plan(selected_plan.id)
            st.success(f"Switched active plan to '{selected_plan.plan_name}'.")
            st.rerun()
    st.divider()

if plan is None:
    st.info("No plan has been created yet. Create the first Workforce Plan below.")
    st.subheader("New Workforce Plan")
    plan_name = st.text_input("Workforce Plan name", key="new_plan_name")
    d1, d2 = st.columns(2)
    plan_start = d1.date_input("Plan start date", value=None, format="YYYY-MM-DD", key="new_plan_start")
    plan_end = d2.date_input("Plan end date", value=None, format="YYYY-MM-DD", key="new_plan_end")
    if st.button("Create plan", type="primary"):
        if not plan_name.strip():
            st.error("Enter a Workforce Plan name.")
        elif plan_start and plan_end and plan_start > plan_end:
            st.error("Start date must be on or before end date.")
        else:
            db.create_new_plan(plan_name.strip(), plan_start, plan_end)
            st.success(f"Plan '{plan_name}' created and set as active.")
            st.rerun()

elif not plan.locked:
    st.subheader(f"Active plan: {plan.plan_name}")
    plan_name = st.text_input("Workforce Plan name", value=plan.plan_name)
    d1, d2 = st.columns(2)
    plan_start = d1.date_input("Plan start date", value=plan.start_date, format="YYYY-MM-DD")
    plan_end = d2.date_input("Plan end date", value=plan.end_date, format="YYYY-MM-DD")

    if st.button("Save plan details", type="primary"):
        if plan_start and plan_end and plan_start > plan_end:
            st.error("Start date must be on or before end date.")
        else:
            db.update_active_plan(plan_name, plan_start, plan_end)
            st.success("Plan details saved.")
            st.rerun()

else:
    st.success(f"**{plan.plan_name}** is locked — a WFP has already been generated for it.")
    st.write(f"- Start date: **{fmt_date(plan.start_date)}**")
    st.write(f"- End date: **{fmt_date(plan.end_date)}**")

    st.divider()
    st.subheader("Create a new Workforce Plan")
    st.caption("This starts a fresh plan and makes it the active one for Functions 1, 2, 3, and 4. "
               f"'{plan.plan_name}' and its data remain in the system for reference.")
    new_plan_name = st.text_input("New Workforce Plan name", key="new_plan_name_2")
    d1, d2 = st.columns(2)
    new_plan_start = d1.date_input("Plan start date", value=None, format="YYYY-MM-DD", key="new_plan_start_2")
    new_plan_end = d2.date_input("Plan end date", value=None, format="YYYY-MM-DD", key="new_plan_end_2")
    if st.button("Create new plan", type="primary"):
        if not new_plan_name.strip():
            st.error("Enter a Workforce Plan name.")
        elif new_plan_start and new_plan_end and new_plan_start > new_plan_end:
            st.error("Start date must be on or before end date.")
        else:
            db.create_new_plan(new_plan_name.strip(), new_plan_start, new_plan_end)
            st.success(f"Plan '{new_plan_name}' created and set as active.")
            st.rerun()

if len(all_plans) > 1 or (len(all_plans) == 1 and plan is not None):
    st.divider()
    st.subheader("Plan history")
    for p in all_plans:
        status = []
        status.append("Active" if p.is_active else "Inactive")
        status.append("Locked" if p.locked else "Unlocked")
        window = f"{fmt_date(p.start_date)} to {fmt_date(p.end_date)}" if p.start_date else "No window set"
        st.write(f"- **{p.plan_name}** — {window} — {' · '.join(status)}")
