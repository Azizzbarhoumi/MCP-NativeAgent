"""Phase 7: troubleshooting chains in the graph viewer.

repo_state -> error -> cause -> command -> concept -> doc section, built from the hand-written
playbook (never from eval data), with risk tiers taken from risk.py.
"""

import pytest

from knowledge.graph import troubleshoot as ts
from knowledge.sources.base import FORBIDDEN
from mcp_servers.git_local.risk import classify

PB = ts.load()


def fake_docs():
    """A tiny doc graph shaped like export.build_full's output."""
    return {"collection": "git_docs", "edges": [], "nodes": [
        {"id": "doc:a", "type": "document", "path": "Documentation/git-merge.adoc"},
        {"id": "sec:1", "type": "section", "path": "Documentation/git-merge.adoc", "section": "git-merge(1) > TRUE MERGE"},
        {"id": "sec:2", "type": "section", "path": "Documentation/git-merge.adoc", "section": "git-merge(1) > TRUE MERGE > deeper"},
    ]}


def test_playbook_is_not_benchmark_data():
    assert not ts.PLAYBOOK.resolve().is_relative_to(FORBIDDEN.resolve())
    assert PB["collection"] == "git_docs"


def test_every_reference_in_the_playbook_exists():
    for eid, e in PB["errors"].items():
        assert e["message"] and e["causes"], eid
        assert e["states"] and all(s in PB["repo_states"] for s in e["states"]), eid
        for cause in e["causes"]:
            assert cause["commands"], (eid, cause["text"])
            for step in cause["commands"]:
                assert step["concepts"], (eid, step["cmd"])
                assert all(c in PB["concepts"] for c in step["concepts"]), (eid, step["cmd"])
    used = {c for e in PB["errors"].values() for cause in e["causes"] for s in cause["commands"] for c in s["concepts"]}
    assert used == set(PB["concepts"]), set(PB["concepts"]) - used     # no orphan concepts
    assert all(c["docs"] for c in PB["concepts"].values())


def test_no_secret_or_token_in_any_command():
    for e in PB["errors"].values():
        for cause in e["causes"]:
            for step in cause["commands"]:
                assert classify(step["cmd"])["command"] != "<redacted>", step["cmd"]


def test_command_tiers_come_from_risk_py():
    g = ts.build_chains(None, PB)
    cmds = {n["label"]: n for n in g["nodes"] if n["type"] == "command"}
    assert cmds["git push --force-with-lease origin feature"]["tier"] == "destructive"
    assert cmds["git reset --soft HEAD~1"]["tier"] == "careful"
    assert cmds["git status"]["tier"] == "safe"
    for label, n in cmds.items():
        assert n["tier"] == ("manual" if n["manual"] else classify(label)["tier"]), label


def test_every_edge_goes_one_column_right():
    g = ts.build_chains(fake_docs(), PB)
    level = {n["id"]: n["level"] for n in g["nodes"]}
    level.update({"doc:a": 5, "sec:1": 5, "sec:2": 5})
    for e in g["edges"]:
        assert level[e["target"]] == level[e["source"]] + 1, e      # a DAG with fixed columns


def test_doc_links_resolve_or_are_reported_never_invented():
    pb = {"collection": "git_docs", "repo_states": {"s": {"label": "S"}},
          "concepts": {"m": {"label": "M", "docs": ["git-merge.adoc#true merge", "git-merge.adoc", "nowhere.md#x"]}},
          "errors": {"e": {"message": "boom", "states": ["s"],
                           "causes": [{"text": "c", "commands": [{"cmd": "git status", "concepts": ["m"]}]}]}}}
    g = ts.build_chains(fake_docs(), pb)
    targets = {e["target"] for e in g["edges"] if e["relation"] == "EXPLAINED_IN"}
    assert targets == {"sec:1", "doc:a"}                      # shallowest matching heading wins
    assert g["unresolved"] == ["m: nowhere.md#x"]


def test_one_error_view_does_not_leak_other_errors_concepts():
    """'git status' is shared by merge and rebase conflicts; the merge chain must not show the
    rebase concept just because the command node is shared."""
    merged = ts.merge(fake_docs(), ts.build_chains(fake_docs(), PB))
    sub = ts.chain_subgraph(merged, "merge_conflict")
    ids = {n["id"] for n in sub["nodes"]}
    assert "concept:merge_conflict" in ids and "concept:rebase_conflict" not in ids
    assert "repo_state:unmerged_paths" in ids and "error:rebase_conflict" not in ids
    assert {n["type"] for n in sub["nodes"]} >= set(ts.CHAIN_TYPES)
    with pytest.raises(KeyError):
        ts.chain_subgraph(merged, "no_such_error")


def test_all_doc_links_resolve_in_the_real_corpus():
    from knowledge.sources.local_docs import all_adapters
    if not all_adapters("git_docs"):
        pytest.skip("git_docs corpus not downloaded (python -m knowledge.acquire --collection git_docs)")
    from knowledge.graph.export import build_full
    g = ts.build_chains(build_full("git_docs"), PB)
    assert g["unresolved"] == []


def test_viewer_is_standalone():
    html = (ts.Path(ts.__file__).parent / "viewer" / "template.html").read_text(encoding="utf-8")
    assert "<script src=" not in html and "http://" not in html.replace("http://localhost", "")
    assert "drawChainNode" in html and "chainSelection" in html
