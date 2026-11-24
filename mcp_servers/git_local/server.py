"""Local git MCP server: read tools (no approval) + typed write steps behind approval.

  python -m mcp_servers.git_local.server          (stdio; started by the agent, not by hand)

Allowed folders: env GIT_TOOL_ALLOWED_ROOTS (separated by ; on Windows, : elsewhere).
Default: only eval/git_scenarios/build.
Writes: prepare_step gives the plan line (tier, explanation, what a dangerous step would lose)
and an approval token bound to that exact step; execute_step runs it only with that token
(plus the confirmation phrase for dangerous steps). There is no tool that runs raw commands.

Important for wiring: the token binds the step, it does not prove a human approved it.
The agent code (not the LLM) must keep the token, show the step to the user, and call
execute_step only after the user's click. Never put the token in the model's context.
"""

try:  # mcp 2.x renamed FastMCP to MCPServer; support both so the venv version doesn't matter
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:
    from mcp.server.fastmcp import FastMCP as Server

from mcp_servers.git_local import inspect_repo as ir
from mcp_servers.git_local import execute as ex
from mcp_servers.git_local.risk import classify

mcp = Server("git-local")


def _safe(fn, *args):
    try:
        return fn(*args)
    except ir.GitToolError as e:
        return {"error": str(e)}


@mcp.tool()
def repo_state(path: str) -> dict:
    """Branch, upstream, ahead/behind, merge/rebase in progress, conflicted/staged/unstaged/
    untracked files, remotes (credentials hidden), stash count, and warnings. Call this first."""
    return _safe(ir.repo_state, path)


@mcp.tool()
def git_log(path: str, n: int = 15, all_branches: bool = True) -> str | dict:
    """Recent commits as a graph with branch names (git log --oneline --decorate --graph)."""
    return _safe(ir.log, path, n, all_branches)


@mcp.tool()
def git_diff(path: str, staged: bool = False, file: str | None = None) -> str | dict:
    """Changes not yet committed (staged=True: what the next commit contains). Shows conflict markers."""
    return _safe(ir.diff, path, staged, file)


@mcp.tool()
def git_reflog(path: str, n: int = 15) -> str | dict:
    """Where HEAD has been. Use it to find 'lost' commits (detached HEAD, bad reset)."""
    return _safe(ir.reflog, path, n)


@mcp.tool()
def git_show(path: str, rev: str = "HEAD") -> str | dict:
    """One commit: message, author, files changed."""
    return _safe(ir.show_commit, path, rev)


@mcp.tool()
def rate_command(command: str) -> dict:
    """Risk tier of a git command you plan to propose: safe, careful or destructive, with why."""
    return classify(command)


@mcp.tool()
def list_operations() -> dict:
    """The write operations you may propose, with their parameters' meaning and base tier."""
    return {name: {"tier": op.tier if isinstance(op.tier, str) else "depends on the repo state",
                   "explain": op.explain} for name, op in ex.OPS.items()}


@mcp.tool()
def prepare_step(path: str, op: str, params: dict, task_id: str = "-") -> dict:
    """Validate one write step and get its approval token. Show the user the command, tier,
    explanation and (if dangerous) would_lose + confirm_phrase. Nothing runs here."""
    return ex.prepare(path, op, params, task_id)


@mcp.tool()
def execute_step(approval_token: str, path: str, op: str, params: dict,
                 confirmation: str | None = None, task_id: str = "-") -> dict:
    """Run ONE step the user approved. The token only works for the exact same op, params
    and repo state, once. Returns the new state in plain words and how to undo."""
    return ex.execute(approval_token, path, op, params, confirmation, task_id)


if __name__ == "__main__":
    mcp.run()
