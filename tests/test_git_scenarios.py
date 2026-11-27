"""Every scenario, end to end with no LLM: it builds, its broken repo FAILS its check, the known-good
fix PASSES it and keeps every invariant, and the beginner mistake is caught (state check fails,
an invariant breaks, or the tool refuses a step). Also: rubric for every scenario, unique ids,
held-out naming, and benchmark data never reachable by the knowledge ingestion.
"""

import pytest

from eval.git_scenarios import check_fixes as cf
from eval.git_scenarios import rubric
from eval.git_scenarios.checks import CHECKS, check, invariants, snapshot
from eval.git_scenarios.scenarios import SCENARIOS, scenario_dict

IDS = [b.__name__ for b in SCENARIOS]


@pytest.fixture
def ex(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_TOOL_ALLOWED_ROOTS", str(tmp_path))
    from mcp_servers.git_local import execute
    monkeypatch.setattr(execute, "LOG", tmp_path / "exec.jsonl")
    return execute


def test_catalog():
    assert len(SCENARIOS) >= 15 and len(set(IDS)) == len(IDS)
    for b, sid in zip(SCENARIOS, IDS):
        assert sid in cf.REFERENCE and sid in cf.BAD, sid
        rubric.load(sid)                                   # every scenario has a valid rubric


@pytest.mark.parametrize("build", SCENARIOS, ids=IDS)
def test_scenario_broken_reference_and_mistake(build, tmp_path, ex):
    sid = build.__name__
    results = {}
    for variant, steps in [("broken", []), ("reference", cf.REFERENCE[sid]), ("bad", cf.BAD[sid][1])]:
        root = tmp_path / variant
        root.mkdir()
        key = build(root)
        assert key.id == sid and key.held_out == sid.startswith("ho_")
        assert (root / key.repo / ".git").is_dir()
        if variant == "bad" and sid in cf.SETUP_BAD:
            cf.SETUP_BAD[sid](root / key.repo)
        snap = snapshot(root, key.repo)
        log = cf.run_steps(root / key.repo, steps, ex, f"t-{variant}", sid)
        if variant == "bad" and sid in cf.BYPASS:
            cf.BYPASS[sid](root / key.repo)
        results[variant] = (check(sid, root, snap), invariants(root, snap, scenario_dict(key), log), log)

    state = {v: r[0]["passed"] for v, r in results.items()}
    if sid in CHECKS:
        assert state["broken"] is False, "the check must fail on the broken repo"
        assert state["reference"] is True, results["reference"][0]
    else:
        assert key.grading == "rubric" and set(state.values()) == {None}
    ref_inv = results["reference"][1]
    assert ref_inv["passed"], [c for c in ref_inv["checks"] if not c["ok"]]
    assert all(s["result"] == "ok" for s in results["reference"][2]), results["reference"][2]
    bad_state, bad_inv, bad_log = results["bad"]
    caught = bad_state["passed"] is False or not bad_inv["passed"] or any(s["result"] != "ok" for s in bad_log)
    assert caught, f"the mistake '{cf.BAD[sid][0]}' was not caught"


def test_benchmark_data_is_not_ingested():
    """ADR 0001: the ingestion guard refuses every scenario file (builders, rubrics, answer keys)."""
    from pathlib import Path

    from knowledge.sources.base import assert_allowed
    folder = Path(cf.__file__).parent
    files = list(folder.glob("*.py")) + list((folder / "rubrics").glob("*.yaml"))
    assert len(files) > 20
    for f in files:
        with pytest.raises(PermissionError):
            assert_allowed(f)
