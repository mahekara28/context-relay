"""Optional MCP SDK adapter over the same local task service used by the CLI."""

from __future__ import annotations

from pathlib import Path

from .organization import Principal
from .store import RelayStore
from .task_service import TaskService


def build_mcp_server(store: RelayStore, *, subject: str):
    """Build a local stdio server; importing the core never requires MCP."""
    try:
        from mcp.server import MCPServer
    except ImportError as error:
        raise RuntimeError("MCP support requires Python 3.11+ and context-relay-local[mcp].") from error
    principal = Principal("local", subject)
    tasks = TaskService(store, authorize=lambda host_principal, action: host_principal == principal)
    server = MCPServer("Context Relay")

    @server.tool(structured_output=True)
    def relay_health() -> dict[str, str]:
        """Report the local task protocol version without exposing stored content."""
        return {"service": "context-relay", "task_schema": "task-context/v1alpha1"}

    return server


def run_mcp_server(database: str | Path, *, subject: str) -> None:
    store = RelayStore(database)
    try:
        build_mcp_server(store, subject=subject).run(transport="stdio")
    finally:
        store.close()
