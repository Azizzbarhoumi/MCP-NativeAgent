"""Shared data contracts. Every agent speaks these shapes, so any agent can be swapped
or tested on its own. Pydantic validates data at every boundary (API, A2A, tool output).
"""

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id() -> str:
    return str(uuid4())


class Evidence(BaseModel):
    """One piece of support for an answer: where it came from and which tool found it."""

    text: str
    source: str = Field(description="URL or doc_id")
    tool: str


class AgentResponse(BaseModel):
    """What every agent returns: an answer, its evidence, and how sure it is."""

    answer: str
    evidence: list[Evidence] = []
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Risk(str, Enum):
    READ = "read"                # runs freely
    WRITE = "write"              # needs human approval
    DESTRUCTIVE = "destructive"  # blocked in v1


class ActionProposal(BaseModel):
    """A side effect the Action agent wants to perform, shown to the user before it runs."""

    tool: str                                   # e.g. "github.create_issue"
    arguments: dict[str, Any] = {}
    risk: Risk
    preview: str                                # human-readable description
    idempotency_key: str = Field(default_factory=new_id)  # retrying never runs it twice


class PlanStep(BaseModel):
    """One step of the orchestrator's plan."""

    agent: str                  # research | knowledge | action | verifier
    instruction: str
    expected_result: str


class TaskState(BaseModel):
    """Everything known about one user request as it moves through the system."""

    task_id: str = Field(default_factory=new_id)  # tags every trace and log line
    user_message: str
    plan: list[PlanStep] = Field(default=[], max_length=8)  # step limit from the README
    results: list[AgentResponse] = []
    pending_actions: list[ActionProposal] = []
    final_answer: str | None = None
