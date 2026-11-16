"""Shared test fixtures. Tests never call the real OpenAI API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.pipeline.llm_client import LLMError
from app.pipeline.prompts import UNDERSTANDING_SYSTEM

SUFFICIENT = {
    "intent": "learn python", "intent_description": "Find Python learning material.",
    "spelling_corrections": [], "context_sufficient": True, "ambiguity": "none",
    "ambiguity_reason": "", "missing_context": [],
}
INSUFFICIENT = {
    "intent": "find courses", "intent_description": "Find a course.",
    "spelling_corrections": [], "context_sufficient": False, "ambiguity": "low",
    "ambiguity_reason": "No topic given.", "missing_context": ["topic", "skill level"],
}


class FakeLLM:
    """Stands in for OpenAI. Records every call so tests can assert on cost and privacy."""

    model_name = "fake-model"

    def __init__(self, understanding=None, rewrite=None, fail_on: str | None = None):
        self.understanding = understanding or SUFFICIENT
        self.rewrite = rewrite or {"rewritten_query": "courses", "context_added": [], "explanation": ""}
        self.fail_on = fail_on
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        stage = "understand" if system_prompt == UNDERSTANDING_SYSTEM else "rewrite"
        self.calls.append((stage, user_prompt))
        if self.fail_on == stage:
            raise LLMError("Simulated OpenAI outage.")
        value = self.understanding if stage == "understand" else self.rewrite
        return value(user_prompt) if callable(value) else dict(value)

    def stages(self) -> list[str]:
        return [stage for stage, _ in self.calls]


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def _make(llm=None, use_llm=True, **overrides):
        settings = Settings(
            _env_file=None,  # ignore the developer's real .env
            database_url=f"sqlite:///{(tmp_path / f'test{len(clients)}.db').as_posix()}",
            openai_api_key="test-key",
            rate_limit_per_minute=overrides.pop("rate_limit_per_minute", 1000),
            **overrides,
        )
        app = create_app(settings, llm_client=(llm or FakeLLM()) if use_llm else None)
        client = TestClient(app)
        client.__enter__()  # runs startup (creates tables)
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


def create_user(client, **fields) -> dict:
    body = {"name": "Priya", "email": "priya@example.com", "role": "Data analyst",
            "location": "Hyderabad", "interests": ["SQL", "Power BI"],
            "preferences": {"level": "intermediate"}}
    body.update(fields)
    res = client.post("/api/users", json=body)
    assert res.status_code == 201, res.text
    return res.json()
