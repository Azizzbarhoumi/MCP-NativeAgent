"""Talk to the GitHub MCP server with NO model involved.

Usage:
  python scripts_mcp_check.py owner/repo                          list tools, then list_issues
  python scripts_mcp_check.py owner/repo list_commits             call any tool
  python scripts_mcp_check.py owner/repo pull_request_read '{"method":"get","pullNumber":1}'
"""

import asyncio
import json
import sys

from orchestrator.graph import ALLOWED_TOOLS
from orchestrator.mcp_client import github_tools


async def main(repo: str, tool: str, extra: dict) -> None:
    owner, name = repo.split("/")
    async with github_tools() as tools:
        print("Allowed tools and their arguments:\n" + await tools.describe(ALLOWED_TOOLS) + "\n")
        result = await tools.call(tool, {"owner": owner, "repo": name, **extra})
        print(json.dumps(result, indent=2, default=str)[:3000])


repo = sys.argv[1]
tool = sys.argv[2] if len(sys.argv) > 2 else "list_issues"
extra = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
asyncio.run(main(repo, tool, extra))
