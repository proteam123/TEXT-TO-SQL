"""Tests run fully offline: a temp DATA_DIR and a scripted FakeLLM instead of a real model."""

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="t2s_tests_")
os.environ["DATA_DIR"] = _TMP
os.environ.pop("FAST_MODE", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import seed_db  # noqa: E402
import config  # noqa: E402

seed_db.seed_business(config.DATABASES["business"])
seed_db.seed_enterprise(config.DATABASES["enterprise"])

import pytest  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402


class FakeLLM:
    """Looks at the TASK tag in the system prompt and replies from a script."""

    def __init__(self, sql_for=None, plausible=None, in_scope=True):
        self.sql_for = sql_for or {}      # question -> list of SQL replies (consumed in order)
        self.plausible = plausible or []  # replies for validate_result (True/False), consumed in order
        self.in_scope = in_scope
        self.calls = []

    def invoke(self, messages):
        system, user = messages[0].content, messages[1].content
        task = system.split("\n", 1)[0].replace("TASK:", "").strip()
        self.calls.append((task, user))
        if task == "check_relevance":
            out = '{"in_scope": true}' if self.in_scope else '{"in_scope": false}'
        elif task == "disambiguate":
            out = '{"assumptions": ["Revenue counts completed orders only."]}'
        elif task == "generate_sql":
            q = user.split("Question: ", 1)[1].split("\n", 1)[0]
            replies = self.sql_for[q]
            out = replies.pop(0) if len(replies) > 1 else replies[0]
        elif task == "validate_result":
            ok = self.plausible.pop(0) if self.plausible else True
            out = '{"plausible": true, "issue": ""}' if ok else \
                '{"plausible": false, "issue": "Cancelled orders are included."}'
        else:
            out = "Here is the answer."
        return AIMessage(content=out, usage_metadata={"input_tokens": 100, "output_tokens": 10,
                                                      "total_tokens": 110})

    def count(self, task):
        return sum(1 for t, _ in self.calls if t == task)


@pytest.fixture
def fake_llm(monkeypatch):
    import llm
    import query_cache

    query_cache.clear()

    def install(**kw):
        fake = FakeLLM(**kw)
        monkeypatch.setattr(llm, "get_llm", lambda: fake)
        return fake

    return install
