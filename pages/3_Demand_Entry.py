import streamlit as st

import alert_engine as ae
import database as db
import master_data as md
import rag_engine as rag
from common import bootstrap, plan_banner, render_alert_card, render_similarity_insight_card, any_llm_configured, get_llm_config, render_llm_attempts_debug

sys_cfg, plan, L = bootstrap("Function 1: Demand Entry")
SL_ON = sys_cfg.service_line_enabled
GR_ON = sys_cfg.grade_enabled
ST_ON = sys_cfg.secondary_technology_enabled
AI_AVAILABLE = any_llm_configured()
llm_cfg = get_llm_config()

st.header("Function 1: Demand entry & WFP generation")
plan_banner(plan)

# Keys for every entry-step widget, so a successful save can fully reset the
# form (previously only the service-line list was cleared, leaving stale
# Technology/Grade/Demand Count values behind and showing a false "Mismatch"
# for the next, still-empty entry).
FORM_KEYS = ["f1_customer", "f1_location", "f1_start_date",
             "f1_primary_tech", "f1_secondary_tech", "f1_grade", "f1_demand_count"]


def reset_form():
    for key in FORM_KEYS:
        if key in st.session_state:
            del st.session_state[key]
    st.session_state.sl_rows = []
    st.session_state.sl_edit_index = None
    # Clear cached alert state too, so a new entry doesn't show a stale alert
    # from the previous Customer/Technology combination.
    for key in list(st.session_state.keys()):
        if key.startswith("f1_alerts_") or key.startswith("f1_alert_logid_") or key.startswith("f1_similarity_"):
            del st.session_state[key]


if plan is None or not (plan.start_date and plan.end_date):
    st.warning("Set up an active Workforce Plan (with a Start/End Date window) in Application Configuration first.")
else:
    customer = st.selectbox(L["customer"], [""] + md.get_customers(), key="f1_customer")
    if customer:
        info = md.get_customer_info(customer)
        c1, c2 = st.columns(2)
        c1.text_input(L["requirement_iou"], value=info["iou"], disabled=True)
        c2.text_input(L["requirement_sub_iou"], value=info["sub_iou"], disabled=True)

        loc_col, date_col = st.columns(2)
        location = loc_col.selectbox(L["location"], [""] + md.get_locations(), key="f1_location")
        start_date = date_col.date_input(
            L["start_date"], value=None, format="YYYY-MM-DD",
            min_value=plan.start_date, max_value=plan.end_date, key="f1_start_date",
        )

        if location and start_date:
            st.text_input(L["country"], value=md.get_country(location), disabled=True)

            # Existing Demand for this Customer/Location/Start Date — Edit (Demand Count only;
            # the other key fields aren't editable here, to avoid re-keying collisions) or
            # Delete (with a two-step confirmation, since deleting a Demand Row also deletes
            # its Supply Plan values — EP/BA/Internal/Trainee live on the same row, so there's
            # nothing separate left behind to clean up, but the planner needs to know that's
            # what's about to happen before it does).
            existing_rows = db.demand_rows_for(plan.id, customer, location, start_date)
            if existing_rows:
                st.subheader("Existing Demand — Edit or Delete")
                for row in existing_rows:
                    row_label = row.service_line or row.primary_technology
                    if GR_ON and row.grade:
                        row_label += f" — {row.grade}"
                    confirm_key = f"f1_confirm_delete_{row.id}"

                    with st.container(border=True):
                        st.write(
                            f"**{row_label}**  ·  {L['status']}: **{row.status}**  ·  "
                            f"{L['gross_demand']}: {row.gross_demand}  ·  {L['total_supply']}: {row.total_supply}"
                        )
                        ec1, ec2, ec3 = st.columns([2, 1, 1])
                        new_count = ec1.number_input(
                            L["demand_count"], min_value=0, value=row.demand_count, key=f"f1_edit_count_{row.id}"
                        )
                        if ec2.button("Save", key=f"f1_save_edit_{row.id}"):
                            db.update_demand_count(row.id, new_count)
                            st.success(f"{L['demand_count']} updated — {L['status']} recalculated automatically.")
                            st.rerun()
                        if ec3.button("Delete", key=f"f1_delete_btn_{row.id}"):
                            st.session_state[confirm_key] = True
                            st.rerun()

                        if st.session_state.get(confirm_key):
                            warn = f"⚠️ This will permanently delete this {L['demand_count'].lower()} row"
                            if row.total_supply > 0:
                                warn += (
                                    f" **and its Supply Plan** (currently {L['ep']} {row.ep} / {L['ba']} {row.ba} / "
                                    f"{L['internal']} {row.internal} / {L['trainee']} {row.trainee}, {row.status})"
                                )
                            warn += ". This cannot be undone."
                            st.warning(warn)
                            wc1, wc2 = st.columns(2)
                            if wc1.button("Yes, delete", key=f"f1_confirm_delete_btn_{row.id}", type="primary"):
                                db.delete_row(row.id)
                                st.session_state.pop(confirm_key, None)
                                st.success("Deleted.")
                                st.rerun()
                            if wc2.button("Cancel", key=f"f1_cancel_delete_{row.id}"):
                                st.session_state.pop(confirm_key, None)
                                st.rerun()
                st.divider()

            cols = st.columns(4 if (ST_ON and GR_ON) else (3 if (ST_ON or GR_ON) else 2))
            idx = 0
            primary_tech = cols[idx].selectbox(L["primary_technology"], [""] + md.PRIMARY_TECHNOLOGIES, key="f1_primary_tech"); idx += 1
            secondary_tech = ""
            if ST_ON:
                secondary_tech = cols[idx].selectbox(
                    L["secondary_technology"] + " (optional)", [""] + md.SECONDARY_TECHNOLOGIES, key="f1_secondary_tech"); idx += 1
            grade = ""
            if GR_ON:
                grade = cols[idx].selectbox(L["grade"], [""] + md.GRADES, key="f1_grade"); idx += 1
            demand_count = cols[idx].number_input(L["demand_count"], min_value=0, value=0, step=1, key="f1_demand_count")

            # Secondary Technology is optional even when enabled — not required for readiness.
            ready_step3 = primary_tech and (grade or not GR_ON) and demand_count > 0

            if ready_step3:
                # Contextual alert (Path A, no LLM) — computed fresh each rerun from
                # alerts_db.DemandEvent history. Both granularities (customer-level and
                # customer+technology) are checked independently; either, both, or
                # neither may fire. See alert_engine.py.
                alert_cache_key = f"f1_alerts_{customer}_{primary_tech}"
                if alert_cache_key not in st.session_state:
                    st.session_state[alert_cache_key] = ae.get_customer_behavior_alerts(
                        customer, primary_technology=primary_tech
                    )
                behavior_alerts = st.session_state[alert_cache_key]

                if behavior_alerts:
                    st.subheader("⚠️ Contextual Alert")
                    for i, a in enumerate(behavior_alerts):
                        log_key = f"f1_alert_logid_{customer}_{primary_tech}_{i}"
                        if log_key not in st.session_state:
                            st.session_state[log_key] = ae.log_shown_alert(a, plan_id=plan.id)
                        render_alert_card(a, st.session_state[log_key])

                # Path B (semantic/RAG) — opt-in, not automatic, since unlike Path A this
                # costs an embedding + LLM call every time. Covers the Similarity/Analogy
                # scenario family Path A structurally cannot: no clean structured key to
                # filter on, so this searches across ALL customers' logged notes by meaning
                # AND by exact keyword (hybrid_search) — across every plan, not just the
                # active one, since a customer's behavior history isn't plan-scoped.
                if AI_AVAILABLE:
                    similarity_key = f"f1_similarity_{customer}_{primary_tech}"
                    debug_key = f"f1_similarity_debug_{customer}_{primary_tech}"
                    logid_key = f"f1_similarity_logid_{customer}_{primary_tech}"
                    if st.button("🔍 Check for similar situations across other customers (AI)", key=f"f1_sim_btn_{customer}_{primary_tech}"):
                        query_text = f"{customer}, {primary_tech}" + (f", {grade}" if grade else "")
                        with st.spinner("Searching..."):
                            hits, search_attempts = rag.hybrid_search(
                                query_text, gemini_key=llm_cfg["gemini_key"], ollama_host=llm_cfg["ollama_host"],
                                exclude_customer=customer,
                            )
                        insight, synth_attempts = None, []
                        if hits:
                            hits_2tuple = [(s, r) for s, r, _t in hits]
                            with st.spinner("Summarizing..."):
                                insight, synth_attempts = rag.synthesize_similarity_insight(
                                    f"New demand: {query_text}", hits_2tuple,
                                    gemini_key=llm_cfg["gemini_key"], groq_key=llm_cfg["groq_key"],
                                    ollama_host=llm_cfg["ollama_host"], ollama_model=llm_cfg["ollama_model"],
                                )
                        st.session_state[similarity_key] = insight
                        st.session_state[debug_key] = search_attempts + synth_attempts
                        if insight:
                            st.session_state[logid_key] = rag.log_shown_insight(
                                insight, customer=customer, primary_technology=primary_tech, plan_id=plan.id,
                            )
                        else:
                            st.session_state.pop(logid_key, None)

                    cached_insight = st.session_state.get(similarity_key)
                    if cached_insight:
                        render_similarity_insight_card(cached_insight, st.session_state.get(logid_key))
                    elif similarity_key in st.session_state:
                        search_succeeded = any(a["status"] == "success" for a in st.session_state.get(debug_key, []))
                        if search_succeeded:
                            st.caption("No sufficiently similar situations found in other customers' logged notes.")
                        else:
                            st.warning("The similarity check couldn't complete — see details below.")
                            render_llm_attempts_debug(st.session_state.get(debug_key, []))

                if SL_ON:
                    st.subheader(L["service_line"] + "s")
                    if "sl_rows" not in st.session_state:
                        st.session_state.sl_rows = []
                    if "sl_edit_index" not in st.session_state:
                        st.session_state.sl_edit_index = None

                    sl_col, cnt_col, btn_col = st.columns([2, 1, 1])
                    new_sl = sl_col.selectbox(L["service_line"], [""] + md.SERVICE_LINES, key="new_sl")
                    new_cnt = cnt_col.number_input("Requirement count", min_value=0, value=0, step=1, key="new_cnt")
                    if btn_col.button("Add " + L["service_line"].lower()):
                        existing_sls = [r["sl"] for r in st.session_state.sl_rows]
                        if not new_sl or new_cnt <= 0:
                            pass
                        elif new_sl in existing_sls:
                            st.error(f"{new_sl} has already been added — duplicate {L['service_line'].lower()}s "
                                     "are not allowed. Edit the existing entry instead.")
                        else:
                            st.session_state.sl_rows.append({"sl": new_sl, "cnt": new_cnt})
                            st.rerun()

                    total_entered = sum(r["cnt"] for r in st.session_state.sl_rows)
                    for i, r in enumerate(st.session_state.sl_rows):
                        if st.session_state.sl_edit_index == i:
                            ec1, ec2, ec3, ec4 = st.columns([2, 1, 1, 1])
                            edit_sl = ec1.selectbox(
                                L["service_line"], md.SERVICE_LINES,
                                index=md.SERVICE_LINES.index(r["sl"]) if r["sl"] in md.SERVICE_LINES else 0,
                                key=f"edit_sl_{i}",
                            )
                            edit_cnt = ec2.number_input("Requirement count", min_value=0, value=r["cnt"], key=f"edit_cnt_{i}")
                            if ec3.button("Save", key=f"save_edit_{i}"):
                                other_sls = [row["sl"] for j, row in enumerate(st.session_state.sl_rows) if j != i]
                                if edit_sl in other_sls:
                                    st.error(f"{edit_sl} is already used by another row — duplicate "
                                             f"{L['service_line'].lower()}s are not allowed.")
                                else:
                                    st.session_state.sl_rows[i] = {"sl": edit_sl, "cnt": edit_cnt}
                                    st.session_state.sl_edit_index = None
                                    st.rerun()
                            if ec4.button("Cancel", key=f"cancel_edit_{i}"):
                                st.session_state.sl_edit_index = None
                                st.rerun()
                        else:
                            rc1, rc2, rc3, rc4 = st.columns([2, 1, 1, 1])
                            rc1.write(r["sl"])
                            rc2.write(r["cnt"])
                            if rc3.button("Edit", key=f"edit_{i}"):
                                st.session_state.sl_edit_index = i
                                st.rerun()
                            if rc4.button("Remove", key=f"rm_{i}"):
                                st.session_state.sl_rows.pop(i)
                                st.rerun()

                    match = total_entered == demand_count
                    st.metric(f"Total entered vs {L['demand_count'].lower()}", f"{total_entered} / {demand_count}",
                              delta="Match" if match else "Mismatch",
                              delta_color="normal" if match else "inverse")

                    if st.button("Save demand", type="primary", disabled=not match or not st.session_state.sl_rows):
                        rows = []
                        for r in st.session_state.sl_rows:
                            rows.append(dict(
                                plan_id=plan.id,
                                customer=customer, location=location, start_date=start_date,
                                primary_technology=primary_tech,
                                secondary_technology=secondary_tech if (ST_ON and secondary_tech) else None,
                                service_line=r["sl"], grade=grade if GR_ON else None,
                                row_type=db.ROW_TYPE_DEMAND,
                                country=md.get_country(location),
                                requirement_iou=info["iou"], requirement_sub_iou=info["sub_iou"],
                                demand_count=r["cnt"],
                            ))
                        created, updated = db.save_demand_rows(rows)
                        msg = []
                        if created: msg.append(f"{created} new row(s) created")
                        if updated: msg.append(f"{updated} existing row(s) updated (duplicate key — edited instead of duplicated)")
                        reset_form()
                        st.success(("; ".join(msg) if msg else "Saved.") + " The form has been reset for the next entry.")
                        st.rerun()
                else:
                    st.info(f"{L['service_line']} is disabled — this {L['demand_count'].lower()} will be saved as a single row.")
                    if st.button("Save demand", type="primary"):
                        created, updated = db.save_demand_rows([dict(
                            plan_id=plan.id,
                            customer=customer, location=location, start_date=start_date,
                            primary_technology=primary_tech,
                            secondary_technology=secondary_tech if (ST_ON and secondary_tech) else None,
                            service_line=None, grade=grade if GR_ON else None,
                            row_type=db.ROW_TYPE_DEMAND,
                            country=md.get_country(location),
                            requirement_iou=info["iou"], requirement_sub_iou=info["sub_iou"],
                            demand_count=demand_count,
                        )])
                        if created:
                            msg = "Saved 1 new demand row."
                        else:
                            msg = "Duplicate key detected — existing row's " + L["demand_count"] + " was updated instead of creating a new row."
                        reset_form()
                        st.success(msg + " The form has been reset for the next entry.")
                        st.rerun()
