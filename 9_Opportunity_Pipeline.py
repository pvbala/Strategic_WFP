import streamlit as st

import alerts_db as adb
import master_data as md
import rag_engine as rag
from common import bootstrap, fmt_date, plan_banner, any_llm_configured, get_llm_config, render_llm_attempts_debug, render_similarity_insight_card

sys_cfg, plan, L = bootstrap("Opportunity Pipeline")

AI_AVAILABLE = any_llm_configured()
llm_cfg = get_llm_config()

st.header("Opportunity Pipeline")
plan_banner(plan)
st.caption(
    "Track pre-demand opportunities — from an early, informal signal through RFP to "
    "win/loss. These are separate from Demand Entry: an opportunity may never convert "
    "into actual demand."
)

st.subheader("New Opportunity")

customer = st.selectbox(L["customer"], [""] + md.get_customers(), key="f9_customer")
if customer:
    c1, c2 = st.columns(2)
    primary_technology = c1.selectbox(L["primary_technology"] + " (optional)", [""] + md.PRIMARY_TECHNOLOGIES, key="f9_tech")
    stage = c2.selectbox("Stage", adb.OPPORTUNITY_STAGES, key="f9_stage")

    c3, c4, c5 = st.columns(3)
    estimated_qty = c3.number_input("Estimated Quantity", min_value=0, value=0, step=1, key="f9_qty")
    estimated_start_date = c4.date_input("Estimated Start Date", value=None, format="YYYY-MM-DD", key="f9_start")

    expects_probability = stage in adb.STAGES_EXPECTING_PROBABILITY
    if expects_probability:
        win_probability = c5.number_input("Win Probability (%)", min_value=0, max_value=100, value=50, step=5, key="f9_prob")
    else:
        c5.text_input("Win Probability (%)", value="(not yet known)", disabled=True)
        win_probability = None
        if stage == "Suspecting":
            st.caption(
                "Suspecting-stage opportunities often don't have a probability yet — that's "
                "expected. Use \"Find Similar Past Situations\" below (once saved, if an AI "
                "provider is configured) to see whether anything like this has come up before."
            )

    notes = st.text_area("Notes (optional)", key="f9_notes")
    created_by = st.text_input("Created By", key="f9_created_by")

    if st.button("Save Opportunity", type="primary", disabled=not customer):
        opp_id = adb.add_opportunity(
            customer=customer,
            primary_technology=primary_technology or None,
            stage=stage,
            estimated_qty=estimated_qty or None,
            estimated_start_date=estimated_start_date,
            win_probability=win_probability,
            notes=notes or None,
            created_by=created_by or None,
            human_verified=True,
        )
        if notes and AI_AVAILABLE:
            try:
                rag.embed_and_store(
                    notes, "opportunity", opp_id, customer=customer,
                    primary_technology=primary_technology or None,
                    gemini_key=llm_cfg["gemini_key"], ollama_host=llm_cfg["ollama_host"],
                )
            except Exception:
                pass
        st.success(f"Opportunity saved for {customer}.")
        st.rerun()

st.divider()
st.subheader("Pipeline")

opps = adb.get_all_opportunities()
if not opps:
    st.info("No opportunities logged yet.")
else:
    filter_customer = st.selectbox("Filter by customer", ["All"] + md.get_customers(), key="f9_filter_customer")
    shown = [o for o in opps if filter_customer == "All" or o.customer == filter_customer]

    rows = []
    weighted_total = 0
    for o in shown:
        weighted = None
        if o.estimated_qty and o.win_probability is not None:
            weighted = round(o.estimated_qty * o.win_probability / 100, 1)
            weighted_total += weighted
        rows.append({
            "Customer": o.customer,
            "Technology": o.primary_technology or "",
            "Stage": o.stage,
            "Est. Qty": str(o.estimated_qty) if o.estimated_qty is not None else "—",
            "Win Prob.": f"{o.win_probability}%" if o.win_probability is not None else "—",
            "Weighted Qty": str(weighted) if weighted is not None else "—",
            "Est. Start": fmt_date(o.estimated_start_date) if o.estimated_start_date else "",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    if weighted_total:
        st.metric("Total weighted pipeline (Est. Qty × Win Probability)", round(weighted_total, 1))
    st.caption(
        "Weighted Qty is only computed for RFP/Negotiation-stage opportunities with a "
        "probability set. Suspecting-stage opportunities are listed but excluded from the total."
    )

    st.divider()
    st.subheader("Find Similar Past Situations (AI)")
    if not AI_AVAILABLE:
        st.info("Configure a provider in AI Settings to enable this — useful mainly for "
                "Suspecting-stage opportunities that don't have a probability to go on yet.")
    else:
        st.caption(
            "Hybrid search — keyword + semantic — across every logged note (demand events "
            "and other opportunities, across all customers and all Workforce Plans, not just "
            "the active one) — surfaces situations that read similarly, even for customers "
            "or wording you wouldn't think to search for directly, as well as exact name/term "
            "matches semantic search alone can miss. This is Path B (RAG): the summary below "
            "is AI-generated and grounded only in the retrieved snippets shown beneath it — "
            "treat it as a lead to investigate, not a fact."
        )
        similarity_opp = st.selectbox(
            "Opportunity to check", shown, format_func=lambda o: f"{o.customer} — {o.stage} ({fmt_date(o.estimated_start_date) if o.estimated_start_date else 'no date'})",
            key="f9_similarity_opp",
        )
        opp_sim_key = f"f9_sim_result_{similarity_opp.id}"
        opp_debug_key = f"f9_sim_debug_{similarity_opp.id}"
        opp_logid_key = f"f9_sim_logid_{similarity_opp.id}"

        if st.button("Find Similar Past Situations"):
            query_parts = [similarity_opp.customer]
            if similarity_opp.primary_technology:
                query_parts.append(similarity_opp.primary_technology)
            if similarity_opp.notes:
                query_parts.append(similarity_opp.notes)
            query_text = " — ".join(query_parts)

            with st.spinner("Searching..."):
                hits, search_attempts = rag.hybrid_search(
                    query_text, gemini_key=llm_cfg["gemini_key"], ollama_host=llm_cfg["ollama_host"],
                    exclude_customer=similarity_opp.customer,
                )
            insight, synth_attempts = None, []
            if hits:
                hits_2tuple = [(s, r) for s, r, _t in hits]
                with st.spinner("Summarizing..."):
                    insight, synth_attempts = rag.synthesize_similarity_insight(
                        f"Opportunity: {query_text}", hits_2tuple,
                        gemini_key=llm_cfg["gemini_key"], groq_key=llm_cfg["groq_key"],
                        ollama_host=llm_cfg["ollama_host"], ollama_model=llm_cfg["ollama_model"],
                    )
            st.session_state[opp_sim_key] = insight
            st.session_state[opp_debug_key] = search_attempts + synth_attempts
            if insight:
                st.session_state[opp_logid_key] = rag.log_shown_insight(
                    insight, customer=similarity_opp.customer, primary_technology=similarity_opp.primary_technology,
                )
            else:
                st.session_state.pop(opp_logid_key, None)

        cached_insight = st.session_state.get(opp_sim_key)
        if cached_insight:
            render_similarity_insight_card(cached_insight, st.session_state.get(opp_logid_key))
        elif opp_sim_key in st.session_state:
            debug_log = st.session_state.get(opp_debug_key, [])
            search_succeeded = any(a["status"] == "success" for a in debug_log)
            if search_succeeded:
                st.info("No sufficiently similar past situations found.")
            else:
                st.warning("The search couldn't complete — see details below.")
                render_llm_attempts_debug(debug_log)
