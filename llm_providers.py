"""
Step 3 — LLM provider layer.

Provider fallback chain: Gemini (primary) -> Groq (fallback 1) -> Ollama
(fallback 2, local-only). Every function here is a pure function — API
keys and hosts are passed in explicitly, never read from a global or from
st.session_state directly, so this module has no Streamlit dependency and
can be unit-tested (and monkeypatched) without a running app.

NOTE ON NAMING: "Groq" (groq.com, fast LPU-based inference) and "Grok"
(x.ai, Elon Musk's chatbot) are two different companies with easily
confused names. This module uses Groq — confirm that's actually the
provider you have a key for before assuming a failure is a bug here
rather than a wrong-provider key.

IMPORTANT — untested against live APIs: this sandbox's network egress is
allowlisted to a fixed set of domains (pypi, npm, github, etc.) and does
not include generativelanguage.googleapis.com, api.x.ai, or a reachable
Ollama host. The request shapes below are written from API documentation,
not verified against a live call. Test against real keys before trusting
this in production, particularly the exact response-parsing paths.

Design principles carried over from the functional document:
- Every call logs which provider actually answered (for the caller to
  surface/track — this module returns the provider name alongside every
  result, it doesn't log anything itself).
- Structured output is requested and parsed defensively; a provider
  returning malformed JSON is treated as a failure and falls through to
  the next tier, not a partial success.
- Aggressive per-provider timeouts, so a full fall-through across all
  three tiers doesn't stall the Streamlit UI for too long.
- Claude is deliberately NOT part of this automatic fallback chain (no
  ongoing free tier) — see the functional document, Section 8.1.
"""
import json
import re

import requests

TIMEOUT_SECONDS = 12

GEMINI_CHAT_MODEL = "gemini-3.6-flash"
GEMINI_EMBED_MODEL = "gemini-embedding-001"
GROQ_CHAT_MODEL = "openai/gpt-oss-20b"
OLLAMA_DEFAULT_CHAT_MODEL = "llama3"
OLLAMA_DEFAULT_EMBED_MODEL = "nomic-embed-text"


# ---------------------------------------------------------------- Chat / extraction calls

def call_gemini_chat(prompt, api_key):
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_CHAT_MODEL}:generateContent?key={api_key}"
    )
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    resp = requests.post(url, json=body, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def call_gemini_with_tools(prompt, api_key, tool_declarations):
    """Single-round function calling — Gemini only for now (see
    ask_router.py for why). Sends the question plus a list of available
    tools (name/description/parameter schema); the model either answers
    directly or requests exactly one tool call. Returns:
      {"type": "function_call", "name": ..., "args": {...}}  or
      {"type": "text", "text": ...}
    Never chains — this function makes exactly one API call and returns
    whatever that single round produced. Multi-round chaining (using a
    tool's result to decide the next call) is a deliberately separate,
    not-yet-built capability."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_CHAT_MODEL}:generateContent?key={api_key}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"functionDeclarations": tool_declarations}],
    }
    resp = requests.post(url, json=body, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    parts = data["candidates"][0]["content"]["parts"]
    for part in parts:
        if "functionCall" in part:
            fc = part["functionCall"]
            return {"type": "function_call", "name": fc["name"], "args": fc.get("args", {})}
    text = "".join(p.get("text", "") for p in parts)
    return {"type": "text", "text": text}


def call_groq_chat(prompt, api_key):
    """Groq (groq.com — fast LPU-based inference), not to be confused
    with Grok/xAI (x.ai). Easy mix-up: identical pronunciation, near-
    identical spelling, completely different company/API/base URL. If
    this call 400s with "model not found," Groq has deprecated the
    model again (they retire models faster than most providers) — check
    the Groq console's model list."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": GROQ_CHAT_MODEL, "messages": [{"role": "user", "content": prompt}]}
    resp = requests.post(url, headers=headers, json=body, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def call_groq_with_tools(prompt, api_key, tool_declarations):
    """Single-round function calling via Groq — OpenAI-compatible tool
    format (Groq's API mirrors OpenAI's chat completions shape, not
    Gemini's). tool_declarations uses the same flat shape
    call_gemini_with_tools takes (name/description/parameters per tool)
    — this function wraps them into OpenAI's nested
    {"type": "function", "function": {...}} shape internally, so
    ask_router.py maintains one tool list, not two.

    Two real differences from Gemini's tool-calling response worth
    knowing if this ever needs debugging:
    - Groq returns tool_calls as a list (Gemini returns a single
      functionCall) — only the first entry is used, consistent with
      single-round scope.
    - Groq's function arguments arrive as a JSON-encoded STRING that
      must be parsed, not a dict already, unlike Gemini's args field."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    tools_payload = [
        {"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["parameters"],
        }}
        for t in tool_declarations
    ]
    body = {
        "model": GROQ_CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "tools": tools_payload,
    }
    resp = requests.post(url, headers=headers, json=body, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    message = data["choices"][0]["message"]
    tool_calls = message.get("tool_calls")
    if tool_calls:
        call = tool_calls[0]
        try:
            args = json.loads(call["function"]["arguments"])
        except (json.JSONDecodeError, TypeError):
            args = {}
        return {"type": "function_call", "name": call["function"]["name"], "args": args}
    return {"type": "text", "text": message.get("content") or ""}





def call_ollama_chat(prompt, host, model=OLLAMA_DEFAULT_CHAT_MODEL):
    url = f"{host.rstrip('/')}/api/generate"
    body = {"model": model, "prompt": prompt, "stream": False}
    resp = requests.post(url, json=body, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    return data["response"]


def chat_with_fallback(prompt, gemini_key=None, groq_key=None, ollama_host=None,
                        ollama_model=OLLAMA_DEFAULT_CHAT_MODEL):
    """Tries each configured provider in order (Gemini -> Groq -> Ollama).
    A provider is skipped if its credential/host wasn't supplied — this is
    how "no key entered" degrades gracefully rather than erroring.
    Returns (raw_text, provider_name, attempts) — attempts is a list of
    {"provider", "status", "detail"} dicts, one per provider actually
    tried, in order, so a caller can show the real reason each one
    failed instead of a bare "extraction failed." On total failure,
    raw_text and provider_name are both None."""
    attempts_to_make = []
    if gemini_key:
        attempts_to_make.append(("gemini", lambda: call_gemini_chat(prompt, gemini_key)))
    if groq_key:
        attempts_to_make.append(("groq", lambda: call_groq_chat(prompt, groq_key)))
    if ollama_host:
        attempts_to_make.append(("ollama", lambda: call_ollama_chat(prompt, ollama_host, ollama_model)))

    attempts_log = []
    for name, fn in attempts_to_make:
        try:
            result = fn()
            if result and result.strip():
                attempts_log.append({"provider": name, "status": "success", "detail": None})
                return result, name, attempts_log
            attempts_log.append({"provider": name, "status": "empty_response", "detail": "Provider returned an empty response."})
        except Exception as e:
            attempts_log.append({"provider": name, "status": "error", "detail": _describe_exception(e)})
            continue  # fall through to next tier
    return None, None, attempts_log


def _describe_exception(e):
    """Turns a requests exception into a short, actionable message —
    HTTP errors include the status code and response body (truncated),
    which is almost always where the real cause (invalid key, wrong
    model name, quota exceeded, connection refused) is visible."""
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        body = e.response.text[:300] if e.response.text else ""
        return f"HTTP {e.response.status_code}: {body}"
    if isinstance(e, requests.exceptions.ConnectionError):
        return f"Connection failed (host unreachable or not running): {str(e)[:200]}"
    if isinstance(e, requests.exceptions.Timeout):
        return f"Timed out after {TIMEOUT_SECONDS}s"
    return f"{type(e).__name__}: {str(e)[:250]}"


def safe_json_parse(raw_text):
    """LLMs frequently wrap JSON in markdown fences or add preamble text
    despite instructions not to. Strips common wrapping before parsing;
    returns None (not an exception) on failure, so callers can treat a
    malformed response as "this provider failed" and move on."""
    if not raw_text:
        return None
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------- Embeddings

def embed_gemini(text, api_key):
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_EMBED_MODEL}:embedContent?key={api_key}"
    )
    body = {"content": {"parts": [{"text": text}]}}
    resp = requests.post(url, json=body, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    return data["embedding"]["values"]


def embed_ollama(text, host, model=OLLAMA_DEFAULT_EMBED_MODEL):
    url = f"{host.rstrip('/')}/api/embeddings"
    body = {"model": model, "prompt": text}
    resp = requests.post(url, json=body, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    return data["embedding"]


def embed_with_fallback(text, gemini_key=None, ollama_host=None, ollama_model=OLLAMA_DEFAULT_EMBED_MODEL):
    """Groq has no public embeddings endpoint at the
    time of writing, so the embedding chain is Gemini -> Ollama only (two
    tiers, not three). Returns (vector, provider_name, attempts) — see
    chat_with_fallback for the attempts log shape."""
    attempts_to_make = []
    if gemini_key:
        attempts_to_make.append(("gemini", lambda: embed_gemini(text, gemini_key)))
    if ollama_host:
        attempts_to_make.append(("ollama", lambda: embed_ollama(text, ollama_host, ollama_model)))

    attempts_log = []
    for name, fn in attempts_to_make:
        try:
            vector = fn()
            if vector:
                attempts_log.append({"provider": name, "status": "success", "detail": None})
                return vector, name, attempts_log
            attempts_log.append({"provider": name, "status": "empty_response", "detail": "Provider returned an empty embedding."})
        except Exception as e:
            attempts_log.append({"provider": name, "status": "error", "detail": _describe_exception(e)})
            continue
    return None, None, attempts_log


# ---------------------------------------------------------------- Connectivity checks (for the settings page)

def test_gemini(api_key):
    try:
        result = call_gemini_chat("Reply with the single word: OK", api_key)
        return True, result[:80]
    except Exception as e:
        return False, _describe_exception(e)


def test_groq(api_key):
    try:
        result = call_groq_chat("Reply with the single word: OK", api_key)
        return True, result[:80]
    except Exception as e:
        return False, _describe_exception(e)


def test_ollama(host, model=OLLAMA_DEFAULT_CHAT_MODEL):
    try:
        result = call_ollama_chat("Reply with the single word: OK", host, model)
        return True, result[:80]
    except Exception as e:
        return False, _describe_exception(e)


# ---------------------------------------------------------------- Structured extraction (Log a Demand Event, AI mode)

EXTRACTABLE_EVENT_TYPES = ["Postponed", "Reduced", "Cancelled", "Increased", "On Time", "Accelerated"]


def extract_event_fields(raw_text, gemini_key=None, groq_key=None, ollama_host=None,
                          ollama_model=OLLAMA_DEFAULT_CHAT_MODEL):
    """Parses a free-text description of a demand-plan change into the
    same structured fields the manual form collects. Dates are
    deliberately NOT extracted as absolute values — models are unreliable
    at inferring real calendar dates from relative phrasing ("in 6
    weeks") without today's date and full context, and a wrong absolute
    date is worse than no date. estimated_shift_days is returned instead,
    as a hint the UI can show alongside a manually-picked date field.
    Returns (parsed_dict_or_None, provider_name_or_None, attempts_log).
    This function never writes anything to the database — the caller is
    responsible for presenting the result for human review before any
    save."""
    prompt = (
        "Extract structured fields from this description of a change to a workforce demand "
        "plan. Respond with ONLY valid JSON (no markdown fences, no preamble), with exactly "
        "these keys:\n"
        '{"event_type": one of ' + str(EXTRACTABLE_EVENT_TYPES) + ', '
        '"estimated_shift_days": integer or null (only if a postponement/acceleration timeframe '
        'is mentioned, e.g. "6 weeks" -> 42), '
        '"qty_before": integer or null, "qty_after": integer or null, '
        '"reason_category": a short phrase describing the reason, or null, '
        '"confidence": "High", "Medium", or "Low"}\n\n'
        f'Text: "{raw_text}"'
    )
    raw, provider, attempts_log = chat_with_fallback(
        prompt, gemini_key=gemini_key, groq_key=groq_key,
        ollama_host=ollama_host, ollama_model=ollama_model,
    )
    if raw is None:
        return None, None, attempts_log
    parsed = safe_json_parse(raw)
    if not parsed or "event_type" not in parsed:
        attempts_log.append({"provider": provider, "status": "unparseable_json",
                              "detail": f"Provider responded but not with valid JSON: {raw[:200]}"})
        return None, None, attempts_log
    return parsed, provider, attempts_log