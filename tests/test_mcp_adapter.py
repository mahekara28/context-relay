import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from context_relay import RelayStore
from context_relay.mcp_server import build_mcp_server


@unittest.skipUnless(importlib.util.find_spec("mcp"), "optional MCP SDK not installed")
class McpAdapterTests(unittest.TestCase):
    def test_sdk_client_discovers_health_tool(self):
        from mcp import Client

        async def run():
            store = RelayStore()
            try:
                async with Client(build_mcp_server(store, subject="test")) as client:
                    names = [tool.name for tool in (await client.list_tools()).tools]
                    self.assertIn("relay_health", names)
                    result = await client.call_tool("relay_health", {})
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.structured_content["task_schema"], "task-context/v1alpha1")
            finally:
                store.close()

        asyncio.run(run())

    def test_sdk_client_reads_task_tool_and_resource(self):
        from mcp import Client
        from context_relay.organization import Principal
        from context_relay.task_service import TaskService

        async def run():
            store = RelayStore()
            try:
                principal = Principal("local", "test")
                task = TaskService(store, authorize=lambda identity, action: True).create(
                    principal, goal="Finish report", completion_criteria=["Oracle passes"],
                    workspace={"repository": "example", "root": str(Path.cwd().resolve())})
                async with Client(build_mcp_server(store, subject="test")) as client:
                    shown = await client.call_tool("relay_task_show", {"task_id": task["id"]})
                    self.assertFalse(shown.is_error)
                    self.assertEqual(shown.structured_content["id"], task["id"])
                    packet = await client.call_tool("relay_task_packet", {"task_id": task["id"]})
                    self.assertEqual(packet.structured_content["revision"], 0)
                    resource = await client.read_resource(f"relay://task/{task['id']}")
                    self.assertIn(task["id"], resource.contents[0].text)
            finally:
                store.close()

        asyncio.run(run())

    def test_mcp_report_write_is_revisioned_and_cannot_edit_goal(self):
        from mcp import Client
        from context_relay.organization import Principal
        from context_relay.task_service import TaskService

        async def run():
            store = RelayStore()
            try:
                task = TaskService(store, authorize=lambda identity, action: True).create(
                    Principal("local", "test"), goal="Original", completion_criteria=["Done"],
                    workspace={"repository": "example", "root": str(Path.cwd().resolve())})
                async with Client(build_mcp_server(store, subject="test")) as client:
                    names = [tool.name for tool in (await client.list_tools()).tools]
                    self.assertNotIn("relay_task_create", names)
                    result = await client.call_tool("relay_task_report_open_work", {
                        "task_id": task["id"], "expected_revision": 0,
                        "request_id": "report-1", "open_work": ["Still needs review"]})
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.structured_content["goal"], "Original")
                    stale = await client.call_tool("relay_task_report_open_work", {
                        "task_id": task["id"], "expected_revision": 0,
                        "request_id": "report-2", "open_work": ["Stale"]})
                    self.assertTrue(stale.is_error)
            finally:
                store.close()

        asyncio.run(run())

    def test_mcp_handoff_acceptance_and_reported_claim(self):
        from mcp import Client
        from context_relay.handoffs import HandoffService
        from context_relay.organization import Principal
        from context_relay.task_service import TaskService

        async def run():
            store = RelayStore()
            try:
                sender = Principal("local", "sender")
                tasks = TaskService(store, authorize=lambda identity, action: True)
                task = tasks.create(sender, goal="Finish", completion_criteria=["Oracle passes"],
                                    workspace={"repository": "example", "root": str(Path.cwd().resolve())})
                offer = HandoffService(tasks).offer(sender, task["id"], recipient="receiver")
                async with Client(build_mcp_server(store, subject="receiver")) as client:
                    accepted = await client.call_tool("relay_handoff_accept", {"handoff_id": offer["id"]})
                    self.assertEqual(accepted.structured_content["status"], "accepted")
                    report = await client.call_tool("relay_task_report_claim", {
                        "task_id": task["id"], "expected_revision": 0, "request_id": "claim-1",
                        "claim": "I changed the report"})
                    self.assertFalse(report.is_error)
                    self.assertEqual(report.structured_content["evidence"][0]["source"],
                                     {"kind": "agent_report", "ref": "receiver"})
            finally:
                store.close()

        asyncio.run(run())

    def test_real_stdio_client_handles_bounded_packet_and_errors(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        from context_relay.organization import Principal
        from context_relay.task_service import TaskService

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "tasks.db"
            store = RelayStore(database)
            try:
                task = TaskService(store, authorize=lambda identity, action: True).create(
                    Principal("local", "test"), goal="Finish " + "x" * 600,
                    completion_criteria=["Oracle passes"],
                    workspace={"repository": "example", "root": temporary})
            finally:
                store.close()

            async def run():
                source = str(Path(__file__).resolve().parents[1] / "src")
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "context_relay", "mcp", "--db", str(database), "--subject", "test"],
                    env={"PYTHONPATH": source},
                )
                async with Client(parameters) as client:
                    shown = await client.call_tool("relay_task_show", {"task_id": task["id"]})
                    self.assertEqual(shown.structured_content["id"], task["id"])
                    too_small = await client.call_tool("relay_task_packet", {
                        "task_id": task["id"], "max_chars": 512})
                    self.assertTrue(too_small.is_error)
                    self.assertIn("budget", too_small.content[0].text.lower())
                    missing = await client.call_tool("relay_task_show", {"task_id": "missing"})
                    self.assertTrue(missing.is_error)

            asyncio.run(asyncio.wait_for(run(), timeout=15))


if __name__ == "__main__":
    unittest.main()
