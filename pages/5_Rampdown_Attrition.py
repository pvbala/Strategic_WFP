import streamlit as st

import database as db
import master_data as md
from common import bootstrap, plan_banner

sys_cfg, plan, L = bootstrap("Function 3: Rampdown & Attrition")
SL_ON = sys_cfg.service_line_enabled
GR_ON = sys_cfg.grade_enabled
ST_ON = sys_cfg.secondary_technology_enabled

st.header("Function 3: Create rampdown and attrition")
plan_banner(plan)

if plan is None or not (plan.start_date and plan.end_date):
    st.warning("Set up an active Workforce Plan (with a Start/End Date window) in Application Configuration first.")
else:
    customer = st.selectbox(L["customer"], [""] + md.get_customers())
    if customer:
        info = md.get_customer_info(customer)
        c1, c2 = st.columns(2)
        c1.text_input(L["requirement_iou"], value=info["iou"], disabled=True)
        c2.text_input(L["requirement_sub_iou"], value=info["sub_iou"], disabled=True)

        loc_col, date_col = st.columns(2)
        location = loc_col.selectbox(L["location"], [""] + md.get_locations())
        start_date = date_col.date_input(
            L["start_date"], value=None, format="YYYY-MM-DD", key="f3_date",
            min_value=plan.start_date, max_value=plan.end_date,
        )

        if location and start_date:
            st.text_input(L["country"], value=md.get_country(location), disabled=True, key="f3_country")

            cols = st.columns(3 if (ST_ON and GR_ON) else (2 if (ST_ON or GR_ON) else 1))
            idx = 0
            primary_tech = cols[idx].selectbox(L["primary_technology"], [""] + md.PRIMARY_TECHNOLOGIES, key="f3_ptech"); idx += 1
            secondary_tech = ""
            if ST_ON:
                secondary_tech = cols[idx].selectbox(
                    L["secondary_technology"] + " (optional)", [""] + md.SECONDARY_TECHNOLOGIES, key="f3_stech"); idx += 1
            grade = ""
            if GR_ON:
                grade = cols[idx].selectbox(L["grade"], [""] + md.GRADES, key="f3_grade"); idx += 1

            ready_step3 = primary_tech and (grade or not GR_ON)

            if ready_step3:
                if SL_ON:
                    st.subheader(f"{L['service_line']}s — {L['rampdown'].lower()} / {L['release_to_other_accounts'].lower()} / {L['attrition'].lower()} per line")
                    if "f3_rows" not in st.session_state:
                        st.session_state.f3_rows = []

                    sl_col, rd_col, rel_col, at_col, btn_col = st.columns([2, 1, 1, 1, 1])
                    new_sl = sl_col.selectbox(L["service_line"], [""] + md.SERVICE_LINES, key="f3_new_sl")
                    new_rd = rd_col.number_input(L["rampdown"], min_value=0, value=0, key="f3_new_rd")
                    new_rel = rel_col.number_input(L["release_to_other_accounts"], min_value=0, value=0, key="f3_new_rel")
                    new_at = at_col.number_input(L["attrition"], min_value=0, value=0, key="f3_new_at")
                    if btn_col.button("Add"):
                        if new_sl:
                            st.session_state.f3_rows.append(
                                {"sl": new_sl, "rd": new_rd, "rel": new_rel, "at": new_at})
                            st.rerun()

                    for i, r in enumerate(st.session_state.f3_rows):
                        rc1, rc2, rc3, rc4, rc5 = st.columns([2, 1, 1, 1, 1])
                        rc1.write(r["sl"]); rc2.write(f"{L['rampdown']} {r['rd']}"); rc3.write(f"{L['release_to_other_accounts']} {r['rel']}"); rc4.write(f"{L['attrition']} {r['at']}")
                        if rc5.button("Remove", key=f"f3_rm_{i}"):
                            st.session_state.f3_rows.pop(i)
                            st.rerun()

                    if st.button(f"Save {L['rampdown'].lower()} / {L['attrition'].lower()}", type="primary", disabled=not st.session_state.f3_rows):
                        rows = []
                        for r in st.session_state.f3_rows:
                            rows.append(dict(
                                plan_id=plan.id,
                                customer=customer, location=location, start_date=start_date,
                                primary_technology=primary_tech,
                                secondary_technology=secondary_tech if (ST_ON and secondary_tech) else None,
                                service_line=r["sl"], grade=grade if GR_ON else None,
                                row_type=db.ROW_TYPE_RAMPDOWN,
                                country=md.get_country(location),
                                requirement_iou=info["iou"], requirement_sub_iou=info["sub_iou"],
                                demand_count=0, rampdown=r["rd"],
                                release_to_other_accounts=r["rel"], attrition=r["at"],
                                ep=0, ba=0, internal=r["rel"] + r["at"], trainee=0,
                            ))
                        db.add_rows(rows)
                        st.session_state.f3_rows = []
                        st.success(f"Saved {len(rows)} rampdown row(s).")
                        st.rerun()
                else:
                    st.subheader(f"{L['rampdown']} / {L['release_to_other_accounts']} / {L['attrition']}")
                    r1, r2, r3 = st.columns(3)
                    rd = r1.number_input(L["rampdown"], min_value=0, value=0, key="f3_rd_single")
                    rel = r2.number_input(L["release_to_other_accounts"], min_value=0, value=0, key="f3_rel_single")
                    at = r3.number_input(L["attrition"], min_value=0, value=0, key="f3_at_single")
                    if st.button(f"Save {L['rampdown'].lower()} / {L['attrition'].lower()}", type="primary"):
                        db.add_rows([dict(
                            plan_id=plan.id,
                            customer=customer, location=location, start_date=start_date,
                            primary_technology=primary_tech,
                            secondary_technology=secondary_tech if (ST_ON and secondary_tech) else None,
                            service_line=None, grade=grade if GR_ON else None,
                            row_type=db.ROW_TYPE_RAMPDOWN,
                            country=md.get_country(location),
                            requirement_iou=info["iou"], requirement_sub_iou=info["sub_iou"],
                            demand_count=0, rampdown=rd,
                            release_to_other_accounts=rel, attrition=at,
                            ep=0, ba=0, internal=rel + at, trainee=0,
                        )])
                        st.success("Saved 1 rampdown row.")
                        st.rerun()
