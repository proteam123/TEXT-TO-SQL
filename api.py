"""FastAPI layer.

    uvicorn api:app --reload --port 8004      then open http://localhost:8004/docs
"""

import csv
import io

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

import config
import db
import llm
import query_cache
import schema_index
import token_tracker

app = FastAPI(title="Text-to-SQL Agent (free stack)", version="1.0")


class QueryRequest(BaseModel):
    question: str
    database: str = "business"
    use_cache: bool = True
    fast: bool | None = None


def _run(req: QueryRequest) -> dict:
    from agent import run_agent  # imported lazily so /health works without an API key

    if req.database not in config.DATABASES:
        raise HTTPException(404, f"Unknown database '{req.database}'")
    return run_agent(req.question, req.database, req.use_cache, req.fast)


@app.post("/query")
def query(req: QueryRequest):
    return _run(req)


@app.post("/query/export")
def query_export(req: QueryRequest, format: str = Query("csv", pattern="^(csv|json)$")):
    r = _run(req)
    if not r["success"]:
        raise HTTPException(422, r["summary"])
    if format == "json":
        return JSONResponse(r["rows"])
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=r["columns"])
    writer.writeheader()
    writer.writerows(r["rows"])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=result.csv"})


@app.get("/schema")
def schema(database: str = "business", question: str | None = None):
    try:
        index = schema_index.get_index(database)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))
    tables = index.select(question) if question else None
    return {"database": database, "filtered_for": question,
            "schema": index.render(tables)}


@app.get("/tables")
def tables(database: str = "business"):
    try:
        return db.table_previews(database)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))


@app.get("/cache/stats")
def cache_stats():
    return {**query_cache.stats(), "recent": query_cache.entries(10)}


@app.delete("/cache")
def cache_clear():
    return {"deleted": query_cache.clear()}


@app.get("/cost/report")
def cost_report():
    return token_tracker.report()


@app.get("/databases")
def databases():
    return db.databases_status()


@app.post("/evaluate")
def evaluate(fast: bool = False):
    from eval_harness import run_eval

    return run_eval(fast=fast, verbose=False)


@app.get("/health")
def health():
    return {"status": "ok", "llm_provider": llm.provider(), "llm_model": llm.model_name(),
            "databases": db.databases_status()}
