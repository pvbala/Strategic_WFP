"""
Step 3 — Path B (RAG / semantic retrieval).

Covers the scenario family the No-RAG rules engine (alert_engine.py)
structurally cannot: Similarity/Analogy (scenarios 6, 7, 8, 11, 12b) and
the ungrounded half of Opportunity/Pipeline (scenario 5). Per the
functional document, this is deliberately narrow in scope — retrieval
surfaces relevant free text as evidence; it never computes or asserts a
number. Any statistic in a rendered insight still comes from Path A.

Vector storage is a plain SQLite table (alerts_db.EmbeddingRecord) with
cosine similarity computed in Python via numpy, not a dedicated vector
database — reasonable at the data volumes this app will see for a while,
and the retrieval interface below (semantic_search) is what a future
pgvector/dedicated-vector-DB migration would need to preserve, not this
file's internals.
"""
import json

import numpy as np

import alert_engine as ae
import alerts_db as adb
import llm_providers as llm

DEFAULT_MIN_SCORE = 0.55
DEFAULT_TOP_K = 5


def _cosine_similarity(a, b):
    a = np.array(a, dtype=float)
    b = np.array(b, dtype=float)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def embed_and_store(text, source_table, source_id, customer=None, primary_technology=None,
                     gemini_key=None, ollama_host=None):
    """Best-effort: called after saving a DemandEvent or Opportunity with
    non-empty notes. Returns the embedding_record id, or None if there's
    no text, no provider configured, or the embedding call failed — a
    failure here should never block the caller's own save from
    succeeding, so callers should wrap this in try/except."""
    if not text or not text.strip():
        return None
    vector, provider, _attempts = llm.embed_with_fallback(text, gemini_key=gemini_key, ollama_host=ollama_host)
    if vector is None:
        return None
    return adb.add_embedding_record(
        source_table=source_table, source_id=source_id, customer=customer,
        primary_technology=primary_technology, text=text,
        embedding_json=json.dumps(vector), embedding_provider=provider,
    )


def semantic_search(query_text, gemini_key=None, ollama_host=None,
                     top_k=DEFAULT_TOP_K, min_score=DEFAULT_MIN_SCORE, exclude_customer=None):
    """Returns (hits, attempts_log). hits is a list of (score,
    EmbeddingRecord) tuples, highest score first, above min_score —
    empty if the query embedding itself failed OR if nothing scored
    above threshold; attempts_log is how a caller tells those two cases
    apart (check whether attempts_log contains a "success" entry) rather
    than silently presenting a provider outage as "no matches found."""
    query_vec, _provider, attempts_log = llm.embed_with_fallback(query_text, gemini_key=gemini_key, ollama_host=ollama_host)
    if query_vec is None:
        return [], attempts_log

    records = adb.get_all_embedding_records()
    scored = []
    for r in records:
        if exclude_customer and r.customer == exclude_customer:
            continue
        try:
            vec = json.loads(r.embedding_json)
        except (json.JSONDecodeError, TypeError):
            continue
        score = _cosine_similarity(query_vec, vec)
        if score >= min_score:
            scored.append((score, r))

    scored.sort(key=lambda x: -x[0])
    return scored[:top_k], attempts_log


def synthesize_similarity_insight(query_context, hits, gemini_key=None, groq_key=None,
                                   ollama_host=None, ollama_model=llm.OLLAMA_DEFAULT_CHAT_MODEL):
    """Turns retrieved snippets into a short, grounded note. The prompt
    explicitly forbids introducing facts not present in the retrieved
    evidence — this is the grounding constraint from the functional
    document applied to the generation step, not just the alerting step.
    Returns (insight_dict_or_None, attempts_log). Never raises."""
    if not hits:
        return None, []

    evidence_lines = "\n".join(
        f"{i+1}. [{r.customer or 'Unknown customer'}] {r.text}"
        for i, (score, r) in enumerate(hits)
    )
    prompt = (
        "You are assisting a workforce planner. Here is the current situation:\n"
        f"{query_context}\n\n"
        "Here are similar past situations found via semantic search. Base your response "
        "ONLY on these — do not invent or assume any fact not stated below:\n"
        f"{evidence_lines}\n\n"
        "Respond with ONLY valid JSON (no markdown fences, no preamble), with exactly these keys:\n"
        '{"headline": "one short sentence", '
        '"summary": "2-3 sentences grounded only in the evidence above", '
        '"caveat": "one short sentence on how confident this is / what is missing"}'
    )

    raw, provider, attempts_log = llm.chat_with_fallback(
        prompt, gemini_key=gemini_key, groq_key=groq_key,
        ollama_host=ollama_host, ollama_model=ollama_model,
    )
    if raw is None:
        return None, attempts_log

    parsed = llm.safe_json_parse(raw)
    if not parsed or "headline" not in parsed:
        attempts_log.append({"provider": provider, "status": "unparseable_json",
                              "detail": f"Provider responded but not with valid JSON: {raw[:200]}"})
        return None, attempts_log

    parsed["provider"] = provider
    parsed["evidence"] = [
        {"customer": r.customer, "text": r.text, "score": round(score, 2)}
        for score, r in hits
    ]
    return parsed, attempts_log


# ---------------------------------------------------------------- Hybrid (keyword + semantic) search

def hybrid_search(query_text, gemini_key=None, ollama_host=None, top_k=DEFAULT_TOP_K,
                   min_score=DEFAULT_MIN_SCORE, exclude_customer=None):
    """Combines FTS5 keyword search (exact/near-exact term matches — an
    exact customer or technology name) with semantic search (fuzzy,
    meaning-based matches — "budget freeze" vs "finance hasn't approved
    the PO"). Neither alone is sufficient: pure semantic search can rank
    a literal exact match below a vaguely-related one, since embeddings
    optimize for meaning, not precision; pure keyword search misses
    anything phrased differently. Returns (hits, attempts_log) — hits is
    a list of (score, EmbeddingRecord, match_type) tuples, match_type one
    of "keyword", "semantic", "both". Keyword hits get a high baseline
    score (ranked by FTS position, not literally comparable to cosine
    similarity) since an exact term match is strong evidence on its own,
    independent of embedding distance. To feed these into
    synthesize_similarity_insight (which expects (score, record)
    2-tuples), strip match_type: [(s, r) for s, r, t in hits]."""
    keyword_records = adb.keyword_search(query_text, top_k=top_k)
    if exclude_customer:
        keyword_records = [r for r in keyword_records if r.customer != exclude_customer]

    semantic_hits, attempts_log = semantic_search(
        query_text, gemini_key=gemini_key, ollama_host=ollama_host,
        top_k=top_k, min_score=min_score, exclude_customer=exclude_customer,
    )

    combined = {}
    for i, r in enumerate(keyword_records):
        combined[r.id] = {"record": r, "score": max(0.9 - i * 0.02, 0.5), "type": "keyword"}
    for score, r in semantic_hits:
        if r.id in combined:
            combined[r.id]["type"] = "both"
            combined[r.id]["score"] = max(combined[r.id]["score"], score)
        else:
            combined[r.id] = {"record": r, "score": score, "type": "semantic"}

    ranked = sorted(combined.values(), key=lambda x: -x["score"])[:top_k]
    hits = [(item["score"], item["record"], item["type"]) for item in ranked]
    return hits, attempts_log


# ---------------------------------------------------------------- Customer profile bridge (Path A -> Path B)

def refresh_customer_profile_embedding(customer, gemini_key=None, ollama_host=None):
    """Regenerates the embedded behavior-profile summary for one
    customer — delete-then-insert, since embeddings aren't editable in
    place. This is what lets semantic/keyword search surface Path A
    findings ("postponed 4 of 6 times") by meaning, which raw free-text
    notes alone can't do. Returns the new embedding_record id, or None
    if the customer doesn't clear the minimum sample size yet (nothing
    to embed) or the embedding call failed."""
    adb.delete_embeddings_by_source("customer_profile", customer=customer)
    text = ae.summarize_customer_profile_text(customer)
    if text is None:
        return None
    return embed_and_store(
        text, "customer_profile", 0, customer=customer, primary_technology=None,
        gemini_key=gemini_key, ollama_host=ollama_host,
    )


def refresh_all_customer_profile_embeddings(gemini_key=None, ollama_host=None):
    """Batch version, used by the Precompute Similar Situations page.
    Refreshes every customer with at least one logged DemandEvent —
    refresh_customer_profile_embedding is a no-op (returns None) for any
    customer below the minimum sample size, so this is safe to call
    broadly rather than needing to pre-filter. Returns
    {"refreshed": n, "skipped": n}."""
    customers = adb.distinct_event_customers()
    refreshed, skipped = 0, 0
    for customer in customers:
        result = refresh_customer_profile_embedding(customer, gemini_key=gemini_key, ollama_host=ollama_host)
        if result is not None:
            refreshed += 1
        else:
            skipped += 1
    return {"refreshed": refreshed, "skipped": skipped}


# ---------------------------------------------------------------- Feedback logging for Path B insights

def log_shown_insight(insight, customer=None, primary_technology=None, plan_id=None):
    """Persists a Path B insight that was actually shown to a planner —
    the family="similarity_rag" counterpart to alert_engine.log_shown_alert
    (family="customer_behavior"). Enables the same Useful/Not Useful
    feedback loop Path A alerts already have, which Path B insights were
    previously missing entirely. customer is required by the AlertLog
    schema even for ad hoc, no-specific-customer queries (Similarity
    Search) — a placeholder is used rather than leaving it blank."""
    return adb.log_alert(
        customer=customer or "(ad hoc query)",
        primary_technology=primary_technology,
        granularity="customer_tech" if primary_technology else "customer",
        risk_level="Info",
        headline=insight["headline"],
        evidence_json=json.dumps(insight["evidence"]),
        recommendation=insight["summary"],
        confidence="Unverified (AI)",
        sample_size=len(insight["evidence"]),
        plan_id=plan_id,
        family="similarity_rag",
    )


# ---------------------------------------------------------------- Precompute Similar Situations (batch)

def run_precompute_similar_situations(gemini_key=None, groq_key=None, ollama_host=None,
                                       ollama_model=llm.OLLAMA_DEFAULT_CHAT_MODEL,
                                       progress_callback=None):
    """Batch job backing the Precompute Similar Situations page. Two
    phases:
      1. Refresh every customer's behavior-profile embedding (bridges
         Path A findings into the searchable corpus — see
         refresh_all_customer_profile_embeddings).
      2. For every Opportunity currently in Suspecting stage (across all
         customers and all Workforce Plans — Opportunity has no plan
         scoping at all, consistent with how Path A/B already treat
         customer history as cross-plan), run a hybrid search excluding
         that opportunity's own customer, and synthesize an insight if
         matches are found. Each generated insight is logged to
         AlertLog (via log_shown_insight) exactly once here, at compute
         time, so the feedback buttons on the results page don't need to
         re-log on every view.

    Replaces the entire precomputed_insight table with fresh results
    each run — not incremental, so results always reflect the data as of
    the last run, with no stale entries lingering from opportunities that
    have since changed stage or been deleted.

    progress_callback, if given, is called as (index, total) after each
    opportunity is processed, so the caller can render progress in the
    UI. Returns a summary dict: profiles_refreshed, profiles_skipped,
    opportunities_checked, insights_generated."""
    profile_result = refresh_all_customer_profile_embeddings(gemini_key=gemini_key, ollama_host=ollama_host)

    opportunities = [o for o in adb.get_all_opportunities() if o.stage == "Suspecting"]
    adb.clear_precomputed_insights()

    insights_generated = 0
    for i, opp in enumerate(opportunities):
        query_parts = [opp.customer]
        if opp.primary_technology:
            query_parts.append(opp.primary_technology)
        if opp.notes:
            query_parts.append(opp.notes)
        query_text = " — ".join(query_parts)

        hits, _search_attempts = hybrid_search(
            query_text, gemini_key=gemini_key, ollama_host=ollama_host, exclude_customer=opp.customer,
        )
        if hits:
            hits_2tuple = [(s, r) for s, r, _t in hits]
            insight, _synth_attempts = synthesize_similarity_insight(
                f"Opportunity: {query_text}", hits_2tuple,
                gemini_key=gemini_key, groq_key=groq_key, ollama_host=ollama_host, ollama_model=ollama_model,
            )
            if insight:
                log_id = log_shown_insight(insight, customer=opp.customer, primary_technology=opp.primary_technology)
                adb.add_precomputed_insight(
                    customer=opp.customer, primary_technology=opp.primary_technology,
                    headline=insight["headline"], summary=insight["summary"], caveat=insight.get("caveat"),
                    evidence_json=json.dumps(insight["evidence"]), provider=insight.get("provider"),
                    source_opportunity_id=opp.id, alert_log_id=log_id,
                )
                insights_generated += 1

        if progress_callback:
            progress_callback(i + 1, len(opportunities))

    return {
        "profiles_refreshed": profile_result["refreshed"],
        "profiles_skipped": profile_result["skipped"],
        "opportunities_checked": len(opportunities),
        "insights_generated": insights_generated,
    }
