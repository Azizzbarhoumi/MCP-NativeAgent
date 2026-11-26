"""LLM client routing: the right URL and headers per provider, and no retry on key/budget errors."""

import httpx
import pytest

from common import llm


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self.headers, self.text = status, {}, "{}"
        self._body = body or {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 3}}

    def json(self):
        return self._body


def test_ollama_needs_no_key_and_uses_local_url(monkeypatch):
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(url=url, headers=headers)
        return FakeResp()

    monkeypatch.setattr(httpx, "post", post)
    assert llm.chat([{"role": "user", "content": "hi"}], task_id="t", provider="ollama", model="llama3.1") == "ok"
    assert seen["url"].endswith("/v1/chat/completions") and "11434" in seen["url"]
    assert "Authorization" not in seen["headers"]


def test_budget_error_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append(1) or FakeResp(402))
    monkeypatch.setattr(llm.settings, "groq_api_key", llm.settings.groq_api_key or __import__("pydantic").SecretStr("x"))
    with pytest.raises(llm.ProviderAccessError):
        llm.chat([{"role": "user", "content": "hi"}], task_id="t", provider="groq", model="m")
    assert len(calls) == 1


def test_write_timeout_and_dropped_connection_are_retried(monkeypatch):
    """After the PC slept, a request can fail with a write or pool timeout; that must be retried,
    not crash or hang the run."""
    errors = [httpx.WriteTimeout("w"), httpx.ReadError("dropped")]
    def post(*a, **k):
        if errors:
            raise errors.pop(0)
        return FakeResp()
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    assert llm.chat([{"role": "user", "content": "hi"}], task_id="t", provider="ollama", model="m") == "ok"
    assert errors == []
