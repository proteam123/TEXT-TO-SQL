"""Streamlit UI.   streamlit run app.py"""

import pandas as pd
import streamlit as st

import config
import db
import llm
import query_cache
import schema_index
import token_tracker

st.set_page_config(page_title="Text-to-SQL Agent", page_icon="🗄️", layout="wide")

EXAMPLES = {
    "business": [
        "What are the top 3 product categories by total revenue?",
        "Who are the top 5 customers by total spending?",
        "How many orders were cancelled?",
        "Which country has the most customers?",
        "List the customers who have never placed an order.",
    ],
    "enterprise": [
        "What is the total order amount per customer name?",
        "Which product category has the highest sales amount?",
        "How many cancelled orders are there?",
    ],
}

with st.sidebar:
    st.title("🗄️ Text-to-SQL")
    status = {d["name"]: d for d in db.databases_status()}
    database = st.selectbox("Database", list(config.DATABASES))
    if not status[database]["exists"]:
        st.error("Database file missing. Run `python seed_db.py` first.")
        st.stop()
    fast = st.toggle("Fast mode (2 LLM calls instead of 4)", value=config.fast_mode_default(),
                     help="Skips the disambiguate and validate steps: quicker and uses less of "
                          "your free-tier quota, but less accurate on ambiguous questions.")
    use_cache = st.toggle("Use query cache", value=True)
    st.caption(f"LLM: **{llm.provider()}** / `{llm.model_name()}`")
    st.caption(f"Max rows {config.MAX_ROWS} · Top-K tables {config.TOP_K_TABLES} · "
               f"Max retries {config.MAX_RETRIES}")

ask_tab, schema_tab, tables_tab, usage_tab, eval_tab = st.tabs(
    ["Ask", "Schema", "Tables", "Usage & cache", "Evaluation"])

# ------------------------------------------------------------------ Ask
with ask_tab:
    st.subheader("Ask a question about your data")
    cols = st.columns(len(EXAMPLES[database]))
    for col, ex in zip(cols, EXAMPLES[database]):
        if col.button(ex):
            st.session_state["question"] = ex
    question = st.text_input("Question", key="question",
                             placeholder="e.g. Which country has the most customers?")
    if st.button("Run", type="primary") and question.strip():
        from agent import run_agent

        with st.spinner("Thinking..."):
            st.session_state["result"] = run_agent(question, database, use_cache, fast)

    r = st.session_state.get("result")
    if r:
        if not r["success"]:
            st.error(r["summary"] or "No answer.")
        for w in r["warnings"]:
            st.warning(w)
        if r["assumptions"]:
            with st.expander("Assumptions the agent made", expanded=True):
                for a in r["assumptions"]:
                    st.write(f"- {a}")
        if r["sql_query"]:
            st.code(r["sql_query"], language="sql")
        if r["rows"]:
            df = pd.DataFrame(r["rows"], columns=r["columns"])
            st.dataframe(df)
            st.download_button("Download CSV", df.to_csv(index=False), "result.csv", "text/csv")
        if r["success"]:
            st.subheader("Summary")
            st.write(r["summary"] or "No summary available.")
        t = r["token_usage"]
        st.caption(
            f"{'⚡ cache hit' if r['cache_hit'] else '🧠 fresh'} · {r['latency_ms']} ms · "
            f"{t['llm_calls']} LLM call(s) · {t['input_tokens']} in / {t['output_tokens']} out tokens · "
            f"retries {r['retries']} · tables sent to LLM: {', '.join(r['tables_used']) or '-'}")

# ------------------------------------------------------------------ Schema
with schema_tab:
    st.subheader("What the LLM sees")
    q = st.text_input("Filter for a question (optional)", key="schema_q",
                      placeholder="e.g. total order amount per customer")
    index = schema_index.get_index(database)
    chosen = index.select(q) if q else None
    st.caption(f"{len(chosen) if chosen else len(index.tables)} of {len(index.tables)} tables shown")
    st.code(index.render(chosen), language="text")

# ------------------------------------------------------------------ Tables
with tables_tab:
    for t in db.table_previews(database):
        with st.expander(f"{t['table']}  ({t['row_count']} rows)"):
            st.dataframe(pd.DataFrame(t["preview"], columns=t["columns"]))

# ------------------------------------------------------------------ Usage
with usage_tab:
    rep = token_tracker.report()
    c = st.columns(5)
    c[0].metric("Queries", rep["total_queries"])
    c[1].metric("Success rate", f"{rep['success_rate_pct']}%")
    c[2].metric("Cache hit rate", f"{rep['cache_hit_rate_pct']}%")
    c[3].metric("LLM calls", rep["llm_calls"], help="Free tiers limit requests per minute/day")
    c[4].metric("Calls saved by cache", rep["llm_calls_saved_by_cache"])
    st.caption(f"Tokens: {rep['input_tokens']} in / {rep['output_tokens']} out · "
               f"avg latency {rep['avg_latency_ms']} ms · cost $0 (free tier)")
    if rep["by_node"]:
        st.dataframe(pd.DataFrame(rep["by_node"]))
    st.subheader("Cache")
    st.json(query_cache.stats())
    ents = query_cache.entries(20)
    if ents:
        st.dataframe(pd.DataFrame(ents))
    if st.button("Clear cache"):
        st.success(f"Deleted {query_cache.clear()} entries")

# ------------------------------------------------------------------ Eval
with eval_tab:
    st.write("Runs 10 benchmark questions on the **business** database and flags "
             "**silent wrong answers** (queries that run but return wrong data).")
    st.info("Uses roughly 40 LLM calls (20 in fast mode): watch your free-tier limits.")
    if st.button("Run evaluation"):
        from eval_harness import run_eval

        with st.spinner("Running benchmark..."):
            rep = run_eval(fast=fast, verbose=False)
        m = st.columns(4)
        m[0].metric("Passed", f"{rep['passed']}/{rep['total']}")
        m[1].metric("Silent wrong", rep["silent_wrong"])
        m[2].metric("Failed", rep["failed"])
        m[3].metric("Avg latency", f"{rep['avg_latency_ms']} ms")
        st.dataframe(pd.DataFrame(rep["results"])[["id", "status", "question", "note", "retries",
                                                   "llm_calls", "latency_ms"]],
                     )
