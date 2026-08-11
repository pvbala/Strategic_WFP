import streamlit as st

import ask_router
import rag_engine as rag
from common import bootstrap, any_llm_configured, get_llm_config, render_llm_attempts_debug, render_similarity_insight_card, render_alert_card

sys_cfg, plan, L = bootstrap("Similarity Search")

st.header("Similarity Search")

AI_AVAILABLE = any_llm_configured()
llm_cfg = get_llm_config()
ASK_PROVIDERS_AVAILABLE = [p for p in ["gemini", "groq"] if llm_cfg[f"{p}_key"]]  # Ollama excluded — see ask_router.py
ASK_AVAILABLE = bool(ASK_PROVIDERS_AVAILABLE)

MATCH_TYPE_LABEL = {"keyword": "🔑 keyword", "semantic": "🧠 semantic", "both": "🔑🧠 keyword + semantic"}
PROVIDER_LABEL = {"gemini": "Gemini", "groq": "Groq"}

if not AI_AVAILABLE:
    st.warning(
        "No AI provider configured — this page needs one to work. Set up Gemini, Groq, or "
        "Ollama in AI Settings first."
    )
else:
    mode_options = ["Search only"]
    if ASK_AVAILABLE:
        mode_options.insert(0, "Ask")
    mode = st.radio("Mode", mode_options, horizontal=True, key="f13_mode",
                     help="Ask lets the model pick the right answer source automatically — exact "
                          "customer stats, exact estimation-bias stats, or a fuzzy note search. "
                          "Search only always does the fuzzy note search directly, same as before.")
    if not ASK_AVAILABLE:
        st.caption("Ask mode requires Gemini or Groq specifically (tool calling isn't verified for Ollama yet) — currently unavailable.")

    # ---------------------------------------------------------------- Ask mode
    if mode == "Ask":
        st.caption(
            "Type any question — the model decides whether it needs exact customer stats, "
            "exact estimation-bias stats, a fuzzy note search, or none of those. One decision "
            "per question — it does not chain multiple lookups together."
        )

        if len(ASK_PROVIDERS_AVAILABLE) > 1:
            ask_provider = st.selectbox(
                "Provider for this Ask session", ASK_PROVIDERS_AVAILABLE,
                format_func=lambda p: PROVIDER_LABEL[p], key="f13_ask_provider",
                help="Sticks for this session — every question you ask uses this provider, with "
                     "no automatic switching to another one if it fails. Change it here anytime.",
            )
        else:
            ask_provider = ASK_PROVIDERS_AVAILABLE[0]
            st.caption(f"Provider: {PROVIDER_LABEL[ask_provider]} (only one configured — set up another in AI Settings to choose between them).")

        question = st.text_area(
            "Ask a question",
            placeholder='e.g. "Does Alpha Bank have a history of postponing?" or "Is EP usually overestimated?"',
            key="f13_ask_question",
        )
        if st.button("Ask", type="primary", disabled=not question.strip()):
            with st.spinner("Thinking..."):
                result = ask_router.answer_question(
                    question, provider=ask_provider, gemini_key=llm_cfg["gemini_key"], groq_key=llm_cfg["groq_key"],
                    ollama_host=llm_cfg["ollama_host"], ollama_model=llm_cfg["ollama_model"],
                )
            st.session_state["f13_ask_result"] = result

        result = st.session_state.get("f13_ask_result")
        if result:
            source = result["source"]

            if source == "error":
                st.error(result["message"])

            elif source == "direct":
                st.info(result["text"])
                st.caption("General answer — not looked up from your logged data.")

            elif source == "path_a_alerts":
                st.caption(f"Routed to: exact customer statistics (Path A) — customer = {result['args'].get('customer')}")
                if not result["alerts"]:
                    st.info(
                        "No alert currently triggers for this customer — either not enough logged "
                        "history yet, or their adverse rate is below threshold. That's a real answer, "
                        "not a failure to find data."
                    )
                else:
                    for alert, log_id in zip(result["alerts"], result["log_ids"]):
                        render_alert_card(alert, log_id)

            elif source == "path_a_bias":
                st.caption(f"Routed to: exact estimation-bias statistics (Path A) — metric = {result['args'].get('metric')}")
                bias = result["bias"]
                if not bias:
                    st.info("No actuals logged yet for that metric/customer combination — nothing to compute a bias from.")
                else:
                    scope = bias["customer"] or "Portfolio-wide"
                    st.markdown(f"**{scope} — {bias['metric']}**")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Sample size", bias["sample_size"])
                    c2.metric("Avg variance", f"{bias['avg_variance_pct']}%")
                    c3.metric("Direction", bias["direction"])
                    st.caption("Source: Path A (structured) — computed directly from logged actuals, not AI-generated.")

            elif source == "path_b":
                st.caption(f"Routed to: fuzzy note search (Path B) — query = \"{result['args'].get('query')}\"")
                if not result["hits"]:
                    search_succeeded = any(a["status"] == "success" for a in result["attempts"])
                    if search_succeeded:
                        st.info("No matches found for that search.")
                    else:
                        st.warning("The search couldn't complete — see details below.")
                        render_llm_attempts_debug(result["attempts"])
                else:
                    if result["insight"]:
                        render_similarity_insight_card(result["insight"], result["log_id"])
                    else:
                        st.warning("Found matching notes but couldn't generate a summary — see details below.")
                        render_llm_attempts_debug(result["attempts"])
                    with st.expander("Retrieved evidence"):
                        for score, r, match_type in result["hits"]:
                            st.write(f"**{r.customer or 'Unknown customer'}**" + (f" — {r.primary_technology}" if r.primary_technology else ""))
                            st.write(r.text)
                            st.caption(f"{MATCH_TYPE_LABEL.get(match_type, match_type)} · similarity {round(score, 2)} · Source: {r.source_table}")

    # ---------------------------------------------------------------- Search only mode (unchanged)
    else:
        st.caption(
            "Ask a free-form question about anything logged in this app — a demand event or an "
            "opportunity, for any customer, across every Workforce Plan (not just the active one). "
            "This is Path B (RAG): hybrid keyword + semantic retrieval, so it catches both exact "
            "terms and fuzzy meaning matches. Always searches — does not decide whether an exact "
            "stats lookup would answer better; use Ask mode for that."
        )
        query = st.text_area(
            "What are you trying to find?",
            placeholder='e.g. "has any customer mentioned currency or FX issues as a reason for delay?"',
            key="f13_query",
        )
        min_score = st.slider("Minimum semantic similarity", 0.3, 0.9, rag.DEFAULT_MIN_SCORE, 0.05, key="f13_min_score",
                               help="Only affects the semantic half of the search — keyword (exact term) matches are always included regardless of this setting.")

        if st.button("Search", type="primary", disabled=not query.strip()):
            with st.spinner("Searching..."):
                hits, search_attempts = rag.hybrid_search(
                    query, gemini_key=llm_cfg["gemini_key"], ollama_host=llm_cfg["ollama_host"],
                    top_k=8, min_score=min_score,
                )
            st.session_state["f13_hits"] = hits
            st.session_state["f13_search_attempts"] = search_attempts
            st.session_state["f13_last_query"] = query
            st.session_state.pop("f13_logid", None)

        hits = st.session_state.get("f13_hits")
        if hits is not None:
            if not hits:
                search_succeeded = any(a["status"] == "success" for a in st.session_state.get("f13_search_attempts", []))
                if search_succeeded:
                    st.info(
                        "No matches. Try lowering the minimum similarity, rephrasing the question, "
                        "or check whether relevant events/opportunities have notes logged at all — "
                        "this only searches text that was entered with an AI provider configured at "
                        "the time (or a customer profile summary, if one has been precomputed — see "
                        "Precompute Similar Situations)."
                    )
                else:
                    st.warning("The search couldn't complete — see details below.")
                    render_llm_attempts_debug(st.session_state.get("f13_search_attempts", []))
            else:
                st.success(f"{len(hits)} matching note(s) found.")
                if "f13_logid" not in st.session_state:
                    with st.spinner("Summarizing..."):
                        hits_2tuple = [(s, r) for s, r, _t in hits]
                        insight, synth_attempts = rag.synthesize_similarity_insight(
                            st.session_state.get("f13_last_query", query), hits_2tuple,
                            gemini_key=llm_cfg["gemini_key"], groq_key=llm_cfg["groq_key"],
                            ollama_host=llm_cfg["ollama_host"], ollama_model=llm_cfg["ollama_model"],
                        )
                    st.session_state["f13_insight"] = insight
                    st.session_state["f13_synth_attempts"] = synth_attempts
                    if insight:
                        st.session_state["f13_logid"] = rag.log_shown_insight(insight, customer=None)

                insight = st.session_state.get("f13_insight")
                if insight:
                    render_similarity_insight_card(insight, st.session_state.get("f13_logid"))
                else:
                    st.warning("Found matching notes but couldn't generate a summary — see raw evidence below.")
                    render_llm_attempts_debug(st.session_state.get("f13_synth_attempts", []))

                st.divider()
                st.subheader("Retrieved evidence")
                for score, r, match_type in hits:
                    with st.container(border=True):
                        st.write(f"**{r.customer or 'Unknown customer'}**" + (f" — {r.primary_technology}" if r.primary_technology else ""))
                        st.write(r.text)
                        st.caption(
                            f"{MATCH_TYPE_LABEL.get(match_type, match_type)} · similarity {round(score, 2)} · "
                            f"Source: {r.source_table} · Embedded via {r.embedding_provider or 'unknown'}"
                        )