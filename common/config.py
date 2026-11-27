"""Typed settings loaded from environment variables and the .env file.

Every service imports `settings` from here instead of reading os.environ directly,
so config has one source, one set of defaults, and types that are checked at startup.
"""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    llm_provider: Literal["ollama", "mistral", "groq", "openrouter", "gemini", "anthropic"] = "ollama"
    llm_model: str = "llama3.1"
    ollama_base_url: str = "http://localhost:11434"
    mistral_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    # OpenRouter serves the same gpt-oss models under the same names as Groq
    # (openai/gpt-oss-20b, openai/gpt-oss-120b), so switching keeps results comparable.
    openrouter_api_key: SecretStr | None = None
    # Spacing between calls, in seconds (free tiers rate-limit by requests AND tokens per minute).
    # Override with LLM_MIN_INTERVAL / GROQ_MIN_INTERVAL in .env or the shell.
    llm_min_interval: float = 5.0
    groq_min_interval: float = 2.5
    openrouter_min_interval: float = 0.5
    # Groundedness checker inside the pipeline: a third model (not the answerer, not the judge).
    checker_provider: str = "groq"
    checker_model: str = "openai/gpt-oss-20b"
    # Query decomposition classifier/splitter (cheap, fast model; spares the Mistral quota).
    decomposer_provider: str = "groq"
    decomposer_model: str = "openai/gpt-oss-20b"
    groq_judge_model: str = "openai/gpt-oss-120b"  # set GROQ_JUDGE_MODEL in .env to change
    judge_provider: str = "groq"   # JUDGE_PROVIDER=openrouter runs the same judge model there
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    # Stores
    qdrant_url: str = "http://localhost:6333"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("change-me-please")

    # Tracing
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None

    # Tools
    github_token: SecretStr | None = None
    google_calendar_credentials: str | None = None
    search_api_key: SecretStr | None = None


settings = Settings()
