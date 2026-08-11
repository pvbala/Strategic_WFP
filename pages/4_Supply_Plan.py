import streamlit as st

import database as db
import master_data as md
from common import bootstrap, fmt_date, plan_banner

sys_cfg, plan, L = bootstrap("Function 2: Supply Plan")
GR_ON = sys_cfg.grade_enabled

st.header("Function 2: Create supply plan for the demand entered")
plan_banner(plan)
st.caption(
    "Only demand lines with a current Mismatch between Gross Demand and Total Supply are "
    "shown below — once a line is saved as Match, it drops out of this list."
)

if plan is None:
    st.warning("No active Workforce Plan. Set one up in Application Configuration first.")
else:
    all_demand_rows = [r for r in db.all_rows(plan.id) if r.row_type == db.ROW_TYPE_DEMAND]
    mismatch_rows = [r for r in all_demand_rows if r.status == "Mismatch"]
    mismatch_count = len(mismatch_rows)
    if mismatch_count > 0:
        st.warning(
            f"⚠️ {mismatch_count} demand line{'s' if mismatch_count != 1 else ''} need review for "
            f"Supply Plan and Demand Plan mismatches. Either correct the Supply Plan or correct the Demand Plan."
        )

    if not all_demand_rows:
        st.info("No demand has been entered yet. Use Function 1 first.")
    elif not mismatch_rows:
        st.success("No mismatches — every demand line currently has matching Total Supply.")
    else:
        customers = sorted({r.customer for r in mismatch_rows})
        customer = st.selectbox(L["customer"], [""] + customers)
        if customer:
            info = md.get_customer_info(customer)
            c1, c2 = st.columns(2)
            c1.text_input(L["requirement_iou"], value=info["iou"], disabled=True)
            c2.text_input(L["requirement_sub_iou"], value=info["sub_iou"], disabled=True)

            locations = sorted({r.location for r in mismatch_rows if r.customer == customer})
            location = st.selectbox(L["location"], [""] + locations)
            if location:
                dates = sorted({r.start_date for r in mismatch_rows
                                if r.customer == customer and r.location == location})
                start_date = st.selectbox(L["start_date"], [""] + dates, format_func=lambda d: fmt_date(d) if d else "")
                if start_date:
                    rows = [r for r in mismatch_rows if r.customer == customer
                            and r.location == location and r.start_date == start_date]
                    if not rows:
                        st.error(f"No {db.ROW_TYPE_DEMAND.lower()} exists for this key. Supply cannot be entered "
                                 "without a matching demand row.")
                    else:
                        st.caption(f"Enter {L['ep']}, {L['ba']}, {L['internal']}, and {L['trainee']} for each row below.")
                        for row in rows:
                            label = row.service_line or f"{row.primary_technology}"
                            if GR_ON and row.grade:
                                label += f" — {row.grade}"
                            with st.container(border=True):
                                st.write(f"**{label}**  ·  {L['demand_count']} (gross): {row.gross_demand}")
                                e1, e2, e3, e4 = st.columns(4)
                                ep = e1.number_input(L["ep"], min_value=0, value=row.ep, key=f"ep_{row.id}")
                                ba = e2.number_input(L["ba"], min_value=0, value=row.ba, key=f"ba_{row.id}")
                                internal = e3.number_input(L["internal"], min_value=0, value=row.internal, key=f"in_{row.id}")
                                trainee = e4.number_input(L["trainee"], min_value=0, value=row.trainee, key=f"tr_{row.id}")
                                total = ep + ba + internal + trainee
                                status = "Match" if total == row.gross_demand else "Mismatch"
                                st.write(f"Sum: {total}  ·  {L['status']}: **{status}**")
                                if st.button("Save this row", key=f"save_{row.id}"):
                                    db.update_supply(row.id, ep, ba, internal, trainee)
                                    st.success("Saved.")
                                    st.rerun()
