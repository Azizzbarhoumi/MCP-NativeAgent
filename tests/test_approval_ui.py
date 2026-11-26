"""The human approval page: approve or reject a prepared step outside the model's context.

Each test drives the real page over HTTP on 127.0.0.1 and checks that the model-facing side
(prepare_for_model, step_status, execute_with_code and the MCP tools) never sees the code or
the page address.
"""

import inspect
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import pytest

from mcp_servers.git_local import approval_ui as ui
from mcp_servers.git_local import execute as ex

ENV = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **ENV}).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    r = allowed / "repo"
    r.mkdir(parents=True)
    monkeypatch.setenv("GIT_TOOL_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setattr(ex, "LOG", tmp_path / "exec.jsonl")
    monkeypatch.setattr(ex, "APPROVALS_LOG", tmp_path / "approvals.log")
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("a\n")
    git(r, "add", "a.txt")
    git(r, "commit", "-q", "-m", "init")
    return r


@pytest.fixture
def page(repo, monkeypatch):
    monkeypatch.setattr(ex, "_STEPS", {})                      # no steps left over from other tests
    url = ui.start(0)
    yield url
    ui.stop()


def get(url, host=None):
    req = urllib.request.Request(url, headers={"Host": host} if host else {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read().decode()


def post(url, data, origin=None, host=None):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if origin:
        headers["Origin"] = origin
    if host:
        headers["Host"] = host
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:      # follows the 303 back to the page
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_page_lists_the_step_and_approve_lets_the_model_run_it(repo, page, capsys):
    p = ex.prepare_for_model(str(repo), "create_branch", {"name": "save"})
    status, html = get(page)
    assert status == 200 and "git branch --end-of-options save" in html and p["step_id"] in html
    assert ex.step_status(p["step_id"])["status"] == "pending"
    assert not ex.execute_with_code(p["step_id"])["ok"]                       # not approved yet
    status, html = post(page + "approve", {"step_id": p["step_id"]})
    assert status == 200 and "Approved" in html
    assert ex.step_status(p["step_id"])["status"] == "approved"
    r = ex.execute_with_code(p["step_id"])                                    # no code needed now
    assert r["ok"] and "save" in git(repo, "branch", "--format=%(refname:short)").split()
    assert ex.step_status(p["step_id"])["status"] == "used"
    assert not ex.execute_with_code(p["step_id"])["ok"]                       # works once


def test_reject_cancels_the_step(repo, page):
    p = ex.prepare_for_model(str(repo), "create_branch", {"name": "nope"})
    post(page + "reject", {"step_id": p["step_id"]})
    assert ex.step_status(p["step_id"])["status"] == "rejected"
    assert not ex.execute_with_code(p["step_id"])["ok"]
    assert "nope" not in git(repo, "branch", "--format=%(refname:short)").split()


def test_dangerous_step_needs_the_phrase_typed_on_the_page(repo, page):
    (repo / "a.txt").write_text("edited\n")
    p = ex.prepare_for_model(str(repo), "reset_hard", {"rev": "HEAD"})
    assert p["tier"] == "dangerous"
    status, html = post(page + "approve", {"step_id": p["step_id"], "confirmation": "yes"})
    assert "type exactly" in html and ex.step_status(p["step_id"])["status"] == "pending"
    post(page + "approve", {"step_id": p["step_id"], "confirmation": p["confirm_phrase"]})
    r = ex.execute_with_code(p["step_id"])
    assert r["ok"] and (repo / "a.txt").read_text() == "a\n" and r["backups"]


def test_the_code_still_works_and_is_shown_only_on_the_page(repo, page, capsys):
    p = ex.prepare_for_model(str(repo), "create_branch", {"name": "viacode"})
    code = re.search(rf'step {p["step_id"]} .*?class="code">(\w{{6}})<', get(page)[1], re.S).group(1)
    assert code not in str(p) and code not in str(ex.step_status(p["step_id"]))
    assert ex.execute_with_code(p["step_id"], code)["ok"]


def test_model_facing_results_never_hold_the_page_address(repo, page):
    p = ex.prepare_for_model(str(repo), "create_branch", {"name": "x"})
    secret = page.rstrip("/").rsplit("/", 1)[1]
    for out in (p, ex.step_status(p["step_id"]), ex.execute_with_code(p["step_id"], "WRONG1")):
        assert secret not in str(out) and "127.0.0.1" not in str(out)
    assert secret in (ex.APPROVALS_LOG.read_text())               # the human's side has it


def test_wrong_secret_wrong_host_and_cross_site_post_are_refused(repo, page):
    p = ex.prepare_for_model(str(repo), "create_branch", {"name": "evil"})
    base = page.rsplit("/", 2)[0]
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/guess/")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:              # DNS-rebinding: foreign Host header
        get(page, host="evil.example:80")
    assert e.value.code == 404
    status, _ = post(page + "approve", {"step_id": p["step_id"]}, origin="https://evil.example")
    assert status == 403 and ex.step_status(p["step_id"])["status"] == "pending"


def test_commit_messages_are_escaped_on_the_page(repo, page):
    (repo / "a.txt").write_text("b\n")
    git(repo, "add", "a.txt")
    p = ex.prepare_for_model(str(repo), "commit", {"message": "<script>alert(1)</script>"})
    assert p["tier"] != "blocked", p
    html = get(page)[1]
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_mcp_tools_expose_status_but_no_approval_function():
    from mcp_servers.git_local import server
    names = {n for n, f in vars(server).items() if callable(f)}
    assert "step_status" in names and "execute_step" in names
    assert not {"approve_by_human", "reject_by_human", "pending_for_human"} & names
    assert inspect.signature(server.execute_step).parameters["approval_code"].default is None
