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


if __name__ == "__main__":
    unittest.main()
