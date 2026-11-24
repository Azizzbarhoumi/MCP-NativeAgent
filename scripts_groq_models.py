"""List the Groq models your key can use (ids only). Usage: python scripts_groq_models.py"""

import httpx

from common.config import settings

r = httpx.get("https://api.groq.com/openai/v1/models",
              headers={"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"}, timeout=30)
r.raise_for_status()
for m in sorted(r.json()["data"], key=lambda m: m["id"]):
    print(f"{m['id']:50} context={m.get('context_window')}  owner={m.get('owned_by')}")
