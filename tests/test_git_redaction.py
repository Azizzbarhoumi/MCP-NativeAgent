"""Log redaction: credentials must never leave the git tool (remote URLs, rated commands)."""

from mcp_servers.git_local.inspect_repo import redact_url
from mcp_servers.git_local.risk import classify


def test_redact_token_in_https_url():
    assert redact_url("https://user:ghp_secret123@github.com/a/b.git") == "https://***@github.com/a/b.git"


def test_plain_urls_unchanged():
    for url in ["https://github.com/a/b.git", "git@github.com:a/b.git", "C:/repos/origin.git"]:
        assert redact_url(url) == url


def test_command_with_token_is_redacted():
    r = classify("git remote set-url origin https://me:ghp_abcdefghijklmnopqrstuvwxyz@github.com/a/b")
    assert r["command"] == "<redacted>" and r["tier"] == "destructive"
