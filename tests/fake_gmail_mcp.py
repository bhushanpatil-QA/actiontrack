"""A stand-in Gmail MCP server for tests. Logs every tool call to $FAKE_MCP_LOG (one JSON per line).

It deliberately exposes a send tool too, so tests can prove actiontrack never calls it.
"""
import json
import os

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server

server = Server("fake-gmail")
LOG = os.environ.get("FAKE_MCP_LOG", "fake_mcp_calls.jsonl")


def _log(tool: str, **kwargs):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"tool": tool, **kwargs}) + "\n")


@server.tool()
def create_draft(to: str, subject: str, body: str) -> str:
    """Create a Gmail draft."""
    _log("create_draft", to=to, subject=subject, body=body)
    return json.dumps({"id": "draft-001", "message": "draft created"})


@server.tool()
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email immediately (must never be called by actiontrack)."""
    _log("send_email", to=to, subject=subject, body=body)
    return "sent"


if __name__ == "__main__":
    server.run()
