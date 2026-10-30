"""Optional MCP SDK adapter over the same local task service used by the CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .handoffs import HandoffService
from .organization import Principal
from .store import RelayStore
from .task_packet import compile_task_packet
from .task_service import TaskService


def build_mcp_server(store: RelayStore, *, subject: str):
    """Build a local stdio server; importing the core never requires MCP."""
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError as error:
        raise RuntimeError("MCP support requires Python 3.11+ and context-relay-local[mcp].") from error
    principal = Principal("local", subject)
    tasks = TaskService(store, authorize=lambda host_principal, action: host_principal == principal)
    reports = TaskService(store, authorize=lambda host_principal, action: host_principal == principal,
                          source_class="agent_report")
    handoffs = HandoffService(tasks)
    server = MCPServer("Context Relay")

    @server.tool(structured_output=True)
    def relay_health() -> dict[str, str]:
        """Report the local task protocol version without exposing stored content."""
        return {"service": "context-relay", "task_schema": "task-context/v1alpha1"}

    @server.tool(structured_output=True)
    def relay_task_show(task_id: str) -> dict[str, Any]:
        """Read the current validated task snapshot by ID."""
        return tasks.show(principal, task_id)

    @server.tool(structured_output=True)
    def relay_task_packet(task_id: str, max_chars: int = 16000) -> dict[str, Any]:
        """Read a bounded continuation packet with current instructions and source refs."""
        return compile_task_packet(tasks.show(principal, task_id), max_chars=max_chars)

    @server.tool(structured_output=True)
    def relay_task_diff(task_id: str, from_revision: int) -> dict[str, Any]:
        """Read changes since a task revision, including instruction provenance."""
        return tasks.diff(principal, task_id, from_revision=from_revision)

    @server.resource("relay://task/{task_id}")
    def task_resource(task_id: str) -> str:
        """Current task snapshot as a portable JSON resource."""
        return json.dumps(tasks.show(principal, task_id), ensure_ascii=False)

    @server.tool(structured_output=True)
    def relay_task_report_open_work(task_id: str, expected_revision: int,
                                    request_id: str, open_work: list[str]) -> dict[str, Any]:
        """Report remaining work at a known revision; this cannot change the goal or instructions."""
        try:
            return reports.update(principal, task_id, expected_revision=expected_revision,
                                  request_id=request_id, changes={"open_work": open_work})
        except (ValueError, LookupError, PermissionError) as error:
            raise ToolError(str(error)) from None

    @server.tool(structured_output=True)
    def relay_task_report_claim(task_id: str, expected_revision: int,
                                request_id: str, claim: str) -> dict[str, Any]:
        """Record a model-reported claim, clearly labeled as unverified."""
        try:
            return reports.attach_evidence(principal, task_id, expected_revision=expected_revision,
                                           request_id=request_id, claim=claim,
                                           source={"kind": "agent_report", "ref": principal.subject})
        except (ValueError, LookupError, PermissionError) as error:
            raise ToolError(str(error)) from None

    @server.tool(structured_output=True)
    def relay_handoff_inspect(handoff_id: str) -> dict[str, Any]:
        """Read an immutable handoff offer and its acceptance status."""
        return handoffs.inspect(principal, handoff_id)

    @server.tool(structured_output=True)
    def relay_handoff_accept(handoff_id: str) -> dict[str, Any]:
        """Accept a fresh handoff addressed to this local harness identity."""
        try:
            return handoffs.accept(principal, handoff_id)
        except (ValueError, LookupError, PermissionError) as error:
            raise ToolError(str(error)) from None

    return server


def run_mcp_server(database: str | Path, *, subject: str) -> None:
    store = RelayStore(database)
    try:
        build_mcp_server(store, subject=subject).run(transport="stdio")
    finally:
        store.close()
