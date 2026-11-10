"""Per-call token + latency log (SQLite). On a free tier you pay in *rate limits*, not dollars,
so we track tokens and number of LLM calls, and how many calls the cache saved."""

import sqlite3
from datetime import datetime, timezone

import config


def _conn():
    con = sqlite3.connect(config.TOKEN_DB)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS calls (
            run_id TEXT, node TEXT, model TEXT,
            input_tokens INTEGER, output_tokens INTEGER, latency_ms INTEGER, ts TEXT);
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, question TEXT, db TEXT, cache_hit INTEGER,
            success INTEGER, retries INTEGER, latency_ms INTEGER, ts TEXT);
        """
    )
    return con


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_call(run_id, node, model, input_tokens, output_tokens, latency_ms):
    with _conn() as con:
        con.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?)",
                    (run_id, node, model, input_tokens, output_tokens, latency_ms, _now()))


def log_run(run_id, question, db, cache_hit, success, retries, latency_ms):
    with _conn() as con:
        con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?)",
                    (run_id, question, db, int(cache_hit), int(success), retries, latency_ms, _now()))


def run_totals(run_id: str) -> dict:
    with _conn() as con:
        calls, tin, tout = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) "
            "FROM calls WHERE run_id=?", (run_id,)).fetchone()
    return {"llm_calls": calls, "input_tokens": tin, "output_tokens": tout}


def report() -> dict:
    with _conn() as con:
        runs, hits, ok, avg_ms = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(cache_hit),0), COALESCE(SUM(success),0), "
            "COALESCE(AVG(latency_ms),0) FROM runs").fetchone()
        calls, tin, tout = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) "
            "FROM calls").fetchone()
        by_node = con.execute(
            "SELECT node, COUNT(*), SUM(input_tokens), SUM(output_tokens), AVG(latency_ms) "
            "FROM calls GROUP BY node ORDER BY 2 DESC").fetchall()
    miss = runs - hits
    calls_per_miss = (calls / miss) if miss else 0
    return {
        "total_queries": runs,
        "success_rate_pct": round(100 * ok / runs, 1) if runs else 0,
        "cache_hits": hits,
        "cache_hit_rate_pct": round(100 * hits / runs, 1) if runs else 0,
        "llm_calls": calls,
        "llm_calls_saved_by_cache": round(hits * calls_per_miss),
        "input_tokens": tin,
        "output_tokens": tout,
        "avg_latency_ms": round(avg_ms),
        "cost_usd": 0.0,  # free tier
        "by_node": [
            {"node": n, "calls": c, "input_tokens": i or 0, "output_tokens": o or 0,
             "avg_latency_ms": round(a or 0)} for n, c, i, o, a in by_node
        ],
    }
