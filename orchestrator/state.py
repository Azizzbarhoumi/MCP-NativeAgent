"""LangGraph state for the orchestrator loop.

The state is the single object passed between graph nodes. Each node returns a partial
update; the `Annotated[..., operator.add]` fields are *appended to* instead of replaced,
so nodes can add messages/evidence without copying the whole list.
"""

import operator
from typing import Annotated, TypedDict

from common.schemas import Evidence


class OrchestratorState(TypedDict):
    # Chat history as plain dicts ({"role": ..., "content": ...}) because common/llm.py
    # sends exactly that format to the model. No LangChain message objects needed.
    messages: Annotated[list[dict], operator.add]

    # One id per run; every trace and log line is tagged with it (README section 2).
    task_id: str

    # Reuses the shared Evidence schema: text, source (URL), tool name.
    evidence: Annotated[list[Evidence], operator.add]

    # Incremented by every node; used to enforce the 8-step cap.
    step_count: int

    # Tool call the model asked for: {"name": ..., "args": {...}} (None = nothing pending).
    pending_tool: dict | None

    # Set when the tool failed; the answer node then reports it instead of asking the model.
    tool_error: str | None

    # The final text returned to the user.
    final_answer: str | None
