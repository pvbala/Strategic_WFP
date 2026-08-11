import json

import streamlit as st

import alerts_db as adb
import rag_engine as rag
from common import bootstrap, any_llm_configured, get_llm_config, render_similarity_insight_card

sys_cfg, plan, L = bootstrap("Precompute Similar Situations")

st.header("Precompute Similar Situations")
st.caption(
    "Batch-generates AI similarity insights for every Suspecting-stage Opportunity, across "
    "every customer and every Workforce Plan, so you don't have to click \"Find Similar Past "
    "Situations\" individually for each one on the Opportunity Pipeline page. Also refreshes "
    "each customer's behavior-profile summary, so Similarity Search can find those patterns "
    "by meaning too — not just raw notes."
)

AI_AVAILABLE = any_llm_configured()
llm_cfg = get_llm_config()

with st.expander("What does this actually do, and what does it cost?"):
    st.markdown(
        "**Step 1 — Refresh customer profiles.** For every customer with at least 3 logged "
        "demand events, regenerates a short text summary of their behavior pattern (e.g. "
        "*\"postponed 4 of 6 times, average shift 42 days\"*) and re-embeds it, replacing any "
        "earlier version. This makes Path A findings searchable by meaning — Similarity Search "
        "otherwise only ever sees raw free-text notes, never aggregate patterns.\n\n"
        "**Step 2 — Similar-situation search, per opportunity.** For every Opportunity "
        "currently in Suspecting stage (the ones with no win probability yet), searches every "
        "*other* customer's logged notes and profiles — hybrid keyword + semantic, the same "
        "mechanism as Opportunity Pipeline's on-demand version — and asks an AI provider to "
        "summarize what it finds, grounded only in the retrieved evidence.\n\n"
        "**Cost and time**: roughly one search plus one LLM synthesis call per Suspecting-stage "
        "opportunity, plus one embedding call per customer with enough history. A handful of "
        "opportunities takes seconds; dozens could take a minute or two — a progress bar tracks "
        "it while it runs.\n\n"
        "**Every run replaces the previous results in full** — this is not additive. If an "
        "opportunity has since moved out of Suspecting stage, or been deleted, its old "
        "precomputed insight disappears on the next run rather than lingering as stale data."
    )

if not AI_AVAILABLE:
    st.warning("Configure a provider in AI Settings first — this page needs one to run.")
else:
    suspecting_opps = [o for o in adb.get_all_opportunities() if o.stage == "Suspecting"]

    if not suspecting_opps:
        st.info(
            "No Suspecting-stage opportunities currently logged — nothing to precompute yet. "
            "Log one in Opportunity Pipeline, or check back once one exists."
        )
    else:
        n_customers = len({o.customer for o in suspecting_opps})
        st.write(
            f"**{len(suspecting_opps)}** Suspecting-stage opportunit{'y' if len(suspecting_opps) == 1 else 'ies'} "
            f"found, across **{n_customers}** customer{'s' if n_customers != 1 else ''}."
        )

        if st.button("Run Precompute", type="primary"):
            progress_bar = st.progress(0.0, text="Refreshing customer profiles...")

            def on_progress(i, total):
                progress_bar.progress(i / total, text=f"Checked {i} of {total} Suspecting-stage opportunities...")

            with st.spinner("Running precompute — this may take a while depending on how many opportunities there are..."):
                result = rag.run_precompute_similar_situations(
                    gemini_key=llm_cfg["gemini_key"], groq_key=llm_cfg["groq_key"],
                    ollama_host=llm_cfg["ollama_host"], ollama_model=llm_cfg["ollama_model"],
                    progress_callback=on_progress,
                )
            progress_bar.progress(1.0, text="Done.")
            st.success(
                f"Refreshed {result['profiles_refreshed']} customer profile(s) "
                f"({result['profiles_skipped']} skipped — not enough logged history yet). "
                f"Checked {result['opportunities_checked']} Suspecting-stage opportunit"
                f"{'y' if result['opportunities_checked'] == 1 else 'ies'}, generated "
                f"{result['insights_generated']} similarity insight"
                f"{'s' if result['insights_generated'] != 1 else ''}."
            )
            st.rerun()

    st.divider()
    st.subheader("Precomputed Results")
    insights = adb.get_all_precomputed_insights()
    if not insights:
        st.info("No precomputed results yet — click **Run Precompute** above to generate them.")
    else:
        last_computed = max(i.computed_at for i in insights)
        st.caption(
            f"Last computed: {last_computed.strftime('%d-%b-%y %H:%M')} UTC — re-run above to "
            f"refresh. {len(insights)} result(s) shown, one per Suspecting-stage opportunity "
            f"that had at least one similar situation found."
        )
        for pi in insights:
            st.write(f"**{pi.customer}**" + (f" — {pi.primary_technology}" if pi.primary_technology else ""))
            insight_dict = {
                "headline": pi.headline,
                "summary": pi.summary,
                "caveat": pi.caveat or "",
                "provider": pi.provider,
                "evidence": json.loads(pi.evidence_json) if pi.evidence_json else [],
            }
            render_similarity_insight_card(insight_dict, pi.alert_log_id)
