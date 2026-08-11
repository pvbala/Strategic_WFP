"""
Step 4 — single-round tool calling ("Ask").

Given a free-form question, lets the model pick at most one tool from a
small, deliberately narrow set (see TOOLS below), execute it once, and
return a result the UI renders according to its source — never lets the
model restate or recompute a number a tool already returned, same
grounding discipline as everywhere else in this app.

Explicitly NOT multi-round: this makes exactly one tool-selection
decision per question. It never looks at a tool's result and decides to
call a second tool based on it. That's a deliberately separate,
not-yet-built capability (chaining, e.g. "look up this customer's stats,
then search using what was found") — see the conversation this was
scoped from for the reasoning and concrete scenarios that would need it.

Gemini-only for now: tool-calling response shapes differ meaningfully
across providers, unlike plain chat (uniform enough for
chat_with_fallback's shared fallback chain). Groq/Ollama tool-calling
support has not been verified against this app's usage. If Gemini isn't
configured, Ask mode is unavailable — Similarity Search still works via
its existing Search-only mode, same graceful-degradation pattern as
every other AI feature here.
"""
import alert_engine as ae
import llm_providers as llm
import rag_engine as rag

TOOLS = [
    {
        "name": "get_customer_behavior_alerts",
        "description": (
            "Exact, structured postponement/reduction/cancellation statistics for one "
            "customer, computed from logged demand events. Use for questions about a "
            "specific customer's reliability, postponement rate, or delivery risk."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer": {"type": "string", "description": "Customer name, exact match to master data"},
                "primary_technology": {"type": "string", "description": "Optional — narrows to one technology"},
            },
            "required": ["customer"],
        },
    },
    {
        "name": "compute_estimation_bias",
        "description": (
            "Exact variance between planned and actual values for one estimation metric. "
            "Use for questions about whether the team over- or under-estimates a specific "
            "metric, either for one customer or across the whole portfolio."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "description": "One of: Attrition, Rampdown, Release to Other Accounts, EP, BA, Internal, Trainee, Onsite Demand",
                },
                "customer": {"type": "string", "description": "Optional — narrows to one customer; omit for portfolio-wide"},
            },
            "required": ["metric"],
        },
    },
    {
        "name": "search_similar_situations",
        "description": (
            "Fuzzy hybrid (keyword + semantic) search over free-text notes logged across "
            "the whole system, for situations resembling a description. Use for open-ended "
            "questions that aren't about one customer's exact numbers — informal signals, "
            "cross-customer patterns, or 'has anything like this happened before.'"
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search text"}},
            "required": ["query"],
        },
    },
]


PROVIDERS_SUPPORTING_TOOLS = ["gemini", "groq"]  # Ollama excluded — see module docstring


def answer_question(question, provider, gemini_key=None, groq_key=None, ollama_host=None,
                     ollama_model=llm.OLLAMA_DEFAULT_CHAT_MODEL):
    """provider: "gemini" or "groq" — picked once by the person for their
    Ask session (see the provider selector on the Similarity Search
    page), not decided automatically. No fallback between them: if the
    chosen provider fails, this returns an error rather than silently
    trying the other one — switching providers underneath the person
    without telling them would be more surprising than useful, and
    dropped the merged two-provider attempts_log this needed when it
    used to fall back automatically.

    Embeddings are the one exception carved out of "only use the chosen
    provider": Groq has no embeddings API at all, so the
    search_similar_situations tool always uses Gemini (if configured)
    for the embedding step regardless of which provider was chosen for
    routing/synthesis — this isn't a fallback, it's Groq structurally
    being unable to do that part no matter what's selected.

    Returns a dict tagged by source so the UI can render each
    differently and never blur AI-synthesized text with grounded
    structured numbers:
      {"source": "path_a_alerts", "alerts": [...], "log_ids": [...]}
      {"source": "path_a_bias", "bias": {...} or None, "args": {...}}
      {"source": "path_b", "insight": {...} or None, "hits": [...], "log_id": ..., "attempts": [...]}
      {"source": "direct", "text": "..."}
      {"source": "error", "message": "..."}
    """
    if provider == "gemini":
        if not gemini_key:
            return {"source": "error", "message": "Gemini is not configured — set a key in AI Settings first."}
        call_fn = lambda: llm.call_gemini_with_tools(question, gemini_key, TOOLS)
    elif provider == "groq":
        if not groq_key:
            return {"source": "error", "message": "Groq is not configured — set a key in AI Settings first."}
        call_fn = lambda: llm.call_groq_with_tools(question, groq_key, TOOLS)
    else:
        return {"source": "error", "message": f"Unsupported provider for Ask mode: {provider}"}

    try:
        decision = call_fn()
    except Exception as e:
        return {"source": "error", "message": llm._describe_exception(e)}

    if decision["type"] == "text":
        return {"source": "direct", "text": decision["text"]}

    name = decision["name"]
    args = decision["args"]

    if name == "get_customer_behavior_alerts":
        customer = args.get("customer")
        if not customer:
            return {"source": "error", "message": "Model requested a customer lookup without naming a customer."}
        tech = args.get("primary_technology") or None
        alerts = ae.get_customer_behavior_alerts(customer, primary_technology=tech)
        log_ids = [ae.log_shown_alert(a, plan_id=None) for a in alerts]
        return {"source": "path_a_alerts", "args": args, "alerts": alerts, "log_ids": log_ids}

    if name == "compute_estimation_bias":
        metric = args.get("metric")
        if not metric:
            return {"source": "error", "message": "Model requested an estimation-bias lookup without naming a metric."}
        customer = args.get("customer") or None
        bias = ae.compute_estimation_bias(metric, customer=customer)
        return {"source": "path_a_bias", "args": args, "bias": bias}

    if name == "search_similar_situations":
        query = args.get("query") or question
        # Embedding step: Gemini only (Groq can't embed), regardless of
        # which provider was chosen above for the routing decision.
        embed_gemini_key = gemini_key  # available whenever configured, independent of `provider`
        hits, search_attempts = rag.hybrid_search(query, gemini_key=embed_gemini_key, ollama_host=ollama_host)
        insight, synth_attempts, log_id = None, [], None
        if hits:
            hits_2tuple = [(s, r) for s, r, _t in hits]
            # Synthesis text-generation step respects the chosen provider —
            # only that provider's key is passed through, so this stays
            # consistent with "the whole Ask session uses one provider,"
            # same as the routing decision above.
            synth_gemini_key = gemini_key if provider == "gemini" else None
            synth_groq_key = groq_key if provider == "groq" else None
            insight, synth_attempts = rag.synthesize_similarity_insight(
                query, hits_2tuple, gemini_key=synth_gemini_key, groq_key=synth_groq_key,
                ollama_host=ollama_host, ollama_model=ollama_model,
            )
            if insight:
                log_id = rag.log_shown_insight(insight, customer=None)
        return {

            "source": "path_b", "args": args, "hits": hits, "insight": insight,
            "log_id": log_id, "attempts": search_attempts + synth_attempts,
        }

    return {"source": "error", "message": f"Model requested an unrecognized tool: {name}"}