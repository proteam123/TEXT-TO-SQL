"""LLM access. Pick a FREE provider in .env:

  LLM_PROVIDER=groq    -> Groq free tier   (fast, needs GROQ_API_KEY)          [default]
  LLM_PROVIDER=gemini  -> Google AI Studio free tier (needs GEMINI_API_KEY)
  LLM_PROVIDER=ollama  -> runs locally on your PC, no key, no limits (needs Ollama installed)

Every call goes through ask(), which also records token usage and latency.
"""

import json
import os
import re
import time
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage

import token_tracker

DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-120b",        # also try: llama-3.3-70b-versatile, llama-3.1-8b-instant
    "gemini": "gemini-3.5-flash",
    "ollama": "qwen2.5-coder:7b",
}


def provider() -> str:
    return os.getenv("LLM_PROVIDER", "groq").lower()


def model_name() -> str:
    return os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(provider(), "unknown")


def _need(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"{key} is not set. Add it to your .env file (see .env.example).")
    return val


@lru_cache(maxsize=1)
def get_llm():
    p, model = provider(), model_name()
    if p == "groq":
        from langchain_groq import ChatGroq

        kwargs = {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}
        return ChatGroq(model=model, temperature=0, api_key=_need("GROQ_API_KEY"),
                        max_retries=4, **kwargs)  # max_retries backs off on free-tier 429s
    if p == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, temperature=0,
                                      google_api_key=_need("GEMINI_API_KEY"), max_retries=4)
    if p == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=model, temperature=0,
                          base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    raise ValueError(f"Unknown LLM_PROVIDER '{p}'. Use groq, gemini or ollama.")


def _to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # some providers return a list of parts
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)


def ask(node: str, run_id: str, system: str, user: str) -> str:
    """One LLM call, with usage logged under `node`."""
    llm = get_llm()
    t0 = time.perf_counter()
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    ms = int((time.perf_counter() - t0) * 1000)
    usage = getattr(resp, "usage_metadata", None) or {}
    token_tracker.log_call(run_id, node, model_name(), usage.get("input_tokens", 0),
                           usage.get("output_tokens", 0), ms)
    return _to_text(resp.content).strip()


def clean_sql(text: str) -> str:
    """Models sometimes wrap SQL in ```sql fences or add prose; keep just the query."""
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1)
    return text.strip().rstrip(";").strip()


def parse_json(text: str) -> dict:
    """Extract the first {...} object from a model reply; {} if there is none."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
