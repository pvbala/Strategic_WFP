import datetime

import streamlit as st

import alerts_db as adb
import llm_providers as llm
import master_data as md
import rag_engine as rag
from common import bootstrap, plan_banner, any_llm_configured, get_llm_config, render_llm_attempts_debug

sys_cfg, plan, L = bootstrap("Log a Demand Event")

st.header("Log a Demand Event")
plan_banner(plan)
st.caption(
    "Record what actually happened against previously entered demand — a postponement, "
    "reduction, cancellation, increase, or on-time confirmation. This builds the history "
    "the contextual alerts in Demand Entry are based on."
)

AI_AVAILABLE = any_llm_configured()
llm_cfg = get_llm_config()

customer = st.selectbox(L["customer"], [""] + md.get_customers(), key="f8_customer")

if customer:
    loc_col, tech_col = st.columns(2)
    location = loc_col.selectbox(L["location"] + " (optional)", [""] + md.get_locations(), key="f8_location")
    primary_technology = tech_col.selectbox(
        L["primary_technology"] + " (optional)", [""] + md.PRIMARY_TECHNOLOGIES, key="f8_primary_tech"
    )
    st.caption(
        "Leave Location/Primary Technology blank if this event applies to the customer broadly. "
        "Setting Primary Technology lets this event also feed the finer-grained "
        "Customer + Technology alert."
    )

    st.divider()

    # ---------------------------------------------------------------- Entry mode
    mode_options = ["Structured"]
    if AI_AVAILABLE:
        mode_options.append("Free Text (AI-assisted)")
    else:
        st.caption(
            "Free-text entry with AI-assisted extraction is available once a provider is "
            "configured in AI Settings. Structured entry below always works."
        )
    mode = st.radio("Entry Mode", mode_options, horizontal=True, key="f8_mode")

    # ---------------------------------------------------------------- Free-text extraction (AI mode only)
    if mode == "Free Text (AI-assisted)":
        st.text_area(
            "Describe what happened, in your own words",
            key="f8_free_text",
            help="e.g. \"Customer said finance froze the budget, pushing this out about 6 weeks "
                 "and cutting headcount from 30 to 20.\"",
        )
        if st.button("Extract with AI"):
            raw_text = st.session_state.get("f8_free_text", "")
            if not raw_text.strip():
                st.error("Enter a description first.")
            else:
                with st.spinner("Extracting..."):
                    parsed, provider, attempts_log = llm.extract_event_fields(
                        raw_text, gemini_key=llm_cfg["gemini_key"], groq_key=llm_cfg["groq_key"],
                        ollama_host=llm_cfg["ollama_host"], ollama_model=llm_cfg["ollama_model"],
                    )
                if parsed is None:
                    st.error(
                        "Extraction failed — every configured provider was unreachable or "
                        "returned an unusable response. You can still fill in the structured "
                        "fields below manually; your free text is preserved in Notes."
                    )
                    render_llm_attempts_debug(attempts_log)
                    st.session_state["f8_pending_extraction"] = None
                else:
                    st.session_state["f8_pending_extraction"] = {"parsed": parsed, "provider": provider, "raw_text": raw_text}
                    st.rerun()

        pending = st.session_state.get("f8_pending_extraction")
        if pending:
            parsed = pending["parsed"]
            st.info(
                f"🤖 Extracted via **{pending['provider']}** — review and correct below before saving. "
                f"Confidence: {parsed.get('confidence', 'Unknown')}."
            )
            if st.button("Apply to form for review"):
                # Pre-set the shared structured-field widgets' session_state BEFORE they're
                # instantiated below, so the widgets render pre-filled. Values only applied
                # if they're valid options for their respective dropdowns — an unrecognized
                # value is left for the planner to set manually rather than silently dropped
                # into a field where it would look chosen but wasn't.
                if parsed.get("event_type") in adb.EVENT_TYPES:
                    st.session_state["f8_event_type"] = parsed["event_type"]
                if parsed.get("qty_before") is not None:
                    st.session_state["f8_qty_before"] = int(parsed["qty_before"])
                if parsed.get("qty_after") is not None:
                    st.session_state["f8_qty_after"] = int(parsed["qty_after"])
                reason = (parsed.get("reason_category") or "").strip().lower()
                match = next((r for r in md.get_reason_categories() if r.lower() == reason), None)
                if match:
                    st.session_state["f8_reason"] = match
                st.session_state["f8_notes"] = pending["raw_text"]
                st.session_state["f8_extraction_provider"] = pending["provider"]
                st.session_state["f8_shift_hint"] = parsed.get("estimated_shift_days")
                st.session_state["f8_pending_extraction"] = None
                st.rerun()
        st.divider()

    shift_hint = st.session_state.get("f8_shift_hint")
    extraction_provider = st.session_state.get("f8_extraction_provider")
    if extraction_provider:
        st.caption(f"🤖 Fields below were pre-filled by AI ({extraction_provider}) — please verify before saving.")

    # ---------------------------------------------------------------- Shared structured fields (both modes)
    event_type = st.selectbox(L.get("event_type", "Event Type"), adb.EVENT_TYPES, key="f8_event_type")

    c1, c2 = st.columns(2)
    original_period = c1.date_input("Original Period", value=None, format="YYYY-MM-DD", key="f8_orig_period")
    new_period = None
    if event_type in ("Postponed", "Accelerated"):
        hint = f" (AI estimated ~{shift_hint} days shift)" if shift_hint else ""
        new_period = c2.date_input("New Period" + hint, value=None, format="YYYY-MM-DD", key="f8_new_period")
    else:
        c2.text_input("New Period", value="(not applicable)", disabled=True)

    c3, c4 = st.columns(2)
    qty_before = c3.number_input("Quantity Before", min_value=0, step=1, key="f8_qty_before")
    qty_after = None
    if event_type in ("Reduced", "Increased"):
        qty_after = c4.number_input("Quantity After", min_value=0, step=1, key="f8_qty_after")
    else:
        c4.text_input("Quantity After", value="(not applicable)", disabled=True)

    reason_category = st.selectbox(
        "Reason Category", [""] + md.get_reason_categories(), key="f8_reason"
    )
    notes = st.text_area(
        "Notes (optional)", key="f8_notes",
        help="Free-text context, kept as-is. If a provider is configured, this is also "
             "embedded for use by Similarity Search and Opportunity Pipeline's similar-situation "
             "lookup — not by the alert engine, which only ever uses the structured fields above.",
    )

    c5, c6 = st.columns(2)
    logged_by = c5.text_input("Logged By", key="f8_logged_by")
    event_date = c6.date_input("Event Date", value=datetime.date.today(), format="YYYY-MM-DD", key="f8_event_date")

    ready = event_type and event_date and (
        event_type not in ("Postponed", "Accelerated") or new_period
    ) and (
        event_type not in ("Reduced", "Increased") or qty_after is not None
    )

    if st.button("Save Event", type="primary", disabled=not ready):
        event_id = adb.add_demand_event(
            customer=customer,
            primary_technology=primary_technology or None,
            location=location or None,
            event_type=event_type,
            original_period=original_period,
            new_period=new_period,
            qty_before=qty_before or None,
            qty_after=qty_after,
            reason_category=reason_category or None,
            notes=notes or None,
            linked_plan_id=plan.id if plan else None,
            logged_by=logged_by or None,
            event_date=event_date,
            human_verified=True,  # clicking Save is the human confirmation, in both modes
            extraction_provider=extraction_provider,
        )
        # Best-effort embedding for Similarity Search — never blocks the save above.
        if notes and AI_AVAILABLE:
            try:
                rag.embed_and_store(
                    notes, "demand_events", event_id, customer=customer,
                    primary_technology=primary_technology or None,
                    gemini_key=llm_cfg["gemini_key"], ollama_host=llm_cfg["ollama_host"],
                )
            except Exception:
                pass  # embedding is best-effort; the event itself is already saved

        for k in ("f8_pending_extraction", "f8_extraction_provider", "f8_shift_hint"):
            st.session_state.pop(k, None)
        st.success(f"Event logged for {customer}.")
        st.rerun()

    if not ready:
        st.caption(
            "Fill in Event Type, Event Date, and the fields that apply to the selected "
            "Event Type before saving."
        )

    st.divider()
    st.subheader(f"Recent events — {customer}")
    events = adb.get_events_for_customer(customer)
    if not events:
        st.info("No events logged for this customer yet.")
    else:
        rows = []
        for e in events[:20]:
            rows.append({
                "Date": e.event_date.strftime("%d-%b-%y"),
                "Type": e.event_type,
                "Technology": e.primary_technology or "",
                "Shift (days)": str(e.lead_time_days) if e.lead_time_days is not None else "",
                "Qty Δ": str(e.qty_delta) if e.qty_delta is not None else "",
                "Reason": e.reason_category or "",
                "Logged By": e.logged_by or "",
                "Source": e.extraction_provider or "Manual",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
