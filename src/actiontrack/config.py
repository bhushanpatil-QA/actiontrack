"""Paths, 'today', people directory and Gmail MCP settings (SDD §5.2)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DEFAULT_DB = Path(".actiontrack") / "actiontrack.db"


class ConfigError(Exception):
    pass


@dataclass
class GmailMcpConfig:
    transport: str = "stdio"              # stdio | http
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    draft_tool: str = "create_draft"
    arg_to: str = "to"
    arg_subject: str = "subject"
    arg_body: str = "body"
    to_as_list: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.command if self.transport == "stdio" else self.url)


@dataclass
class Config:
    db: Path
    today: date
    people: dict[str, str | None]
    gmail: GmailMcpConfig
    sender_name: str = "Bhushan"


def resolve_today(cli_value: str | None) -> date:
    value = cli_value or os.environ.get("ACTIONTRACK_TODAY")
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ConfigError(f"--today must be YYYY-MM-DD, got {value!r}")


def load_people(path: Path) -> dict[str, str | None]:
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}")
    out: dict[str, str | None] = {}
    for p in data.get("person", []):
        if p.get("name"):
            out[" ".join(str(p["name"]).split())] = p.get("email") or None
    return out


def load_settings(path: Path) -> tuple[GmailMcpConfig, str]:
    if not path.exists():
        return GmailMcpConfig(), "Bhushan"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}")
    g = data.get("gmail_mcp", {})
    known = GmailMcpConfig.__dataclass_fields__.keys()
    unknown = set(g) - set(known)
    if unknown:
        raise ConfigError(f"unknown [gmail_mcp] keys: {', '.join(sorted(unknown))}")
    sender = data.get("followup", {}).get("sender_name", "Bhushan")
    return GmailMcpConfig(**g), sender


def build_config(db: str | None, today: str | None, people: str | None, settings: str | None) -> Config:
    db_path = Path(db or os.environ.get("ACTIONTRACK_DB") or DEFAULT_DB)
    gmail, sender = load_settings(Path(settings or "actiontrack.toml"))
    return Config(db_path, resolve_today(today), load_people(Path(people or "people.toml")), gmail, sender)
