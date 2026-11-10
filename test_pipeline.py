import pytest
from sqlalchemy import text

import db
import eval_harness
import query_cache
import schema_index
from agent import run_agent
from sql_guard import check_sql

Q = "How many customers are in the database?"


# ------------------------------------------------------------------ SQL guard
@pytest.mark.parametrize("sql", [
    "DROP TABLE customers", "DELETE FROM customers", "INSERT INTO orders VALUES (1)",
    "SELECT 1; DROP TABLE customers", "select * from orders;delete from orders",
    "PRAGMA table_info(orders)", "ATTACH DATABASE 'x.db' AS x", "REPLACE INTO customers VALUES (1)",
    "", "   ", "UPDATE orders SET status='x'",
])
def test_guard_blocks(sql):
    assert not check_sql(sql).ok


@pytest.mark.parametrize("sql", [
    "SELECT * FROM customers",
    "WITH t AS (SELECT 1 AS x) SELECT * FROM t",
    "SELECT name FROM customers WHERE name = 'update delete'",   # keywords inside a string are fine
    "SELECT replace(name, 'a', 'b') FROM customers",             # replace() function is fine
    "SELECT created_at, update_dt FROM x",                       # identifiers containing keywords
])
def test_guard_allows(sql):
    assert check_sql(sql).ok


def test_guard_strips_comments_and_adds_limit():
    r = check_sql("SELECT 1 -- DROP TABLE x")
    assert r.ok and "DROP" not in r.sql and r.sql.endswith("LIMIT 200")
    assert check_sql("SELECT 1 LIMIT 5").sql == "SELECT 1 LIMIT 5"


def test_database_is_read_only():
    with pytest.raises(Exception):
        with db.get_engine("business").connect() as c:
            c.execute(text("DELETE FROM customers"))


# ------------------------------------------------------------------ schema index
def test_schema_filter_enterprise():
    idx = schema_index.get_index("enterprise")
    names = [t.name for t in idx.select("What is the total order amount per customer name?", k=6)]
    assert len(names) <= 6
    assert {"ord_hdr", "cust_mst"} <= set(names)
    assert "hr_emp_mst" not in names


def test_alias_translation_and_enum_values():
    idx = schema_index.get_index("enterprise")
    text_ = idx.render([idx.tables["ord_hdr"]])
    assert "total amount" in text_ and "customer name" not in text_
    assert "'CANCELLED'" in text_  # real status values are shown to the LLM
    assert "customer name" in idx.render([idx.tables["cust_mst"]])


# ------------------------------------------------------------------ cache
def test_fingerprint_semantic():
    a = "Which product category had the highest revenue last quarter?"
    b = "what product category has highest revenue in the last quarter"
    assert query_cache.fingerprint(a) == query_cache.fingerprint(b)
    assert query_cache.fingerprint("top 3 customers") != query_cache.fingerprint("top 5 customers")


# ------------------------------------------------------------------ agent graph
def test_happy_path_then_cache_hit(fake_llm):
    fake = fake_llm(sql_for={Q: ["```sql\nSELECT COUNT(*) AS n FROM customers;\n```"]})
    r = run_agent(Q)
    assert r["success"] and r["rows"] == [{"n": 40}] and r["retries"] == 0
    assert r["assumptions"] == ["Revenue counts completed orders only."]
    assert r["token_usage"]["llm_calls"] == 5  # relevance, disambiguate, generate, validate, summarize
    assert query_cache.stats()["entries"] == 1

    before = len(fake.calls)
    r2 = run_agent("how many customers are there in the database")  # reworded
    assert r2["cache_hit"] and r2["rows"] == [{"n": 40}]
    assert len(fake.calls) == before + 2        # relevance check and fresh summary on cached rows
    assert fake.count("generate_sql") == 1
    assert r2["token_usage"]["llm_calls"] == 2


def test_self_correction_after_sql_error(fake_llm):
    fake = fake_llm(sql_for={Q: ["SELECT COUNT(*) FROM customer_table",
                                 "SELECT COUNT(*) AS n FROM customers"]})
    r = run_agent(Q)
    assert r["success"] and r["retries"] == 1 and r["rows"] == [{"n": 40}]
    # the second generate call was told about the database error
    second = [u for t, u in fake.calls if t == "generate_sql"][1]
    assert "no such table" in second


def test_destructive_sql_is_blocked(fake_llm):
    fake_llm(sql_for={"Delete everything": ["DROP TABLE customers"]})
    r = run_agent("Delete everything")
    assert not r["success"] and "safe query" in r["summary"]
    assert r["retries"] == 3  # 1 attempt + 2 retries, then give up
    with db.get_engine("business").connect() as c:  # table still there
        assert c.execute(text("SELECT COUNT(*) FROM customers")).scalar() == 40


def test_validation_triggers_regeneration(fake_llm):
    q = "What is total revenue?"
    wrong = "SELECT SUM(quantity*unit_price) AS revenue FROM order_items"
    right = ("SELECT SUM(oi.quantity*oi.unit_price) AS revenue FROM order_items oi "
             "JOIN orders o ON o.order_id=oi.order_id WHERE o.status='completed'")
    fake = fake_llm(sql_for={q: [wrong, right]}, plausible=[False, True])
    r = run_agent(q)
    assert r["success"] and r["retries"] == 1
    assert "status='completed'" in r["sql_query"]
    assert fake.count("generate_sql") == 2


def test_fast_mode_skips_two_calls(fake_llm):
    fake = fake_llm(sql_for={Q: ["SELECT COUNT(*) AS n FROM customers"]})
    r = run_agent(Q, use_cache=False, fast=True)
    assert r["success"] and r["token_usage"]["llm_calls"] == 3
    assert fake.count("disambiguate") == 0 and fake.count("validate_result") == 0
    assert fake.count("check_relevance") == 1


def test_out_of_scope_question_is_declined_without_sql(fake_llm):
    fake = fake_llm(in_scope=False)
    r = run_agent("Tell me a joke", use_cache=False)
    assert not r["success"]
    assert r["summary"] == "Unable to retrieve an answer from the selected database."
    assert fake.count("check_relevance") == 1
    assert fake.count("generate_sql") == 0


def test_llm_failure_is_reported_not_raised(monkeypatch):
    import llm

    def boom():
        raise RuntimeError("GROQ_API_KEY is not set")

    monkeypatch.setattr(llm, "get_llm", boom)
    r = run_agent("anything", use_cache=False)
    assert not r["success"] and "GROQ_API_KEY" in r["summary"]


# ------------------------------------------------------------------ eval harness
def test_classify():
    truth, wrong = [(1, 10.0)], [(1, 12.5)]
    assert eval_harness.classify([{"a": 1, "b": 10.0}], truth, wrong) == "PASS"
    assert eval_harness.classify([{"a": 1, "b": 12.5}], truth, wrong) == "SILENT-WRONG"
    assert eval_harness.classify([{"a": 9}], truth, wrong) == "FAIL"


def test_eval_flags_silent_wrong_answer(fake_llm):
    by_id = {c.id: c for c in eval_harness.CASES}
    sql_for = {c.question: [c.truth_sql] for c in eval_harness.CASES}
    b2 = by_id["B02"]
    sql_for[b2.question] = [b2.silent_wrong_sql]  # agent "forgets" the status filter
    fake_llm(sql_for=sql_for)
    rep = eval_harness.run_eval(verbose=False, fast=True)
    status = {x["id"]: x["status"] for x in rep["results"]}
    assert status["B02"] == "SILENT-WRONG"
    assert all(v == "PASS" for k, v in status.items() if k != "B02")
    assert rep["passed"] == 9 and rep["silent_wrong"] == 1


# ------------------------------------------------------------------ API
def test_api_endpoints(fake_llm):
    from fastapi.testclient import TestClient

    from api import app

    fake_llm(sql_for={Q: ["SELECT COUNT(*) AS n FROM customers"]})
    c = TestClient(app)
    assert c.get("/health").json()["status"] == "ok"
    r = c.post("/query", json={"question": Q, "use_cache": False}).json()
    assert r["success"] and r["rows"] == [{"n": 40}]
    assert c.post("/query/export?format=csv", json={"question": Q, "use_cache": False}).text.startswith("n")
    assert "TABLE orders" in c.get("/schema", params={"database": "business"}).json()["schema"]
    assert c.get("/cost/report").json()["total_queries"] >= 1
    assert c.get("/tables").status_code == 200
    assert c.post("/query", json={"question": Q, "database": "nope"}).status_code == 404
    assert c.delete("/cache").status_code == 200
