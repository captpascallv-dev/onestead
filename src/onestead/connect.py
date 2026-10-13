"""本机接入说明。网页「接一个新的伙伴」和 `onestead init` 都用这一份。"""

from __future__ import annotations

from .i18n import text as tr

import json
import sys
import sysconfig

from . import config
from pathlib import Path


def tool_path(name: str) -> Path:
    """当前 Python 环境 scripts 目录里的命令。Windows 上带 .exe。"""
    suffix = ".exe" if sys.platform == "win32" else ""
    return Path(sysconfig.get_path("scripts")) / f"{name}{suffix}"


def toml_basic_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def connect_prompt(owner_name: str) -> str:
    """四段都能直接复制。身份用环境变量 ONESTEAD_AGENT，不在这里填死。"""
    mcp = tool_path("onestead-mcp")
    cli = tool_path("onestead")
    blob = json.dumps(
        {"mcpServers": {"onestead": {"command": str(mcp), "env": {"ONESTEAD_AGENT": tr("agent.254")}}}},
        ensure_ascii=False, indent=2)
    toml = "\n".join([
        "[mcp_servers.onestead]",
        f"command = {toml_basic_string(str(mcp))}",
        tr("agent.245"),
    ])
    claude_cmd = tr("agent.243", v0=mcp)
    intro = (
        tr("agent.244", v0=config.display_name(), v1=mcp, v2=owner_name, v3=cli)
    )
    sections = "\n".join([
        "## Claude Code",
        "",
        tr("agent.246"),
        "",
        tr("agent.247", v0=mcp),
        "",
        "`.mcp.json`：",
        "",
        "```json",
        blob,
        "```",
        "",
        tr("agent.248"),
        "",
        "```",
        claude_cmd,
        "```",
        "",
        "## Codex",
        "",
        tr("agent.249"),
        "",
        tr("agent.247", v0=mcp),
        "",
        "```toml",
        toml,
        "```",
        "",
        "## Cursor",
        "",
        tr("agent.250"),
        "",
        tr("agent.247", v0=mcp),
        "",
        "```json",
        blob,
        "```",
        "",
        "## ChatGPT",
        "",
        tr("agent.251", v0=mcp),
        tr("agent.252"),
        tr("agent.253"),
    ])
    return intro + "\n\n" + sections + "\n"
