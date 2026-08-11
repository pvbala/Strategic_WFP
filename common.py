import streamlit as st

import database as db


def fmt_date(d):
    """Display format for dates: dd-mmm-yy, e.g. 05-Aug-26."""
    if d is None:
        return ""
    return d.strftime("%d-%b-%y")


def _inject_css():
    st.markdown("""
    <style>
    /* Fixed px units throughout (not rem) — Plotly injects its own global
       html{font-size} reset for its internal layout math, which otherwise
       shrinks every rem-based size elsewhere on the page, including the
       sidebar, the moment a chart renders (e.g. on the Analytics page). */
    html, body, .stApp { font-size: 18px; }
    p, span, div, label, li { font-size: 16px !important; }
    h1 { font-size: 34px !important; }
    h2 { font-size: 27px !important; }
    h3 { font-size: 22px !important; }
    .stButton > button { font-size: 16px !important; }
    .stMetric [data-testid="stMetricValue"] { font-size: 26px !important; }
    .stMetric [data-testid="stMetricLabel"] { font-size: 16px !important; }
    input[type="number"] { text-align: center !important; }
    .stDataFrame div[data-testid="stDataFrameResizable"] { font-size: 16px !important; }

    /* Explicit sidebar pin, high specificity, so it can't be overridden by
       anything injected later in the DOM (e.g. by chart libraries). */
    section[data-testid="stSidebar"] * {
        font-size: 16px !important;
    }
    section[data-testid="stSidebar"] h1 {
        font-size: 22px !important;
    }
    </style>
    """, unsafe_allow_html=True)


def get_llm_config():
    """Reads whatever AI Settings has saved to session_state, in the form
    every llm_providers.py / rag_engine.py function expects. Returns a
    dict; values are None/empty when not configured, so callers can pass
    this straight through as kwargs and get correct graceful-degradation
    behavior for free."""
    return {
        "gemini_key": st.session_state.get("gemini_api_key") or None,
        "groq_key": st.session_state.get("groq_api_key") or None,
        "ollama_host": st.session_state.get("ollama_host") or None,
        "ollama_model": st.session_state.get("ollama_model") or "llama3",
    }


def any_llm_configured():
    cfg = get_llm_config()
    return bool(cfg["gemini_key"] or cfg["groq_key"] or cfg["ollama_host"])


def render_similarity_insight_card(insight, alert_log_id=None):
    """Renders a Path B (semantic/RAG) insight — visually distinct from
    render_alert_card's Path A styling (blue instead of red/orange) so
    it's never confused with a grounded, structured alert. Always labeled
    as AI-generated and unverified; the caller is responsible for having
    already applied the grounding constraint at generation time (see
    rag_engine.synthesize_similarity_insight).

    alert_log_id, if provided (the id returned by
    rag_engine.log_shown_insight), renders Useful/Not Useful feedback
    buttons wired to alerts_db.record_alert_feedback — the same feedback
    loop Path A alerts have via render_alert_card, previously missing
    for Path B."""
    import alerts_db as adb

    st.markdown(f"""
    <div style="border-left: 6px solid #1F6FEB; background: #F0F7FF;
                border-radius: 10px; padding: 18px 22px; margin-bottom: 14px;">
      <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
        <span style="background:#1F6FEB; color:white; font-size:11px; font-weight:700;
                     padding:4px 10px; border-radius:20px; letter-spacing:0.5px;">
          AI SIMILARITY INSIGHT — UNVERIFIED
        </span>
      </div>
      <div style="font-size:16px; font-weight:700; color:#16324f; margin-bottom:8px;">
        {insight['headline']}
      </div>
      <div style="font-size:14px; color:#28405c; line-height:1.55; margin-bottom:10px;">
        {insight['summary']}
      </div>
      <div style="font-size:12.5px; color:#4a6b8a; margin-bottom:10px;">
        ⚠ {insight['caveat']}
      </div>
      <div style="background:#ffffff; border:1px solid #d7e6f5; border-radius:8px; padding:10px 14px;">
        <div style="font-size:11px; font-weight:700; color:#4a6b8a; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px;">Retrieved Evidence</div>
        {''.join(f'<div style="font-size:13px; color:#28405c; margin-bottom:3px;">• [{e["customer"]}] {e["text"]} <span style="color:#8aa5bd;">(similarity {e["score"]})</span></div>' for e in insight['evidence'])}
      </div>
      <div style="font-size:11px; color:#4a6b8a; margin-top:8px;">
        Source: Path B (semantic) · via {insight.get('provider', 'unknown')} · not grounded in structured counts like Path A alerts above
      </div>
    </div>
    """, unsafe_allow_html=True)

    if alert_log_id is not None:
        fb1, fb2, _ = st.columns([1, 1, 4])
        if fb1.button("👍 Useful", key=f"sim_useful_{alert_log_id}"):
            adb.record_alert_feedback(alert_log_id, "useful")
            st.toast("Thanks for the feedback.")
        if fb2.button("👎 Not Useful", key=f"sim_not_useful_{alert_log_id}"):
            adb.record_alert_feedback(alert_log_id, "not_useful")
            st.toast("Thanks for the feedback.")


def render_llm_attempts_debug(attempts_log):
    """Shows exactly what each provider returned/errored on, in an
    expander — this is what turns a bare 'extraction failed' into
    something a person can actually act on (wrong key, wrong model name,
    Ollama not running, network blocked, etc). Call this whenever an
    LLM-backed feature fails, using the attempts_log returned by
    llm_providers.chat_with_fallback / embed_with_fallback (or
    propagated through extract_event_fields / semantic_search /
    synthesize_similarity_insight)."""
    if not attempts_log:
        st.caption("No provider was configured to try — check AI Settings.")
        return
    with st.expander("Why did this fail? (per-provider details)"):
        for a in attempts_log:
            icon = {"success": "✅", "error": "❌", "empty_response": "⚠️", "unparseable_json": "⚠️"}.get(a["status"], "•")
            st.write(f"{icon} **{a['provider']}** — {a['status']}")
            if a.get("detail"):
                st.code(a["detail"], language=None)


def plan_banner(plan):
    """Shown at the top of Functions 1-4 so it's always clear which
    Workforce Plan the screen is currently operating against."""
    if plan and plan.plan_name:
        status = "🔒 Locked" if plan.locked else "🟢 Active"
        st.info(f"**Workforce Plan:** {plan.plan_name}  ·  {status}")
    else:
        st.warning("**Workforce Plan:** none selected — set one up in Application Configuration.")


def render_alert_card(alert, alert_log_id):
    """Renders a single Customer Behavior alert (see alert_engine.py) as a
    styled card, with Useful/Not Useful feedback buttons wired to
    alerts_db.record_alert_feedback. alert_log_id is the row id returned
    by alert_engine.log_shown_alert() for this specific alert."""
    import alerts_db as adb

    is_high = alert["risk_level"] == "High Risk"
    border_color = "#C0392B" if is_high else "#E8590C"
    bg_color = "#FBE9E7" if is_high else "#FFF4E8"
    badge_color = "#C0392B" if is_high else "#E8590C"

    st.markdown(f"""
    <div style="border-left: 6px solid {border_color}; background: {bg_color};
                border-radius: 10px; padding: 18px 22px; margin-bottom: 14px;">
      <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
        <span style="background:{badge_color}; color:white; font-size:11px; font-weight:700;
                     padding:4px 10px; border-radius:20px; letter-spacing:0.5px;">
          {alert['risk_level'].upper()}
        </span>
        <span style="font-size:16px; font-weight:700; color:#3a2410;">{alert['headline']}</span>
      </div>
      <div style="font-size:14px; color:#4a3a20; line-height:1.55; margin-bottom:10px;">
        {alert['body']}
      </div>
      <div style="background:#ffffff; border:1px solid #f0d9b5; border-radius:8px; padding:10px 14px; margin-bottom:10px;">
        <div style="font-size:11px; font-weight:700; color:#8a5a1a; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px;">Evidence</div>
        {''.join(f'<div style="font-size:13px; color:#4a3a20; margin-bottom:3px;">• {e}</div>' for e in alert['evidence'])}
      </div>
      <div style="font-size:13.5px; color:#2e2415; background:#FFEBD1; border-radius:6px; padding:8px 12px;">
        <b>Recommendation:</b> {alert['recommendation']}
      </div>
      <div style="font-size:11px; color:#8a5a1a; margin-top:8px;">
        Confidence: {alert['confidence']} · Sample size: {alert['sample_size']} events · Source: Path A (structured)
      </div>
    </div>
    """, unsafe_allow_html=True)

    fb1, fb2, _ = st.columns([1, 1, 4])
    if fb1.button("👍 Useful", key=f"alert_useful_{alert_log_id}"):
        adb.record_alert_feedback(alert_log_id, "useful")
        st.toast("Thanks for the feedback.")
    if fb2.button("👎 Not Useful", key=f"alert_not_useful_{alert_log_id}"):
        adb.record_alert_feedback(alert_log_id, "not_useful")
        st.toast("Thanks for the feedback.")


def bootstrap(page_title):
    """Called at the top of every page. Sets page config, ensures the DB
    is initialized, loads System Config, the active Plan (may be None if no
    plan has been created yet), and column labels; injects shared styling;
    renders the shared sidebar status. Returns (sys_cfg, plan, labels)."""
    st.set_page_config(page_title=page_title, layout="wide")
    _inject_css()
    db.init_db()

    import alerts_db as adb
    adb.init_alerts_db()

    sys_cfg = db.get_system_config()
    plan = db.get_active_plan()
    labels = db.get_column_labels()

    st.sidebar.title("Strategic Workforce Planning")
    if plan and plan.plan_name:
        st.sidebar.caption(f"Active plan: **{plan.plan_name}**")
        if plan.start_date and plan.end_date:
            st.sidebar.caption(f"Window: {fmt_date(plan.start_date)} to {fmt_date(plan.end_date)}")
    else:
        st.sidebar.caption("No active plan — visit Application Configuration.")

    if sys_cfg.locked:
        # System Configuration values are intentionally not surfaced here
        # once locked — see Home for the "locked, not displayed" notice.
        st.sidebar.caption("System configuration locked.")
    else:
        st.sidebar.caption("System configuration not yet set — visit System Configuration first.")

    return sys_cfg, plan, labels