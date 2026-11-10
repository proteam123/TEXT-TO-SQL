"""Semantic fingerprint cache (SQLite).

Two differently-worded questions with the same meaning get the same fingerprint:
lowercase -> drop punctuation -> drop filler words -> light stemming -> SORT tokens -> hash.
We cache the *SQL* (not the rows), so a cache hit costs zero LLM calls but still returns
fresh data from the database.
"""

import hashlib
import re
import sqlite3
from datetime import datetime, timezone

import config

STOPWORDS = {
    "the", "a", "an", "of", "in", "is", "are", "was", "were", "what", "which", "who", "whom",
    "show", "me", "give", "list", "tell", "has", "have", "had", "do", "does", "did", "for", "to",
    "and", "please", "can", "you", "i", "my", "our", "all", "there", "that", "with", "it",
}


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us")):
        return w[:-1]
    return w


def fingerprint(question: str) -> str:
    tokens = [_stem(t) for t in re.findall(r"[a-z0-9]+", question.lower()) if t not in STOPWORDS]
    return hashlib.sha256(" ".join(sorted(tokens)).encode()).hexdigest()[:16]


def _conn():
    con = sqlite3.connect(config.CACHE_DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS cache (
               fp TEXT, db TEXT, question TEXT, sql TEXT, summary TEXT,
               hits INTEGER DEFAULT 0, created TEXT, PRIMARY KEY (fp, db))"""
    )
    return con


def get(question: str, db: str) -> dict | None:
    fp = fingerprint(question)
    with _conn() as con:
        row = con.execute("SELECT sql, summary FROM cache WHERE fp=? AND db=?", (fp, db)).fetchone()
        if row:
            con.execute("UPDATE cache SET hits = hits + 1 WHERE fp=? AND db=?", (fp, db))
            return {"sql": row[0], "summary": row[1]}
    return None


def put(question: str, db: str, sql: str, summary: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO cache (fp, db, question, sql, summary, hits, created) "
            "VALUES (?,?,?,?,?,0,?)",
            (fingerprint(question), db, question, sql, summary,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )


def delete(question: str, db: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM cache WHERE fp=? AND db=?", (fingerprint(question), db))


def clear() -> int:
    with _conn() as con:
        return con.execute("DELETE FROM cache").rowcount


def entries(limit: int = 50) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT question, db, sql, hits, created FROM cache ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(zip(("question", "db", "sql", "hits", "created"), r)) for r in rows]


def stats() -> dict:
    with _conn() as con:
        n, hits = con.execute("SELECT COUNT(*), COALESCE(SUM(hits),0) FROM cache").fetchone()
    return {"entries": n, "total_hits": hits}
