<<<<<<< HEAD
# TEXT-TO-SQL
=======
# Text-to-SQL Agent — 100% free stack

A production-style **Text-to-SQL agent** you can run for **$0**: ask a question in English, get a
safe SQL query, the data, and a plain-English answer. It re-creates every idea from the paid-API
"production agent" (schema filtering, alias translation, disambiguation, SQL guard, retries,
validation, semantic cache, token tracking, eval harness) using only free tools.

| Layer | Free tool used | Replaces |
|---|---|---|
| LLM | **Groq free tier** (default), **Gemini free tier**, or **Ollama** (local, unlimited) | Claude Haiku (paid) |
| Agent orchestration | **LangGraph** (open source) | same |
| Database | **SQLite** + **SQLAlchemy** (open source) | same |
| API | **FastAPI** + **Uvicorn** | same |
| UI | **Streamlit** | Gradio |
| Cache / logs | **SQLite files** (stdlib `sqlite3`) | same |
| Schema search | **Python stdlib** (`difflib`, `re`) — no vector DB, no embeddings | same |
| Tests | **pytest** with a scripted fake LLM (no API key needed) | — |

---

## 1. The pipeline

```
 User question
      │
      ▼
 ┌───────────────┐   keyword + fuzzy scoring,   │
 │ schema_filter │   alias translation, real    │
 └───────────────┘   column values              │
      ▼
 ┌─────────────────┐  LLM relevance gate; unrelated? → decline
 │ check_relevance │
 └─────────────────┘
      ▼
 ┌─────────────┐  hit   (reuse saved SQL)
 │ cache_check │────────────────────────────────┐
 └─────────────┘                                │
      │ miss                                    │
      ▼                                         │
 ┌──────────────┐    LLM call 2: "what does     │
 │ disambiguate │    'revenue' mean here?"      │
 └──────────────┘                               │
      ▼                                         │
 ┌──────────────┐    LLM call 3: write SQL      │
 │ generate_sql │◄──────────────┐               │
 └──────────────┘               │ retry (≤ 2×)  │
      ▼                         │ with feedback │
 ┌──────────────┐  blocked ─────┤               │
 │  sql_guard   │◄──────────────┼───────────────┘
 └──────────────┘               │
      ▼ safe                    │
 ┌──────────────┐  SQL error ───┤
 │ execute_sql  │  (read-only)  │
 └──────────────┘               │
      ▼                         │
 ┌─────────────────┐ implausible│   LLM call 4: "does this
 │ validate_result │────────────┘   result make sense?"
 └─────────────────┘
      ▼
 ┌───────────┐   LLM call 5: answer in plain English
 │ summarize │
 └───────────┘
      ▼
 ┌─────────────┐
 │ cache_store │ → END        (only validated successes are cached)
 └─────────────┘
```

**Fast mode** skips `disambiguate` and `validate_result`; relevance checking and answer
summarization still run (3 calls instead of 5 before retries). The assistant declines questions
unrelated to the selected database. Every successful retrieval, including a cache hit, gets a fresh
LLM-generated summary.

### What each stage does, and why it exists

| Stage | File | Problem it solves |
|---|---|---|
| `check_relevance` | `agent.py` | Uses the selected database schema to decline unrelated questions before SQL generation. |
| `cache_check` / `cache_store` | `query_cache.py` | Questions are *fingerprinted* (lowercase, drop filler words, stem, **sort words**, hash) so rewordings match. We cache the **SQL**, not the rows, so a hit still returns fresh data; relevance is checked and the result is freshly summarized. |
| `schema_filter` | `schema_index.py` | Real databases have hundreds of tables; sending all of them confuses the model and burns tokens. Each table is scored against the question and only the top K (+ foreign-key neighbours, so joins still work) are sent. |
| alias translation | `schema_index.py` | `ttl_amt`, `cust_nm`, `sts_cd` are meaningless to a model. They become `ttl_amt -- total amount`, so the model sees the meaning *and* the exact name to use. |
| real column values | `schema_index.py` | Models guess `status = 'complete'` when the data says `'completed'`. Low-cardinality text columns are sampled and shown: `values: 'cancelled', 'completed', 'pending'`. |
| `disambiguate` | `agent.py` | "Total revenue" is ambiguous (include cancelled orders?). The model states its assumptions **before** writing SQL; they are shown to the user. |
| `generate_sql` | `agent.py` | The actual text→SQL step, given only the filtered schema + assumptions (+ the previous error when retrying). |
| `sql_guard` | `sql_guard.py` | Don't *trust* the model to be read-only — **enforce** it in code: strip comments, ignore string literals, one statement only, must start with SELECT/WITH, no write/admin keywords, force a `LIMIT`. |
| read-only engine | `db.py` | Second lock: every connection runs `PRAGMA query_only = ON`, so even a guard bug cannot modify data. |
| `execute_sql` + retry | `agent.py` | On a database error, the error text is fed back to the model for a corrected query (max 2 retries). |
| `validate_result` | `agent.py` | Catches **silent wrong answers**: SQL that runs fine but is wrong (forgot the status filter, double-counted a join). A second LLM pass reviews the SQL + sample rows; 0 rows triggers a retry too. |
| `summarize` | `agent.py` | Gemini turns each retrieval's rows into a short answer shown below the CSV download. If this call fails, you still get the data and an explicit warning. |
| token tracker | `token_tracker.py` | Logs tokens, calls and latency per node. On a free tier you "pay" in rate limits, so this shows what to optimise. |
| eval harness | `eval_harness.py` | 10 questions with a correct SQL **and** a plausible-but-wrong SQL. If the agent's result matches the wrong one, it is flagged `SILENT-WRONG`. Re-run after every prompt change to catch regressions. |

---

## 2. A worked example

> Illustrative: the exact wording and SQL your model produces will differ slightly.

Question: **"What are the top 3 product categories by total revenue?"** (database: `business`)

1. **cache_check** — first time asked → miss.
2. **schema_filter** — `revenue` expands (synonyms) to `total/amount/sale/price/quantity`, so
   `products`, `order_items` and `orders` are selected. The LLM sees, for example:
   ```
   TABLE orders  (400 rows)
     order_id INTEGER PK
     customer_id INTEGER FK -> customers.customer_id
     order_date TEXT
     status TEXT -- values: 'cancelled', 'completed', 'pending'
   ```
3. **disambiguate** → `["Revenue = sum(quantity × unit_price) of completed orders only."]`
4. **generate_sql** →
   ```sql
   SELECT p.category, SUM(oi.quantity * oi.unit_price) AS revenue
   FROM order_items oi
   JOIN orders o ON o.order_id = oi.order_id
   JOIN products p ON p.product_id = oi.product_id
   WHERE o.status = 'completed'
   GROUP BY p.category ORDER BY revenue DESC LIMIT 3
   ```
5. **sql_guard** → passes (single SELECT, already has a LIMIT).
6. **execute_sql** → 3 rows from the read-only connection.
7. **validate_result** → "plausible: true". (Had the model forgotten `status = 'completed'`, the
   reviewer would flag it and the agent would regenerate.)
8. **summarize** → "Electronics leads with … followed by …"
9. **cache_store** → saved. Asking *"which 3 product categories have the highest total revenue"*
   later is a cache hit: the saved SQL is re-run on live data, then the current rows are summarized.

The UI shows the summary, assumptions, SQL, a table, a CSV download, and
`⚡/🧠 · latency · LLM calls · tokens · retries · tables sent`.

---

## 3. Why this design is better than a simple demo

- **Accuracy on real schemas** — filtering, alias translation and real column values remove the
  most common hallucinations.
- **Catches silent errors** — assumptions are visible, results are reviewed, and the eval harness
  measures the failure mode demos ignore.
- **Safe by construction** — two independent locks (code guard + read-only connection).
- **Self-healing** — SQL errors become feedback, not failures.
- **Cheap to run** — cache = 0 calls on repeats; fast mode = 2 calls; failures degrade gracefully.
- **Observable and testable** — every call logged; 30 offline tests run in ~1 s with no API key.
- **Free and swappable** — change `LLM_PROVIDER` in `.env`; nothing else changes.

---

## 4. Setup (Windows PowerShell)

```powershell
cd text2sql-free
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .\env.example .\.env  # then edit .env and paste your Gemini API key
notepad .env
python seed_db.py               # creates data\business.db and data\enterprise.db
```

The example `.env` is configured for **Gemini**. Replace `your_gemini_api_key` with your key from
https://aistudio.google.com/apikey. To use another provider instead, set `LLM_PROVIDER` and the
matching key in `.env`.
- **Ollama (no key, no limits, offline)** — install from https://ollama.com, then
  `ollama pull qwen2.5-coder:7b`, set `LLM_PROVIDER=ollama`, `pip install langchain-ollama`

### Run it

```powershell
.\venv\Scripts\streamlit.exe run app.py                    # UI: Ask / Schema / Tables / Usage & cache / Evaluation
.\venv\Scripts\python.exe -m uvicorn api:app --port 8004 --reload    # API docs at http://localhost:8004/docs
.\venv\Scripts\python.exe agent.py "How many orders were cancelled?"   # one-off from the terminal
.\venv\Scripts\python.exe eval_harness.py                  # benchmark (add --fast to halve the LLM calls)
.\venv\Scripts\python.exe -m pip install pytest; .\venv\Scripts\python.exe -m pytest -q   # offline tests, no key needed
```

Try on `business`: *top 3 categories by revenue*, *top 5 customers by spending*, *customers who
never ordered*. Try on `enterprise` (cryptic names, 16 tables): *total order amount per customer
name*. Then open the **Schema** tab, type the same question, and see which tables the model
actually receives.

### Free-tier tips
- Models change often; if Groq reports a missing model, pick another from the Groq console and set
  `LLM_MODEL=...` (for example `llama-3.3-70b-versatile`).
- Hitting rate limits? Turn on **Fast mode**, rely on the cache, or use Ollama locally.
- Small local models (7B) are weaker at SQL; keep `disambiguate`/`validate` on for them.

---

## 5. Use your own database

SQLite: add it in `config.py`

```python
DATABASES = {"mydb": Path(r"D:\data\my.db"), ...}
```

Restart; it appears in the sidebar. Schema reflection, filtering and the guard work unchanged.
For PostgreSQL/MySQL, change the engine URL in `db.py`, replace the SQLite-only
`PRAGMA query_only` with a **read-only database user** (best practice anyway), and change the
`dialect` string in `generate_sql` so the model uses the right functions.

---

## 6. Project layout

```
app.py              Streamlit UI (5 tabs)
api.py              FastAPI (10 endpoints incl. CSV/JSON export)
agent.py            LangGraph pipeline: 9 nodes, conditional edges, run_agent()
llm.py              Free-provider switch (Groq / Gemini / Ollama) + usage logging
schema_index.py     Schema reflection, filtering, alias translation, real column values
sql_guard.py        Deterministic SELECT-only enforcement
db.py               Read-only SQLAlchemy engines
query_cache.py      Semantic fingerprint cache
token_tracker.py    Token / latency / cache-hit logging
eval_harness.py     10-case benchmark with silent-wrong-answer detection
seed_db.py          Demo databases (clean + cryptic enterprise schema)
config.py           Settings (.env)
tests/              30 offline tests (scripted fake LLM)
```

## 7. Honest limitations

- Accuracy depends on the model. Free models are good, not perfect: use the eval harness and the
  visible SQL/assumptions to check answers that matter.
- The cache fingerprint ignores word order, so "A by B" and "B by A" share an entry; numbers and
  words like *highest/lowest* are kept, so "top 3" ≠ "top 5".
- Table scoring is keyword-based. For very large or oddly named schemas, add entries to `ALIASES`
  and `SYNONYMS` in `schema_index.py`, or swap in embeddings later.
- The default "revenue counts completed orders only" is a business rule in the disambiguation
  prompt; change it for your own data.
- No per-query timeout is set for SQLite; add one before exposing this publicly.

## 8. Exercises (learn by extending)

1. Add 10 more `EvalCase`s for the enterprise database and measure the pass rate per model.
2. Add 20 more entries to `ALIASES` for your own naming style.
3. Add a `explain_sql` node that explains the SQL in plain English for non-technical users.
4. Replace keyword scoring with embeddings (`sentence-transformers`, also free) and compare
   accuracy in the eval harness.
5. Add a few-shot examples table (question → SQL) and retrieve the closest ones into the prompt.
>>>>>>> 6653b92 (Add project files)
