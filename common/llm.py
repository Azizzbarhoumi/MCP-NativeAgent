"""Model-agnostic LLM client. Agents call `chat(...)` and never know which provider runs.

Mistral and Groq both speak the OpenAI chat-completions format, so one HTTP function serves
both. The default provider/model come from .env (LLM_PROVIDER, LLM_MODEL); a caller can
override them, e.g. the eval judge uses Groq so it never grades its own model's answers.
Every call is recorded as a Langfuse "generation" inside the task's trace.
"""

import time

import httpx

from common.config import settings
from common.tracing import get_trace

PROVIDERS = {
    # name: (url, settings attribute holding the key, settings attribute: min seconds between calls)
    "mistral": ("https://api.mistral.ai/v1/chat/completions", "mistral_api_key", "llm_min_interval"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "groq_api_key", "groq_min_interval"),
    # Local models through Ollama's OpenAI-compatible endpoint: no key, no rate limit.
    "ollama": (None, None, None),
}

# Running token count per task_id (prompt + completion), read by the eval harness.
TOKENS: dict[str, int] = {}
# Per task_id: one (agent, provider, model, prompt_tokens, completion_tokens) per call, for cost.
USAGE: dict[str, list[tuple]] = {}

class ProviderUnavailable(RuntimeError):
    """Still rate-limited (429) or unreachable after every retry. The run can resume later,
    so long benchmarks save what they have and stop instead of recording a failure."""


class ProviderAccessError(RuntimeError):
    """401/402/403: bad key, no budget or no access. Retrying or moving on to the next
    question cannot help, so callers should stop the run."""


_last_call: dict[str, float] = {}  # per provider, so the judge never waits on Mistral's spacing


def _call_openai_compatible(provider: str, model: str, messages: list[dict],
                            temperature: float, json_mode: bool = False) -> tuple[str, dict]:
    url, key_name, interval_name = PROVIDERS[provider]
    if provider == "ollama":
        url, key, min_interval = settings.ollama_base_url.rstrip("/") + "/v1/chat/completions", None, 0.0
    else:
        key, min_interval = getattr(settings, key_name), getattr(settings, interval_name)
        if key is None:
            raise RuntimeError(f"{key_name.upper()} is not set in .env")
    # Retry on 429 (rate limit) and 5xx (server busy) with exponential backoff, capped at 60s.
    for attempt in range(7):
        time.sleep(max(0.0, min_interval - (time.monotonic() - _last_call.get(provider, 0.0))))
        _last_call[provider] = time.monotonic()
        try:
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {key.get_secret_value()}"} if key else {},
                json={"model": model, "messages": messages, "temperature": temperature,
                      # json_object: the provider guarantees syntactically valid JSON (both support it)
                      **({"response_format": {"type": "json_object"}} if json_mode else {})},
                timeout=300 if provider == "ollama" else 60,   # a local model can be slow
            )
        except httpx.TransportError as e:
            # Any network failure (DNS, Wi-Fi, connect/read/write/pool timeout, dropped connection,
            # e.g. after the PC slept) is retried with the same backoff as a 429, then given up.
            if attempt == 6:
                raise ProviderUnavailable(f"{provider}: network error after 7 attempts: {e}") from e
            wait = min(5 * 2 ** attempt, 60)
            print(f"[llm] network error from {provider} ({type(e).__name__}), retrying in {wait}s (attempt {attempt + 1}/7)")
            time.sleep(wait)
            continue
        if resp.status_code != 429 and resp.status_code < 500:
            break
        wait = float(resp.headers.get("retry-after", min(5 * 2 ** attempt, 60)))
        print(f"[llm] {resp.status_code} from {provider}, retrying in {wait:.0f}s (attempt {attempt + 1}/7)")
        time.sleep(wait)
    if resp.status_code == 429:
        raise ProviderUnavailable(f"{provider}: still rate-limited (429) after 7 attempts")
    if resp.status_code in (401, 402, 403):
        raise ProviderAccessError(f"{provider} HTTP {resp.status_code}: {resp.text[:300]}")
    if resp.status_code >= 400:
        # Show the provider's reason (bad key, unknown model, region block...). The body never
        # contains our key, so it is safe to print.
        raise RuntimeError(f"{provider} HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"], data.get("usage", {})


def chat(
    messages: list[dict],
    task_id: str,
    agent: str = "orchestrator",
    temperature: float = 0.9,
    provider: str | None = None,
    model: str | None = None,
    json_mode: bool = False,
) -> str:
    """Send chat messages to an LLM and return the reply text (default: the .env provider)."""
    provider, model = provider or settings.llm_provider, model or settings.llm_model
    if provider not in PROVIDERS:
        raise NotImplementedError(f"Provider {provider!r} not implemented yet")

    trace = get_trace(task_id, agent)
    gen = trace.generation(name=f"{agent}.llm", model=f"{provider}/{model}", input=messages) if trace else None
    text, usage = _call_openai_compatible(provider, model, messages, temperature, json_mode)

    TOKENS[task_id] = TOKENS.get(task_id, 0) + (usage.get("total_tokens") or 0)
    USAGE.setdefault(task_id, []).append((agent, provider, model, usage.get("prompt_tokens") or 0,
                                          usage.get("completion_tokens") or 0))
    if gen:
        gen.end(
            output=text,
            usage={"input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens")},
        )
    return text
