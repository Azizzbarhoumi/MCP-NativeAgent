"""Local git MCP server: read tools (no approval) + typed write steps behind approval.

  python -m mcp_servers.git_local.server          (stdio; started by the agent, not by hand)

Allowed folders: env GIT_TOOL_ALLOWED_ROOTS (separated by ; on Windows, : elsewhere).
Default: only eval/git_scenarios/build.
Writes: prepare_step gives the plan line (tier, explanation, what a dangerous step would lose)
and a step id; execute_step runs it only with the user's approval code (plus the confirmation
phrase for dangerous steps). There is no tool that runs raw commands.

Approval: the model never sees the approval token. prepare_step returns a step_id; the
approval code goes to the human outside the model's context (this process's stderr and
logs/approvals.log); execute_step needs that code. In-process agent code can still use
execute.prepare/execute directly and hold the token itself.
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
    """Validate one write step. Returns a step_id, the command, tier, explanation and (if
    dangerous) what it would lose and the confirmation phrase the USER must type. Nothing runs
    here. The user receives an approval code outside this conversation."""
    return ex.prepare_for_model(path, op, params, task_id)


@mcp.tool()
def execute_step(step_id: str, approval_code: str, confirmation: str | None = None, task_id: str = "-") -> dict:
    """Run ONE prepared step, only with the approval code the user typed (and, for dangerous
    steps, the confirmation phrase the user typed). Works once, for the exact step and repo
    state it was prepared for. Returns the new state in plain words and how to undo."""
    return ex.execute_with_code(step_id, approval_code, confirmation, task_id)


if __name__ == "__main__":
    mcp.run()
