import asyncio
import importlib.util
import unittest

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
                    workspace={"repository": "example", "root": "/work"})
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


if __name__ == "__main__":
    unittest.main()
