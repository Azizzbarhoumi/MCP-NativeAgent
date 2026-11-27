"""Show Groq's rate limits for some models: one tiny call each, then the limit headers.

  python scripts_groq_limits.py [model ...]

Groq sends x-ratelimit-limit-requests (requests per day) and x-ratelimit-limit-tokens
(tokens per minute) on every reply. The key is only sent, never printed.
"""

import sys

import httpx

from common.config import settings

MODELS = sys.argv[1:] or ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile",
                          "meta-llama/llama-4-maverick-17b-128e-instruct", "meta-llama/llama-4-scout-17b-16e-instruct",
                          "moonshotai/kimi-k2-instruct", "qwen/qwen3-32b", "llama-3.1-8b-instant"]

for model in MODELS:
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
                   headers={"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"},
                   json={"model": model, "messages": [{"role": "user", "content": "Say ok."}], "max_tokens": 5},
                   timeout=30)
    h = r.headers
    print(f"{model:48} HTTP {r.status_code}  requests/day={h.get('x-ratelimit-limit-requests')} "
          f"left={h.get('x-ratelimit-remaining-requests')}  tokens/min={h.get('x-ratelimit-limit-tokens')}"
          + ("" if r.status_code == 200 else f"  {r.text[:120]}"))
