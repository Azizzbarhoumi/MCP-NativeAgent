"""Connection to the GitHub MCP server (official github/github-mcp-server, run in Docker).

Security choices:
- The token lives ONLY in mcp_servers/config/github.env, which Docker reads with --env-file.
  Our Python process never loads it, so it can't leak into prompts, graph state or traces.
- GITHUB_READ_ONLY=1 makes the server refuse every write tool.
- GITHUB_TOOLSETS limits the server to the toolsets we use (fewer tools = fewer wrong choices).
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ENV_FILE = Path(__file__).resolve().parent.parent / "mcp_servers" / "config" / "github.env"

SERVER = StdioServerParameters(
    command="docker",
    args=[
        "run", "-i", "--rm",
        "--env-file", str(ENV_FILE),
        "-e", "GITHUB_READ_ONLY=1",
        "-e", "GITHUB_TOOLSETS=issues,repos,pull_requests,actions",
        "ghcr.io/github/github-mcp-server",
    ],
)


class ToolError(Exception):
    """The tool ran but reported a failure (bad repo, bad token, ...)."""


class GitHubTools:
    """Thin wrapper so the graph only sees `await tools.call(name, args) -> parsed JSON`.
    Tests replace it with a fake that has the same method."""

    def __init__(self, session: ClientSession):
        self.session = session

    async def list_names(self) -> list[str]:
        return [t.name for t in (await self.session.list_tools()).tools]

    async def describe(self, allowed: set[str]) -> str:
        """Tool catalog for the prompt: name, description and arguments, taken from the
        server itself, so the prompt never drifts from the real tool signatures."""
        lines = []
        for t in (await self.session.list_tools()).tools:
            if t.name not in allowed:
                continue
            props = (t.inputSchema or {}).get("properties", {})
            required = set((t.inputSchema or {}).get("required", []))
            args = ", ".join(
                f"{k}{'' if k in required else '?'}: {v.get('type', 'any')}"
                + (f" one of {v['enum']}" if "enum" in v else "")
                for k, v in props.items()
            )
            desc = (t.description or "").split("\n")[0][:200]
            lines.append(f"- {t.name}({args}): {desc}")
        return "\n".join(lines)

    async def call(self, name: str, args: dict):
        result = await self.session.call_tool(name, args)
        text = "".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        if result.isError:
            raise ToolError(text or "unknown error")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


@asynccontextmanager
async def github_tools():
    """Start the server, yield a ready GitHubTools, stop the server on exit."""
    if not ENV_FILE.exists():
        raise FileNotFoundError(
            f"{ENV_FILE} not found. Copy github.env.example to github.env and add your token."
        )
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield GitHubTools(session)
