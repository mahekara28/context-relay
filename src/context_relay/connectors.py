"""Project-scoped harness connectors; no user or global settings are changed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .connector_config import file_state, replace_if_unchanged
from .models import nonempty_string


SERVER_NAME = "context_relay"
_CODEX_BEGIN = "# BEGIN context-relay managed MCP server"
_CODEX_END = "# END context-relay managed MCP server"


def _root(project_root: str | Path) -> Path:
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project_root must be a directory.")
    return root


def _claude_entry(root: Path, python_executable: str | Path, subject: str) -> dict:
    nonempty_string(subject, "subject", limit=200)
    python = Path(python_executable).resolve(strict=True)
    if not python.is_file():
        raise ValueError("Python executable is not a file.")
    return {"type": "stdio", "command": str(python),
            "args": ["-m", "context_relay", "mcp", "--db", str(root / ".relay/tasks.db"),
                     "--subject", subject],
            "env": {"PYTHONPATH": str(root / "src")}}


def _read_claude(root: Path) -> tuple[Path, str | None, dict]:
    path = root / ".mcp.json"
    digest, data = file_state(path)
    parsed = {} if data is None else json.loads(data)
    if type(parsed) is not dict or type(parsed.get("mcpServers", {})) is not dict:
        raise ValueError(".mcp.json must be a JSON object with an mcpServers object.")
    return path, digest, parsed


def configure_claude(project_root: str | Path, *, python_executable: str | Path | None = None,
                     subject: str = "claude") -> dict:
    root = _root(project_root)
    path, digest, parsed = _read_claude(root)
    entry = _claude_entry(root, python_executable or sys.executable, subject)
    servers = parsed.setdefault("mcpServers", {})
    if SERVER_NAME in servers and servers[SERVER_NAME] != entry:
        raise ValueError("Existing context_relay entry differs; inspect it before changing it.")
    servers[SERVER_NAME] = entry
    content = (json.dumps(parsed, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    replace_if_unchanged(path, expected_digest=digest, content=content)
    return diagnose_claude(root)


def remove_claude(project_root: str | Path) -> dict:
    root = _root(project_root)
    path, digest, parsed = _read_claude(root)
    servers = parsed.get("mcpServers", {})
    entry = servers.get(SERVER_NAME)
    if entry is None:
        return {"configured": False, "path": str(path)}
    if type(entry) is not dict or entry.get("args", [])[:3] != ["-m", "context_relay", "mcp"]:
        raise ValueError("Existing context_relay entry is not managed by this connector.")
    del servers[SERVER_NAME]
    if not servers:
        parsed.pop("mcpServers", None)
    content = None if not parsed else (json.dumps(parsed, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    replace_if_unchanged(path, expected_digest=digest, content=content)
    return {"configured": False, "path": str(path)}


def diagnose_claude(project_root: str | Path) -> dict:
    root = _root(project_root)
    path, _, parsed = _read_claude(root)
    entry = parsed.get("mcpServers", {}).get(SERVER_NAME)
    command = entry.get("command") if type(entry) is dict else None
    return {"configured": type(entry) is dict, "path": str(path),
            "executable_exists": bool(command and Path(command).is_file()),
            "project_scoped": True}


def _codex_block(root: Path, python_executable: str | Path, subject: str) -> str:
    entry = _claude_entry(root, python_executable, subject)
    quoted = json.dumps
    return (f"{_CODEX_BEGIN}\n[mcp_servers.{SERVER_NAME}]\n"
            f"command = {quoted(entry['command'])}\n"
            f"args = {quoted(entry['args'])}\n"
            f"cwd = {quoted(str(root))}\n"
            f"env = {{ PYTHONPATH = {quoted(str(root / 'src'))} }}\n"
            f"{_CODEX_END}\n")


def _read_codex(root: Path) -> tuple[Path, str | None, str]:
    path = root / ".codex/config.toml"
    digest, data = file_state(path)
    return path, digest, "" if data is None else data.decode("utf-8")


def configure_codex(project_root: str | Path, *, python_executable: str | Path | None = None,
                    subject: str = "codex") -> dict:
    root = _root(project_root)
    path, digest, original = _read_codex(root)
    block = _codex_block(root, python_executable or sys.executable, subject)
    if _CODEX_BEGIN in original:
        if not original.endswith(block):
            raise ValueError("Existing managed Codex entry differs; inspect it before changing it.")
        return diagnose_codex(root)
    if f"[mcp_servers.{SERVER_NAME}]" in original:
        raise ValueError("Existing context_relay server entry is not managed by this connector.")
    updated = original + ("\n" if original else "") + block
    replace_if_unchanged(path, expected_digest=digest, content=updated.encode("utf-8"))
    return diagnose_codex(root)


def remove_codex(project_root: str | Path) -> dict:
    root = _root(project_root)
    path, digest, original = _read_codex(root)
    marker = ("\n" if not original.startswith(_CODEX_BEGIN) else "") + _CODEX_BEGIN
    start = original.find(marker)
    if start < 0:
        return {"configured": False, "path": str(path)}
    if original.count(_CODEX_BEGIN) != 1 or not original.endswith(_CODEX_END + "\n"):
        raise ValueError("Managed Codex block has been edited; inspect it before removing it.")
    restored = original[:start]
    replace_if_unchanged(path, expected_digest=digest,
                         content=restored.encode("utf-8") if restored else None)
    return {"configured": False, "path": str(path)}


def diagnose_codex(project_root: str | Path) -> dict:
    root = _root(project_root)
    path, _, content = _read_codex(root)
    configured = _CODEX_BEGIN in content and _CODEX_END in content
    command = None
    if configured:
        for line in content.splitlines():
            if line.startswith("command = "):
                try:
                    command = json.loads(line.partition("=")[2].strip())
                except json.JSONDecodeError:
                    pass
    return {"configured": configured, "path": str(path),
            "executable_exists": bool(command and Path(command).is_file()),
            "project_scoped": True}
