"""Schema filtering + cryptic-name translation (no vector DB, no embeddings - stdlib only).

1. Reflect the database (tables, columns, primary/foreign keys).
2. Translate abbreviations:  ttl_amt -> "total amount",  cust_nm -> "customer name".
3. Sample low-cardinality text columns so the LLM sees the REAL values
   (status in 'completed','cancelled','pending') instead of guessing them.
4. For each question, score every table (keyword overlap + fuzzy match), keep the top K,
   then pull in FK neighbours so joins are possible.
"""

import difflib
import re
from dataclasses import dataclass, field

from sqlalchemy import inspect, text

import config
import db

ALIASES = {
    "cust": "customer", "nm": "name", "ord": "order", "hdr": "header", "ln": "line",
    "prd": "product", "qty": "quantity", "prc": "price", "amt": "amount", "ttl": "total",
    "dt": "date", "sts": "status", "cd": "code", "ctry": "country", "mst": "master",
    "crt": "created", "cat": "category", "emp": "employee", "dpt": "department",
    "mgr": "manager", "fin": "finance", "gl": "general ledger", "ent": "entry",
    "acct": "account", "dbt": "debit", "cr": "credit", "pst": "posted", "typ": "type",
    "inv": "inventory", "whs": "warehouse", "stk": "stock", "lvl": "level", "oh": "on hand",
    "loc": "location", "aud": "audit", "trl": "trail", "usr": "user", "actn": "action",
    "sup": "supplier", "vnd": "vendor", "po": "purchase order", "cfg": "config",
    "prm": "parameter", "val": "value", "mkt": "marketing", "cmp": "campaign",
    "strt": "start", "bdgt": "budget", "hr": "human resources", "sys": "system",
}

# Words people use for the same thing as a column/table concept.
SYNONYMS = {
    "revenue": {"total", "amount", "sale", "price", "quantity"},
    "sale": {"total", "amount", "order"},
    "spending": {"total", "amount", "order"},
    "spent": {"total", "amount", "order"},
    "client": {"customer"}, "buyer": {"customer"}, "purchase": {"order"},
    "item": {"product", "line"}, "sold": {"order", "quantity", "line"},
    "staff": {"employee"}, "supplier": {"vendor"},
}

STOPWORDS = {
    "the", "a", "an", "of", "in", "is", "are", "was", "were", "what", "which", "who", "show",
    "me", "give", "list", "tell", "has", "have", "had", "do", "does", "did", "for", "to", "and",
    "please", "can", "you", "i", "my", "our", "all", "there", "that", "with", "by", "per",
    "how", "many", "much", "top", "most", "highest", "lowest", "last", "each", "from", "on",
}


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us")):
        return w[:-1]
    return w


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def expand(name: str) -> str:
    """'ttl_amt' -> 'total amount'. Unknown parts are kept as-is."""
    return " ".join(ALIASES.get(p, p) for p in name.lower().split("_") if p)


@dataclass
class Column:
    name: str
    type: str
    pk: bool = False
    fk: str | None = None
    enum_values: list | None = None

    @property
    def meaning(self) -> str:
        e = expand(self.name)
        return e if e != self.name.replace("_", " ") else ""


@dataclass
class Table:
    name: str
    columns: list[Column]
    row_count: int = 0
    tokens: set = field(default_factory=set)      # from the table name
    col_tokens: set = field(default_factory=set)  # from column names
    value_tokens: set = field(default_factory=set)  # from enum values

    @property
    def meaning(self) -> str:
        e = expand(self.name)
        return e if e != self.name.replace("_", " ") else ""


def _token_set(name: str) -> set:
    raw = _tokens(name.replace("_", " "))
    return {_stem(t) for t in raw} | {_stem(t) for t in _tokens(expand(name))}


class SchemaIndex:
    def __init__(self, db_name: str):
        self.db_name = db_name
        self.tables: dict[str, Table] = {}
        self._build()

    def _build(self):
        engine = db.get_engine(self.db_name)
        insp = inspect(engine)
        with engine.connect() as conn:
            for tname in insp.get_table_names():
                pks = set(insp.get_pk_constraint(tname).get("constrained_columns") or [])
                fks = {}
                for fk in insp.get_foreign_keys(tname):
                    for c, rc in zip(fk["constrained_columns"], fk["referred_columns"]):
                        fks[c] = f"{fk['referred_table']}.{rc}"
                rows = conn.execute(text(f'SELECT COUNT(*) FROM "{tname}"')).scalar() or 0
                cols = []
                for c in insp.get_columns(tname):
                    col = Column(c["name"], str(c["type"]), c["name"] in pks, fks.get(c["name"]))
                    is_text = any(k in col.type.upper() for k in ("CHAR", "TEXT", "CLOB"))
                    if is_text and not col.pk and not col.fk and rows >= 20:
                        vals = [r[0] for r in conn.execute(
                            text(f'SELECT DISTINCT "{c["name"]}" FROM "{tname}" LIMIT 9'))
                            if r[0] is not None]
                        if 0 < len(vals) <= 8:
                            col.enum_values = sorted(vals)
                    cols.append(col)
                t = Table(tname, cols, rows)
                t.tokens = _token_set(tname)
                for col in cols:
                    t.col_tokens |= _token_set(col.name)
                    for v in col.enum_values or []:
                        t.value_tokens |= {_stem(x) for x in _tokens(str(v))}
                self.tables[tname] = t

    # ---------- selection ----------
    def _score(self, t: Table, q: set) -> float:
        score = 3.0 * len(q & t.tokens) + 1.0 * len(q & t.col_tokens) + 2.0 * len(q & t.value_tokens)
        vocab = list(t.tokens | t.col_tokens)
        matched = (q & t.tokens) | (q & t.col_tokens)
        for w in q - matched:
            if len(w) > 3 and difflib.get_close_matches(w, vocab, n=1, cutoff=0.82):
                score += 0.5
        return score

    def select(self, question: str, k: int | None = None) -> list[Table]:
        k = k or config.TOP_K_TABLES
        q = {_stem(w) for w in _tokens(question) if w not in STOPWORDS}
        for w in list(q):
            q |= {_stem(s) for s in SYNONYMS.get(w, ())}
        scored = sorted(((self._score(t, q), n) for n, t in self.tables.items()), reverse=True)
        chosen = [n for s, n in scored if s > 0][:k]
        if not chosen:  # nothing matched: fall back to the first k tables
            chosen = [n for _, n in scored][:k]
        # add join neighbours (tables linked by a foreign key) while there is room
        for n in list(chosen):
            for col in self.tables[n].columns:
                if col.fk:
                    nb = col.fk.split(".")[0]
                    if nb not in chosen and len(chosen) < k:
                        chosen.append(nb)
            for other in self.tables.values():
                if other.name not in chosen and len(chosen) < k and any(
                        c.fk and c.fk.startswith(n + ".") for c in other.columns):
                    chosen.append(other.name)
        return [self.tables[n] for n in chosen]

    # ---------- rendering ----------
    def render(self, tables: list[Table] | None = None) -> str:
        tables = tables if tables is not None else list(self.tables.values())
        lines = []
        for t in tables:
            head = f"TABLE {t.name}" + (f"  -- {t.meaning}" if t.meaning else "")
            lines.append(head + f"  ({t.row_count} rows)")
            for c in t.columns:
                parts = [f"  {c.name} {c.type}"]
                if c.pk:
                    parts.append("PK")
                if c.fk:
                    parts.append(f"FK -> {c.fk}")
                notes = []
                if c.meaning:
                    notes.append(c.meaning)
                if c.enum_values:
                    notes.append("values: " + ", ".join(f"'{v}'" for v in c.enum_values))
                if notes:
                    parts.append("-- " + "; ".join(notes))
                lines.append(" ".join(parts))
            lines.append("")
        return "\n".join(lines).strip()


_INDEXES: dict[str, SchemaIndex] = {}


def get_index(db_name: str, refresh: bool = False) -> SchemaIndex:
    if refresh or db_name not in _INDEXES:
        _INDEXES[db_name] = SchemaIndex(db_name)
    return _INDEXES[db_name]
