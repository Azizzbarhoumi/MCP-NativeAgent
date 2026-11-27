"""Phase 6: the Git/GitHub docs collection (git_docs).

Offline part (always runs): the manifest has exactly the three git sources, each with a license
and attribution, and no source folder points inside eval/.

Live part (skips unless Qdrant is reachable, git_docs exists and the embedding model loads):
hand-written beginner questions must retrieve the page that answers them in the top 5.
The questions were written for this test; none comes from the benchmark (eval/), and nothing
here is indexed. A miss is a real retrieval miss: do not loosen the expected pages to pass.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "knowledge" / "corpus_manifest.yaml"
GIT_SOURCES = {"git_official_docs", "github_git_docs", "progit_book"}
COLLECTION = "git_docs"

# question -> any of these path fragments counts as the right page (top 5, hybrid mode)
PROBES = [
    ("How do I undo my last commit but keep the changes in my files?",
     ["git-reset", "undoing", "reset"]),
    ("git push says updates were rejected because the remote contains work that I do not have locally",
     ["non-fast-forward", "git-push", "pushing", "remotes"]),
    ("What does 'You are in detached HEAD state' mean and how do I get back to a branch?",
     ["git-checkout", "git-switch", "detached"]),
    ("remote: Support for password authentication was removed. What should I use instead?",
     ["personal-access-token", "about-authentication", "caching-your-github-credentials",
      "git-credential", "gitcredentials", "https"]),
    ("ssh says Permission denied (publickey) when I connect to GitHub",
     ["publickey", "troubleshooting-ssh", "ssh"]),
    ("How do I resolve a merge conflict from the command line?",
     ["merge-conflict", "git-merge", "basic-branching-and-merging", "mergetool"]),
    ("Stop tracking a file but keep it on disk",
     ["git-rm", "recording-changes", "gitignore", "ignoring"]),
    ("Save my uncommitted work temporarily so I can switch branches",
     ["git-stash", "stashing"]),
    ("I lost a commit after a reset, how can I find it again?",
     ["git-reflog", "reflog", "revision-selection", "maintenance"]),
    ("Push a new local branch and set its upstream",
     ["git-push", "git-branch", "remote-branches", "pushing"]),
]


def _manifest_sources():
    return [s for s in yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["sources"]
            if s.get("collection") == COLLECTION]


def test_manifest_has_the_three_git_sources_with_licenses():
    sources = _manifest_sources()
    assert {s["name"] for s in sources} == GIT_SOURCES
    for s in sources:
        assert s["license"] and s["attribution"] and s["commit"], s["name"]
        assert s["files"] > 0, s["name"]
        assert not any(f.startswith("eval") for f in s["folders"]), s["name"]


def test_git_docs_never_reads_eval():
    """The adapters read only knowledge/corpus/<source>/, and the guard refuses eval/ paths."""
    from knowledge.sources.base import FORBIDDEN, assert_allowed
    from knowledge.sources.local_docs import CORPUS
    assert not CORPUS.resolve().is_relative_to(FORBIDDEN.resolve())
    with pytest.raises(PermissionError):
        assert_allowed(FORBIDDEN / "git_scenarios" / "scenarios.py")


@pytest.fixture(scope="module")
def live():
    try:
        from knowledge.ingest.store import get_client
        client = get_client()
        if not client.collection_exists(COLLECTION):
            pytest.skip(f"Qdrant has no '{COLLECTION}' collection (run: python -m knowledge.ingest --collection git_docs)")
        count = client.count(COLLECTION).count
    except Exception as e:  # no server, no local folder
        pytest.skip(f"Qdrant not reachable: {type(e).__name__}")
    try:
        from knowledge.retrieve import search
        search("probe", k=1, collection=COLLECTION, mode="hybrid")
    except Exception as e:  # model not downloadable here
        pytest.skip(f"embedding model unavailable: {type(e).__name__}")
    return client, count


def test_collection_holds_only_git_sources(live):
    client, count = live
    assert count > 500
    seen, offset = set(), None
    while True:
        points, offset = client.scroll(COLLECTION, limit=500, offset=offset, with_payload=["source", "path"])
        for p in points:
            seen.add(p.payload["source"])
            assert not p.payload["path"].startswith("eval"), p.payload["path"]
        if offset is None:
            break
    assert seen <= GIT_SOURCES and seen, seen


@pytest.mark.parametrize("question,expected", PROBES, ids=[q[:40] for q, _ in PROBES])
def test_probe_question_retrieves_the_right_page(live, question, expected):
    from knowledge.retrieve import search
    hits = search(question, k=5, collection=COLLECTION, mode="hybrid")
    paths = [f"{h.source}:{h.path}" for h in hits]
    assert any(frag in p for p in paths for frag in expected), paths
