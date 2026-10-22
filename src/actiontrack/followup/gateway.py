"""Draft gateways (SDD §8.6). The only Gmail action this tool can perform is *create a draft*."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from typing import Protocol

from actiontrack.config import ConfigError, GmailMcpConfig


class GatewayError(Exception):
    pass


class DraftGateway(Protocol):
    def create_draft(self, to: str, subject: str, body: str) -> str: ...


def check_tool_name(name: str, allowed: str) -> None:
    """Never-send guard: refuse anything that is not the configured draft tool, and any 'send' tool."""
    if "send" in name.lower():
        raise ConfigError(f"refusing to use MCP tool {name!r}: actiontrack only creates drafts and never sends")
    if name != allowed:
        raise ConfigError(f"refusing to call MCP tool {name!r}: only {allowed!r} is allowed")


class DryRunGateway:
    def __init__(self, out):
        self.out = out

    def create_draft(self, to: str, subject: str, body: str) -> str:
        self.out(f"To: {to}\nSubject: {subject}\n\n{body}\n")
        return "dry-run"


class EmitJsonGateway:
    def __init__(self, out, item_ids: list[int] | None = None):
        self.out = out
        self.item_ids = item_ids or []

    def create_draft(self, to: str, subject: str, body: str) -> str:
        self.out(json.dumps({"to": to, "subject": subject, "body": body, "item_ids": self.item_ids}, ensure_ascii=False))
        return "emitted"


class McpGmailGateway:
    """Talks to a Gmail MCP server (stdio or streamable HTTP) and calls only its draft tool."""

    def __init__(self, cfg: GmailMcpConfig):
        if not cfg.configured:
            raise ConfigError("Gmail MCP is not configured: add a [gmail_mcp] section to actiontrack.toml "
                              "(or use --dry-run / --emit-json)")
        check_tool_name(cfg.draft_tool, cfg.draft_tool)
        self.cfg = cfg

    def create_draft(self, to: str, subject: str, body: str) -> str:
        try:
            return asyncio.run(self._create(to, subject, body))
        except (ConfigError, GatewayError):
            raise
        except Exception as e:  # transport / auth / server errors
            raise GatewayError(f"Gmail MCP call failed: {type(e).__name__}: {e}") from e

    async def _open(self, stack: AsyncExitStack):
        from mcp import ClientSession  # imported lazily: only followup needs the SDK
        if self.cfg.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=self.cfg.command, args=self.cfg.args, env=self.cfg.env or None)
            streams = await stack.enter_async_context(stdio_client(params, errlog=_errlog(stack)))
        elif self.cfg.transport == "http":
            try:
                from mcp.client.streamable_http import streamable_http_client as http_client
            except ImportError:  # mcp 1.x
                from mcp.client.streamable_http import streamablehttp_client as http_client
            streams = await stack.enter_async_context(http_client(self.cfg.url))
        else:
            raise ConfigError(f"unknown transport {self.cfg.transport!r} (use 'stdio' or 'http')")
        session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
        await session.initialize()
        return session

    async def _create(self, to: str, subject: str, body: str) -> str:
        async with AsyncExitStack() as stack:
            session = await self._open(stack)
            tools = {t.name for t in (await session.list_tools()).tools}
            if self.cfg.draft_tool not in tools:
                raise GatewayError(f"MCP server has no tool {self.cfg.draft_tool!r}; available: {', '.join(sorted(tools))}")
            check_tool_name(self.cfg.draft_tool, self.cfg.draft_tool)
            args = {self.cfg.arg_to: [to] if self.cfg.to_as_list else to,
                    self.cfg.arg_subject: subject, self.cfg.arg_body: body}
            result = await session.call_tool(self.cfg.draft_tool, args)
            if getattr(result, "isError", False) or getattr(result, "is_error", False):
                raise GatewayError(f"draft tool returned an error: {_text(result)}")
            return _draft_id(result)


def _errlog(stack: AsyncExitStack):
    """Server stderr goes to our stderr when it is a real file, otherwise to the null device."""
    try:
        sys.stderr.fileno()
        return sys.stderr
    except (AttributeError, OSError, ValueError):
        return stack.enter_context(open(os.devnull, "w"))


def _text(result) -> str:
    return " ".join(getattr(c, "text", "") for c in getattr(result, "content", []) or []).strip()


def _draft_id(result) -> str:
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    candidates = [structured] if isinstance(structured, dict) else []
    text = _text(result)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            candidates.append(parsed)
    except (ValueError, TypeError):
        pass
    for c in candidates:
        for k in ("id", "draftId", "draft_id"):
            if c.get(k):
                return str(c[k])
        if isinstance(c.get("draft"), dict) and c["draft"].get("id"):
            return str(c["draft"]["id"])
    return text or "created"
