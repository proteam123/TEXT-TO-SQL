"""Central configuration. Everything can be overridden from the .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# name -> SQLite file. Add your own database here (or see README "Use your own database").
DATABASES = {
    "business": DATA_DIR / "business.db",      # clean schema: customers / products / orders
    "enterprise": DATA_DIR / "enterprise.db",  # cryptic names + filler tables
}
CACHE_DB = DATA_DIR / "query_cache.db"
TOKEN_DB = DATA_DIR / "token_log.db"

TOP_K_TABLES = int(os.getenv("TOP_K_TABLES", "8"))   # tables sent to the LLM (raise for big schemas)
MAX_ROWS = int(os.getenv("MAX_ROWS", "200"))         # hard cap on returned rows
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))     # self-correction attempts


def fast_mode_default() -> bool:
    """FAST_MODE=1 skips the disambiguate + validate LLM calls (2 calls/query instead of 4)."""
    return os.getenv("FAST_MODE", "0") == "1"
