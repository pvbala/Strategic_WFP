import streamlit as st

import llm_providers as llm
from common import bootstrap

sys_cfg, plan, L = bootstrap("AI Settings")

st.header("AI Settings")
st.caption(
    "Configure the LLM providers used for free-text event capture (Log a Demand Event) and "
    "the AI-powered similarity search (Similarity Search, Opportunity Pipeline). Every AI "
    "feature in this app degrades gracefully to its structured-only behavior when no provider "
    "is configured — nothing here is required for the core app to work."
)
st.warning(
    "🔒 Keys are held only in this browser session's memory (`st.session_state`) — never "
    "written to disk, never logged, and cleared the moment you close or refresh this session. "
    "You will need to re-enter them each new session."
)

st.subheader("Provider chain")
st.caption(
    "Chat/extraction: Gemini → Groq → Ollama, in that order. Embeddings (for similarity "
    "search): Gemini → Ollama (Groq has no public embeddings endpoint at "
    "the time of writing). Ollama only works when this app is running locally — it is "
    "unreachable from Streamlit Community Cloud, so the effective chain there is Gemini → Groq."
)

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Gemini**")
    gemini_key_input = st.text_input(
        "Gemini API Key", value=st.session_state.get("gemini_api_key", ""),
        type="password", key="gemini_key_input",
    )
    if st.button("Save Gemini Key"):
        st.session_state["gemini_api_key"] = gemini_key_input
        st.success("Saved for this session.")
    if st.button("Test Gemini Connection"):
        key = st.session_state.get("gemini_api_key", "")
        if not key:
            st.error("No key saved yet.")
        else:
            with st.spinner("Testing..."):
                ok, detail = llm.test_gemini(key)
            (st.success if ok else st.error)(f"{'OK' if ok else 'Failed'}: {detail}")

with c2:
    st.markdown("**Groq**")
    st.caption("Not to be confused with Grok/xAI — different company, different key.")
    groq_key_input = st.text_input(
        "Groq API Key", value=st.session_state.get("groq_api_key", ""),
        type="password", key="groq_key_input",
    )
    if st.button("Save Groq Key"):
        st.session_state["groq_api_key"] = groq_key_input
        st.success("Saved for this session.")
    if st.button("Test Groq Connection"):
        key = st.session_state.get("groq_api_key", "")
        if not key:
            st.error("No key saved yet.")
        else:
            with st.spinner("Testing..."):
                ok, detail = llm.test_groq(key)
            (st.success if ok else st.error)(f"{'OK' if ok else 'Failed'}: {detail}")

st.divider()
st.markdown("**Ollama (local only)**")
c3, c4 = st.columns(2)
ollama_host_input = c3.text_input(
    "Ollama Host", value=st.session_state.get("ollama_host", "http://localhost:11434"),
    key="ollama_host_input",
)
ollama_model_input = c4.text_input(
    "Ollama Chat Model", value=st.session_state.get("ollama_model", llm.OLLAMA_DEFAULT_CHAT_MODEL),
    key="ollama_model_input",
)
if st.button("Save Ollama Settings"):
    st.session_state["ollama_host"] = ollama_host_input
    st.session_state["ollama_model"] = ollama_model_input
    st.success("Saved for this session.")
if st.button("Test Ollama Connection"):
    host = st.session_state.get("ollama_host", "")
    model = st.session_state.get("ollama_model", llm.OLLAMA_DEFAULT_CHAT_MODEL)
    if not host:
        st.error("No host saved yet.")
    else:
        with st.spinner("Testing..."):
            ok, detail = llm.test_ollama(host, model)
        (st.success if ok else st.error)(f"{'OK' if ok else 'Failed'}: {detail}")

st.divider()
st.subheader("Current status")
gemini_ok = bool(st.session_state.get("gemini_api_key"))
groq_ok = bool(st.session_state.get("groq_api_key"))
ollama_ok = bool(st.session_state.get("ollama_host"))
any_configured = gemini_ok or groq_ok or ollama_ok

s1, s2, s3 = st.columns(3)
s1.metric("Gemini", "Configured" if gemini_ok else "Not set")
s2.metric("Groq", "Configured" if groq_ok else "Not set")
s3.metric("Ollama", "Configured" if ollama_ok else "Not set")

if any_configured:
    st.success(
        "AI features are enabled: free-text event capture (Log a Demand Event) and "
        "similarity search (Similarity Search, Opportunity Pipeline) will use the "
        "configured provider chain."
    )
else:
    st.info(
        "No provider configured. The app works fully without one — Log a Demand Event stays "
        "structured-only, and AI similarity features stay hidden. Set up a provider above to "
        "enable them."
    )
