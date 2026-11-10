"""Deterministic SQL safety check. The LLM is *asked* to write only SELECTs; this module
*enforces* it with code.

Steps: strip comments (so `--DROP` tricks fail) -> blank string literals (so a value like
'update' is not mistaken for a keyword) -> single statement only -> must start with
SELECT/WITH -> no write/admin keywords -> enforce a row LIMIT.
"""

import re
from dataclasses import dataclass

import config

# One pass over the text so quotes and comments are handled in the right precedence.
_TOKEN = re.compile(r"""'(?:[^']|'')*'|"(?:[^"]|"")*"|--[^\n]*|/\*.*?\*/""", re.DOTALL)

_BANNED = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|attach|detach|pragma|"
    r"vacuum|reindex|merge|exec|execute|call|load_extension)\b|\breplace\s+into\b",
    re.IGNORECASE,
)


@dataclass
class GuardResult:
    ok: bool
    sql: str = ""        # cleaned SQL that is safe to execute
    reason: str = ""     # why it was blocked


def _strip_comments(sql: str) -> str:
    def repl(m):
        t = m.group(0)
        return t if t[0] in "'\"" else " "

    return _TOKEN.sub(repl, sql)


def _blank_literals(sql: str) -> str:
    return _TOKEN.sub(lambda m: "''" if m.group(0)[0] in "'\"" else " ", sql)


def check_sql(sql: str, max_rows: int | None = None) -> GuardResult:
    max_rows = max_rows or config.MAX_ROWS
    if not sql or not sql.strip():
        return GuardResult(False, reason="Empty query.")

    no_comments = _strip_comments(sql).strip().rstrip(";").strip()
    scan = _blank_literals(sql).strip().rstrip(";").strip()

    if ";" in scan:
        return GuardResult(False, reason="Multiple statements are not allowed.")
    first = re.match(r"\s*(\w+)", scan)
    if not first or first.group(1).lower() not in ("select", "with"):
        return GuardResult(False, reason="Only SELECT queries are allowed.")
    banned = _BANNED.search(scan)
    if banned:
        return GuardResult(False, reason=f"Forbidden keyword: {banned.group(0).upper()}.")

    if not re.search(r"\blimit\b", scan, re.IGNORECASE):
        no_comments = f"{no_comments}\nLIMIT {max_rows}"
    return GuardResult(True, sql=no_comments)
