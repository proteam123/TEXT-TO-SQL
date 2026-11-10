"""The LangGraph pipeline.

 question
   -> cache_check --(hit)--------------------------> sql_guard
   -> schema_filter -> disambiguate -> generate_sql -> sql_guard --(blocked)--> generate_sql (retry)
                                                          |
                                                      execute_sql --(error)--> generate_sql (retry)
                                                          |
                                                    validate_result --(implausible)--> generate_sql
                                                          |
                                                      summarize -> cache_store -> END
"""

import datetime
import json
import time
import uuid
from typing import TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy import text

import config
import db
import llm
import query_cache
import schema_index
import token_tracker
from sql_guard import check_sql


class AgentState(TypedDict, total=False):
    run_id: str
    question: str
    database: str
    use_cache: bool
    fast: bool
    cache_hit: bool
    cached_summary: str
    restart: bool
    schema_text: str
    tables_used: list
    assumptions: list
    sql_query: str
    feedback: str
    guard_error: str
    exec_error: str
    regen: bool
    retries: int
    columns: list
    rows: list
    row_count: int
    warnings: list
    summary: str
    success: bool
    in_scope: bool


# --------------------------------------------------------------------------- prompts
DISAMBIGUATE_SYSTEM = """TASK: disambiguate
You help a text-to-SQL system. Today's date is {today}.
Given a database schema and a user question, list the assumptions needed to answer the question
unambiguously: what a vague term means (revenue, best, recent, active), concrete dates for
relative periods ("last quarter", "this year"), which statuses count, how ties are handled.
Use sensible business defaults. Revenue, sales and spending count only completed orders
(not cancelled or pending) unless the question says otherwise.
List ONLY assumptions that are actually needed. Reply with JSON only:
{{"assumptions": ["..."]}}   (an empty list if the question is already unambiguous)"""

GENERATE_SYSTEM = """TASK: generate_sql
You are an expert {dialect} analyst. Today's date is {today}.
Write exactly ONE read-only SQL query that answers the question.
Rules:
- Use ONLY the tables and columns in the schema, with their exact names.
- Text after "--" in the schema explains cryptic names and lists the real values of
  category/status columns: use those exact values.
- Follow the stated assumptions.
- SELECT or WITH only. Never modify data.
- Return only the SQL. No markdown, no explanation."""

VALIDATE_SYSTEM = """TASK: validate_result
You are a strict reviewer of SQL results. Decide whether the SQL and its result plausibly answer
the question and follow the stated assumptions (e.g. required status filters, correct joins,
no double counting, sensible magnitudes). Reply with JSON only:
{"plausible": true or false, "issue": "one sentence describing the problem, or empty"}"""

SUMMARIZE_SYSTEM = """TASK: summarize
Answer the user's question in 1-3 plain sentences using ONLY the rows provided. Mention the key
numbers or names. If the rows are only a sample of a larger result, say so. Do not mention SQL."""

RELEVANCE_SYSTEM = """TASK: check_relevance
Decide whether the user's question can be answered using the selected database schema.
Questions about the data, its tables, columns, or values are in scope. General knowledge,
small talk, and questions unrelated to this database are out of scope.
Reply with JSON only: {"in_scope": true} or {"in_scope": false}."""


def _today() -> str:
    return datetime.date.today().isoformat()


def _rows_json(rows: list, n: int) -> str:
    return json.dumps(rows[:n], default=str)


# --------------------------------------------------------------------------- nodes
def cache_check(s: AgentState) -> dict:
    if not s.get("use_cache", True):
        return {"cache_hit": False}
    hit = query_cache.get(s["question"], s["database"])
    if hit:
        return {"cache_hit": True, "sql_query": hit["sql"], "cached_summary": hit["summary"]}
    return {"cache_hit": False}


def check_relevance(s: AgentState) -> dict:
    user = (f"Selected database: {s['database']}\n"
            f"Available schema:\n{s['schema_text']}\n\nQuestion: {s['question']}")
    raw = llm.ask("check_relevance", s["run_id"], RELEVANCE_SYSTEM, user)
    verdict = llm.parse_json(raw)
    if not isinstance(verdict.get("in_scope"), bool):
        raise ValueError("The AI relevance check returned an invalid response.")
    if verdict["in_scope"]:
        return {"in_scope": True}
    return {
        "in_scope": False,
        "success": False,
        "summary": "Unable to retrieve an answer from the selected database.",
    }


def schema_filter(s: AgentState) -> dict:
    index = schema_index.get_index(s["database"])
    tables = index.select(s["question"])
    return {"schema_text": index.render(tables), "tables_used": [t.name for t in tables],
            "restart": False, "retries": 0, "guard_error": None, "exec_error": None}


def disambiguate(s: AgentState) -> dict:
    if s.get("fast"):
        return {"assumptions": []}
    system = DISAMBIGUATE_SYSTEM.format(today=_today())
    user = f"Schema:\n{s['schema_text']}\n\nQuestion: {s['question']}"
    data = llm.parse_json(llm.ask("disambiguate", s["run_id"], system, user))
    items = data.get("assumptions") or []
    return {"assumptions": [str(a) for a in items if str(a).strip()]}


def generate_sql(s: AgentState) -> dict:
    system = GENERATE_SYSTEM.format(dialect="SQLite", today=_today())
    assumptions = "\n".join(f"- {a}" for a in s.get("assumptions", [])) or "(none)"
    user = (f"Schema:\n{s['schema_text']}\n\nAssumptions:\n{assumptions}\n\n"
            f"Question: {s['question']}")
    if s.get("feedback"):
        user += (f"\n\nYour previous query:\n{s.get('sql_query', '')}\n"
                 f"Problem: {s['feedback']}\nWrite a corrected query.")
    sql = llm.clean_sql(llm.ask("generate_sql", s["run_id"], system, user))
    return {"sql_query": sql, "feedback": None, "guard_error": None, "exec_error": None,
            "regen": False}


def sql_guard(s: AgentState) -> dict:
    result = check_sql(s.get("sql_query", ""))
    if result.ok:
        return {"sql_query": result.sql, "guard_error": None}
    if s.get("cache_hit"):  # a bad cached entry: drop it and start over without the cache
        query_cache.delete(s["question"], s["database"])
        return {"restart": True, "cache_hit": False}
    return {"guard_error": result.reason, "retries": s.get("retries", 0) + 1,
            "feedback": f"The query was blocked: {result.reason} Return a single read-only SELECT."}


def execute_sql(s: AgentState) -> dict:
    try:
        with db.get_engine(s["database"]).connect() as conn:
            res = conn.execute(text(s["sql_query"]))
            columns = list(res.keys())
            rows = [dict(r._mapping) for r in res.fetchmany(config.MAX_ROWS)]
    except Exception as exc:  # SQL error: report it back to the model
        msg = str(getattr(exc, "orig", exc))[:400]
        if s.get("cache_hit"):
            query_cache.delete(s["question"], s["database"])
            return {"restart": True, "cache_hit": False}
        return {"exec_error": msg, "retries": s.get("retries", 0) + 1,
                "feedback": f"The query failed with this database error: {msg}"}
    return {"columns": columns, "rows": rows, "row_count": len(rows), "exec_error": None}


def validate_result(s: AgentState) -> dict:
    if s.get("fast"):
        return {"regen": False}
    issue = ""
    if s["row_count"] == 0:
        issue = ("The query returned 0 rows. Check the filters and use the exact values listed in "
                 "the schema. If nothing genuinely matches, keep the query.")
    else:
        user = (f"Question: {s['question']}\nAssumptions: {s.get('assumptions') or 'none'}\n"
                f"SQL:\n{s['sql_query']}\nRows returned: {s['row_count']}\n"
                f"First rows: {_rows_json(s['rows'], 10)}")
        verdict = llm.parse_json(llm.ask("validate_result", s["run_id"], VALIDATE_SYSTEM, user))
        if verdict.get("plausible") is False:
            issue = str(verdict.get("issue") or "The result looks wrong.")
    if not issue:
        return {"regen": False}
    retries = s.get("retries", 0) + 1
    if retries <= config.MAX_RETRIES:
        return {"retries": retries, "feedback": issue, "regen": True}
    return {"regen": False, "warnings": s.get("warnings", []) + [f"Result may be wrong: {issue}"]}


def summarize(s: AgentState) -> dict:
    if s.get("guard_error"):
        return {"success": False,
                "summary": f"I could not run a safe query for that question. {s['guard_error']}"}
    if s.get("exec_error"):
        return {"success": False,
                "summary": f"I could not produce a working SQL query. Last error: {s['exec_error']}"}
    n = s.get("row_count", 0)
    try:
        user = (f"Question: {s['question']}\nAssumptions: {s.get('assumptions') or 'none'}\n"
                f"Total rows: {n}\nRows (up to 20): {_rows_json(s['rows'], 20)}")
        summary = llm.ask("summarize", s["run_id"], SUMMARIZE_SYSTEM, user)
    except Exception as exc:  # Keep retrieved data available if the summary call fails.
        warning = f"AI summary unavailable: {exc}"
        return {"success": True, "summary": f"The query returned {n} row(s).",
                "warnings": s.get("warnings", []) + [warning]}
    return {"success": True, "summary": summary}


def cache_store(s: AgentState) -> dict:
    if s.get("success") and not s.get("cache_hit") and not s.get("warnings"):
        query_cache.put(s["question"], s["database"], s["sql_query"], s["summary"])
    return {}


# --------------------------------------------------------------------------- routing
def after_cache(s):
    return "sql_guard" if s.get("cache_hit") else "disambiguate"


def after_relevance(s):
    return "cache_check" if s.get("in_scope") else END


def after_guard(s):
    if s.get("restart"):
        return "schema_filter"
    if s.get("guard_error"):
        return "generate_sql" if s["retries"] <= config.MAX_RETRIES else "summarize"
    return "execute_sql"


def after_execute(s):
    if s.get("restart"):
        return "schema_filter"
    if s.get("exec_error"):
        return "generate_sql" if s["retries"] <= config.MAX_RETRIES else "summarize"
    return "summarize" if s.get("cache_hit") else "validate_result"


def after_validate(s):
    return "generate_sql" if s.get("regen") else "summarize"


def after_summarize(s):
    return "cache_store" if s.get("success") and not s.get("cache_hit") else END


def build_graph():
    g = StateGraph(AgentState)
    for name, fn in [("schema_filter", schema_filter), ("check_relevance", check_relevance),
                     ("cache_check", cache_check),
                     ("disambiguate", disambiguate), ("generate_sql", generate_sql),
                     ("sql_guard", sql_guard), ("execute_sql", execute_sql),
                     ("validate_result", validate_result), ("summarize", summarize),
                     ("cache_store", cache_store)]:
        g.add_node(name, fn)
    g.set_entry_point("schema_filter")
    g.add_edge("schema_filter", "check_relevance")
    g.add_conditional_edges("check_relevance", after_relevance, ["cache_check", END])
    g.add_conditional_edges("cache_check", after_cache, ["sql_guard", "disambiguate"])
    # A bad cached entry restarts at schema filtering, then rechecks relevance.
    g.add_edge("disambiguate", "generate_sql")
    g.add_edge("generate_sql", "sql_guard")
    g.add_conditional_edges("sql_guard", after_guard,
                            ["schema_filter", "generate_sql", "summarize", "execute_sql"])
    g.add_conditional_edges("execute_sql", after_execute,
                            ["schema_filter", "generate_sql", "summarize", "validate_result"])
    g.add_conditional_edges("validate_result", after_validate, ["generate_sql", "summarize"])
    g.add_conditional_edges("summarize", after_summarize, ["cache_store", END])
    g.add_edge("cache_store", END)
    return g.compile()


GRAPH = build_graph()


# --------------------------------------------------------------------------- public API
def run_agent(question: str, database: str = "business", use_cache: bool = True,
              fast: bool | None = None) -> dict:
    run_id = uuid.uuid4().hex[:12]
    fast = config.fast_mode_default() if fast is None else fast
    t0 = time.perf_counter()
    state = {"run_id": run_id, "question": question.strip(), "database": database,
             "use_cache": use_cache, "fast": fast, "retries": 0, "warnings": []}
    try:
        final = GRAPH.invoke(state, {"recursion_limit": 60})
        error = None
    except Exception as exc:  # e.g. missing API key, rate limit, network error
        final = {**state, "success": False,
                 "summary": f"The AI service reported an error: {exc}"}
        error = str(exc)
    latency = int((time.perf_counter() - t0) * 1000)
    token_tracker.log_run(run_id, question, database, final.get("cache_hit", False),
                          final.get("success", False), final.get("retries", 0), latency)
    return {
        "run_id": run_id,
        "question": question,
        "database": database,
        "success": bool(final.get("success")),
        "summary": final.get("summary", ""),
        "sql_query": final.get("sql_query", ""),
        "columns": final.get("columns", []),
        "rows": final.get("rows", []),
        "row_count": final.get("row_count", 0),
        "assumptions": final.get("assumptions", []),
        "warnings": final.get("warnings", []),
        "tables_used": final.get("tables_used", []),
        "cache_hit": bool(final.get("cache_hit")),
        "retries": final.get("retries", 0),
        "fast_mode": fast,
        "token_usage": token_tracker.run_totals(run_id),
        "latency_ms": latency,
        "error": error,
    }


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "Which product category had the highest revenue?"
    r = run_agent(q)
    print(r["summary"], "\n\nSQL:\n" + r["sql_query"], "\n\nTokens:", r["token_usage"])
