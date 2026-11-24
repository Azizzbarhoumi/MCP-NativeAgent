"""Langfuse tracing. One trace per task_id; every LLM and tool call is recorded inside it.

If Langfuse keys are missing, tracing silently turns off, so services still run
(README rule: every component can be switched off without breaking the others).
"""

from functools import lru_cache

from langfuse import Langfuse

from common.config import settings


@lru_cache
def get_langfuse() -> Langfuse | None:
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
    )


def get_trace(task_id: str, agent: str = "orchestrator", **metadata):
    """Return the trace for this task_id (same id = same trace across agents)."""
    lf = get_langfuse()
    if lf is None:
        return None
    return lf.trace(id=task_id, name=f"task-{task_id[:8]}", metadata=metadata, tags=[agent])


def flush() -> None:
    """Send buffered events now (call before a script exits)."""
    lf = get_langfuse()
    if lf is not None:
        lf.flush()
