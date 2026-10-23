"""Native auto-connect contracts with local stand-ins; no SDK/network calls."""

import asyncio
import functools
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from context_relay import (
    AsyncNativeConnection, MemoryPolicy, NativeConnection, NativePrepared,
    NativeResult, RelayConfig, auto_connect,
)


class Client:
    def __init__(self, call=None):
        self.calls = []
        self.response = object()
        self.responses = SimpleNamespace(create=call or self.create)
        self.messages = SimpleNamespace(create=call or self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=call or self.beta_create))

    def create(self, **request):
        self.calls.append(("stable", request))
        return self.response

    def beta_create(self, **request):
        self.calls.append(("beta", request))
        return self.response


class NativeAutoConnectTests(unittest.TestCase):
    def test_native_responses_uses_complete_input_and_no_automatic_memory(self):
        client = Client()
        request = {"model": "host-model", "input": [{"role": "user", "content": "Export as CSV."}]}
        with auto_connect(client, provider="openai", api="responses", native=True) as agent:
            self.assertIsInstance(agent, NativeConnection)
            self.assertIsNone(agent.memory)
            result = agent.invoke_result(**request)
            self.assertIsInstance(result, NativeResult)
            self.assertIsInstance(result.prepared, NativePrepared)
            self.assertIs(result.response, client.response)
            self.assertEqual(client.calls, [("stable", request)])
            self.assertFalse(result.prepared.report["memory_injected"])

    def test_native_anthropic_beta_selects_endpoint_and_retains_top_level_system(self):
        client = Client()
        request = {"model": "host-model", "max_tokens": 20, "system": "Keep this prefix.",
                   "messages": [{"role": "user", "content": "Continue."}]}
        with auto_connect(client, provider="anthropic", native=True, beta=True) as agent:
            self.assertIs(agent(**request), client.response)
        self.assertEqual(client.calls, [("beta", request)])

    def test_native_stable_anthropic_does_not_require_beta_endpoint(self):
        client = Client()
        del client.beta
        with auto_connect(client, provider="anthropic", native=True) as agent:
            agent(messages=[{"role": "user", "content": "Hello."}])
        self.assertEqual(client.calls[0][0], "stable")

    def test_enabled_or_configured_memory_is_never_silently_ignored(self):
        for options in ({"memory": True}, {"memory": MemoryPolicy()},
                        {"config": RelayConfig(memory=MemoryPolicy())},
                        {"config": RelayConfig(memory=MemoryPolicy()), "memory": None}):
            with self.subTest(options=options), self.assertRaises((TypeError, ValueError)):
                auto_connect(Client(), provider="anthropic", native=True, **options)

    def test_explicit_false_disables_configured_memory_for_native_mode(self):
        with auto_connect(Client(), provider="anthropic", native=True, memory=False,
                          config=RelayConfig(memory=MemoryPolicy())) as agent:
            self.assertIsNone(agent.memory)
            self.assertIsNone(agent.config.memory)

    def test_native_none_is_accepted_as_no_configured_memory(self):
        with auto_connect(Client(), provider="anthropic", native=True, memory=None) as agent:
            self.assertIsNone(agent.memory)

    def test_invalid_mode_combinations_fail_before_creating_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "unused" / "relay.db"
            cases = [
                ("openai", {"native": True}),
                ("openai", {"native": True, "api": "chat"}),
                ("ollama", {"native": True}),
                ("plain", {"native": True}),
                ("anthropic", {"beta": True}),
                ("openai", {"native": True, "api": "responses", "beta": True}),
                ("anthropic", {"native": 1}),
                ("anthropic", {"beta": "yes"}),
            ]
            for provider, options in cases:
                with self.subTest(provider=provider, options=options), self.assertRaises((TypeError, ValueError)):
                    auto_connect(Client(), provider=provider, config=RelayConfig(db_path=str(database)), **options)
                self.assertFalse(database.parent.exists())

    def test_native_connection_and_decorated_bound_methods_cannot_be_wrapped_twice(self):
        with auto_connect(Client(), provider="anthropic", native=True) as agent:
            for original in (agent, agent.__call__, agent.invoke, agent.invoke_result):
                @functools.wraps(original)
                def decorated(**request):
                    return original(**request)

                for target in (original, decorated):
                    with self.subTest(target=target), self.assertRaisesRegex(ValueError, "already connected"):
                        auto_connect(target, provider="plain")

    def test_decorated_async_native_method_stays_async(self):
        marker, calls = object(), []

        async def create(**request):
            calls.append(request)
            return marker

        @functools.wraps(create)
        def decorated(**request):
            return create(**request)

        with auto_connect(Client(decorated), provider="anthropic", native=True) as agent:
            self.assertIsInstance(agent, AsyncNativeConnection)
            self.assertIs(asyncio.run(agent(messages=[{"role": "user", "content": "Hello."}])), marker)
        self.assertEqual(len(calls), 1)

    def test_missing_beta_endpoint_fails_with_specific_error(self):
        client = Client()
        del client.beta
        with self.assertRaisesRegex(TypeError, "beta.messages.create"):
            auto_connect(client, provider="anthropic", native=True, beta=True)


if __name__ == "__main__":
    unittest.main()
