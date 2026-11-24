import pytest
from pydantic import ValidationError

from common.schemas import ActionProposal, AgentResponse, Evidence, Risk, TaskState


def test_agent_response_roundtrip():
    r = AgentResponse(answer="hi", evidence=[Evidence(text="t", source="doc1", tool="vector_search")], confidence=0.8)
    assert AgentResponse.model_validate_json(r.model_dump_json()) == r


def test_confidence_must_be_between_0_and_1():
    with pytest.raises(ValidationError):
        AgentResponse(answer="x", confidence=1.5)


def test_action_proposal_gets_unique_idempotency_key():
    a = ActionProposal(tool="github.create_issue", risk=Risk.WRITE, preview="Create issue")
    b = ActionProposal(tool="github.create_issue", risk="write", preview="Create issue")
    assert a.idempotency_key != b.idempotency_key


def test_task_gets_task_id():
    assert TaskState(user_message="plan my week").task_id
