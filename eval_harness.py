"""Benchmark with SILENT-WRONG-ANSWER detection.

The hardest failure of a text-to-SQL agent is a query that runs without error, returns
plausible numbers, and is wrong (e.g. revenue that includes cancelled orders). Each case has:
  truth_sql         - the correct query
  silent_wrong_sql  - a plausible-but-wrong variant (optional)
If the agent's rows match the wrong variant instead of the truth, the case is SILENT-WRONG.

    python eval_harness.py            # runs against the real LLM (about 4 LLM calls per case)
    python eval_harness.py --fast     # skips the disambiguate/validate calls
"""

import sys
from dataclasses import dataclass

from sqlalchemy import text

import db

REVENUE = ("SELECT p.category, ROUND(SUM(oi.quantity*oi.unit_price),2) AS revenue "
           "FROM order_items oi JOIN orders o ON o.order_id=oi.order_id "
           "JOIN products p ON p.product_id=oi.product_id ")


@dataclass
class EvalCase:
    id: str
    question: str
    truth_sql: str
    silent_wrong_sql: str | None = None
    note: str = ""
    database: str = "business"


CASES = [
    EvalCase("B01", "How many customers are in the database?",
             "SELECT COUNT(*) FROM customers"),
    EvalCase("B02", "What are the top 3 product categories by total revenue?",
             REVENUE + "WHERE o.status='completed' GROUP BY p.category ORDER BY revenue DESC LIMIT 3",
             REVENUE + "GROUP BY p.category ORDER BY revenue DESC LIMIT 3",
             "Revenue must exclude cancelled/pending orders"),
    EvalCase("B03", "Who are the top 5 customers by total spending?",
             "SELECT c.name, ROUND(SUM(oi.quantity*oi.unit_price),2) AS spent FROM customers c "
             "JOIN orders o ON o.customer_id=c.customer_id JOIN order_items oi ON oi.order_id=o.order_id "
             "WHERE o.status='completed' GROUP BY c.customer_id ORDER BY spent DESC LIMIT 5",
             "SELECT c.name, ROUND(SUM(oi.quantity*oi.unit_price),2) AS spent FROM customers c "
             "JOIN orders o ON o.customer_id=c.customer_id JOIN order_items oi ON oi.order_id=o.order_id "
             "GROUP BY c.customer_id ORDER BY spent DESC LIMIT 5",
             "Spending must exclude cancelled/pending orders"),
    EvalCase("B04", "How many orders were cancelled?",
             "SELECT COUNT(*) FROM orders WHERE status='cancelled'"),
    EvalCase("B05", "What is the average order value of completed orders?",
             "SELECT ROUND(AVG(t),2) FROM (SELECT SUM(oi.quantity*oi.unit_price) AS t FROM orders o "
             "JOIN order_items oi ON oi.order_id=o.order_id WHERE o.status='completed' "
             "GROUP BY o.order_id)",
             "SELECT ROUND(AVG(unit_price*quantity),2) FROM order_items",
             "Order value is per ORDER, not per line item"),
    EvalCase("B06", "Which country has the most customers?",
             "SELECT country, COUNT(*) AS n FROM customers GROUP BY country ORDER BY n DESC LIMIT 1"),
    EvalCase("B07", "What was the total revenue from completed orders in 2025?",
             "SELECT ROUND(SUM(oi.quantity*oi.unit_price),2) FROM orders o "
             "JOIN order_items oi ON oi.order_id=o.order_id "
             "WHERE o.status='completed' AND o.order_date >= '2025-01-01' AND o.order_date < '2026-01-01'"),
    EvalCase("B08", "How many products are in the Electronics category?",
             "SELECT COUNT(*) FROM products WHERE category='Electronics'"),
    EvalCase("B09", "List the customers who have never placed an order.",
             "SELECT name FROM customers WHERE customer_id NOT IN (SELECT customer_id FROM orders)"),
    EvalCase("B10", "Which product sold the most units in completed orders?",
             "SELECT p.name, SUM(oi.quantity) AS units FROM order_items oi "
             "JOIN orders o ON o.order_id=oi.order_id JOIN products p ON p.product_id=oi.product_id "
             "WHERE o.status='completed' GROUP BY p.product_id ORDER BY units DESC LIMIT 1",
             "SELECT p.name, SUM(oi.quantity) AS units FROM order_items oi "
             "JOIN products p ON p.product_id=oi.product_id GROUP BY p.product_id "
             "ORDER BY units DESC LIMIT 1",
             "Units sold must exclude cancelled/pending orders"),
]


def _norm_value(v):
    if isinstance(v, float):
        return round(v, 2)
    return v


def normalize(rows) -> list:
    """Rows -> sorted list of sorted tuples, so column names/order do not matter."""
    out = []
    for r in rows:
        vals = r.values() if isinstance(r, dict) else r
        out.append(tuple(sorted(str(_norm_value(v)) for v in vals)))
    return sorted(out)


def run_sql(database: str, sql: str) -> list:
    with db.get_engine(database).connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql))]


def classify(agent_rows, truth_rows, wrong_rows=None) -> str:
    a = normalize(agent_rows)
    if a == normalize(truth_rows):
        return "PASS"
    if wrong_rows is not None and normalize(wrong_rows) != normalize(truth_rows) \
            and a == normalize(wrong_rows):
        return "SILENT-WRONG"
    return "FAIL"


def run_eval(cases=None, fast: bool | None = None, runner=None, verbose=True) -> dict:
    if runner is None:
        from agent import run_agent as runner
    cases = cases or CASES
    results = []
    for c in cases:
        r = runner(c.question, c.database, use_cache=False, fast=fast)
        truth = run_sql(c.database, c.truth_sql)
        wrong = run_sql(c.database, c.silent_wrong_sql) if c.silent_wrong_sql else None
        status = classify(r["rows"], truth, wrong) if r["success"] else "FAIL"
        results.append({"id": c.id, "question": c.question, "status": status, "note": c.note,
                        "sql": r["sql_query"], "latency_ms": r["latency_ms"],
                        "llm_calls": r["token_usage"]["llm_calls"], "retries": r["retries"]})
        if verbose:
            mark = {"PASS": "OK ", "FAIL": "XX ", "SILENT-WRONG": "!! "}[status]
            print(f"{mark}[{c.id}] {status:<13} {c.question}")
    total = len(results)
    passed = sum(x["status"] == "PASS" for x in results)
    silent = sum(x["status"] == "SILENT-WRONG" for x in results)
    report = {
        "total": total, "passed": passed, "failed": total - passed - silent,
        "silent_wrong": silent, "pass_rate_pct": round(100 * passed / total, 1) if total else 0,
        "avg_latency_ms": round(sum(x["latency_ms"] for x in results) / total) if total else 0,
        "results": results,
    }
    if verbose:
        print(f"\nPassed {passed}/{total} ({report['pass_rate_pct']}%)  |  "
              f"silent-wrong {silent}  |  avg latency {report['avg_latency_ms']} ms")
    return report


if __name__ == "__main__":
    run_eval(fast="--fast" in sys.argv)
