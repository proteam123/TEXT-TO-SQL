"""Database access. Every connection is read-only at the engine level (defense in depth:
the SQL guard checks the text, SQLite itself refuses writes)."""

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import URL

import config

_ENGINES = {}


def get_engine(name: str):
    if name not in config.DATABASES:
        raise ValueError(f"Unknown database '{name}'. Available: {list(config.DATABASES)}")
    if name not in _ENGINES:
        path = config.DATABASES[name]
        if not path.exists():
            raise FileNotFoundError(f"{path} not found. Run: python seed_db.py")
        engine = create_engine(
            URL.create("sqlite", database=str(path)),
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def _read_only(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA query_only = ON")  # any INSERT/UPDATE/DROP now raises an error
            cur.close()

        _ENGINES[name] = engine
    return _ENGINES[name]


def databases_status() -> list[dict]:
    out = []
    for name, path in config.DATABASES.items():
        info = {"name": name, "path": str(path), "exists": path.exists(), "tables": None}
        if path.exists():
            info["tables"] = len(inspect(get_engine(name)).get_table_names())
        out.append(info)
    return out


def table_previews(name: str, rows: int = 5) -> list[dict]:
    engine = get_engine(name)
    out = []
    with engine.connect() as conn:
        for t in inspect(engine).get_table_names():
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
            res = conn.execute(text(f'SELECT * FROM "{t}" LIMIT {int(rows)}'))
            out.append({"table": t, "row_count": count, "columns": list(res.keys()),
                        "preview": [dict(r._mapping) for r in res]})
    return out
