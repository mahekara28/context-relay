"""Easy connection contracts using local stand-ins, never real SDKs or APIs."""

import asyncio
import copy
import functools
import json
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from context_relay import (
    AsyncConnectedAgent, ConnectedAgent, ContextGuard, MemoryInterpreter,
    MemoryPolicy, OutputPolicy, OutputRejected, RelayConfig, TokenBudget,
    attach, auto_connect, connect,
)
from context_relay.budget import TokenBudgetExceeded


def history(text="Export as CSV."):
    return [{"role": "user", "content": text}]


def reply(provider, text="Ready."):
    if provider == "openai":
        return {"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": text}}]}
    if provider == "anthropic":
        return {"type": "message", "role": "assistant", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": text}]}
    return {"done": True, "done_reason": "stop", "message": {
        "role": "assistant", "content": text}}


class Recorder:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.closed = False

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response

    def close(self):
        self.closed = True


class OpenAIStandIn(Recorder):
    def __init__(self, response):
        super().__init__(response)
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))
        self.responses = types.SimpleNamespace(create=self.create)


class AnthropicStandIn(Recorder):
    def __init__(self, response):
        super().__init__(response)
        self.messages = types.SimpleNamespace(create=self.create)


class OllamaStandIn(Recorder):
    def chat(self, model="", messages=None, **kwargs):
        return self.create(model=model, messages=messages, **kwargs)


class AsyncRecorder:
    async def create(self, **kwargs):
        await asyncio.sleep(0)
        return super().create(**kwargs)


class AsyncOpenAIStandIn(AsyncRecorder, OpenAIStandIn):
    pass


class AsyncAnthropicStandIn(AsyncRecorder, AnthropicStandIn):
    pass


class AsyncOllamaStandIn(AsyncRecorder, OllamaStandIn):
    async def chat(self, model="", messages=None, **kwargs):
        return await self.create(model=model, messages=messages, **kwargs)


BASES = {
    "openai": (OpenAIStandIn, AsyncOpenAIStandIn, "OpenAI", "AsyncOpenAI"),
    "anthropic": (AnthropicStandIn, AsyncAnthropicStandIn, "Anthropic", "AsyncAnthropic"),
    "ollama": (OllamaStandIn, AsyncOllamaStandIn, "Client", "AsyncClient"),
}


@contextmanager
def loaded_sdk(provider, *, asynchronous=False, public=False):
    """Register class identities with the same modules an SDK exposes."""
    base, async_base, name, async_name = BASES[provider]
    module_name = provider if public else provider + "._client"
    module = types.ModuleType(module_name)
    cls = type(async_name if asynchronous else name,
               (async_base if asynchronous else base,), {"__module__": module_name})
    setattr(module, cls.__name__, cls)
    with patch.dict(sys.modules, {module_name: module}):
        yield cls


class AutoConnectTests(unittest.TestCase):
    def connected(self, client, **options):
        agent = auto_connect(client, **options)
        self.addCleanup(agent.close)
        return agent

    def test_plain_function_enables_memory_without_changing_response_identity(self):
        calls, marker = [], object()

        def generate(*, messages, model):
            calls.append((messages, model))
            return marker

        agent = self.connected(generate)
        original = history()
        expected = copy.deepcopy(original)
        result = agent.invoke(messages=original, model="host-model")
        self.assertIsInstance(agent, ConnectedAgent)
        self.assertIs(result.response, marker)
        self.assertEqual(agent.memory.entries()[0]["value"], "CSV")
        self.assertIn("user_memory", json.dumps(calls[0][0]))
        self.assertEqual(calls[0][1], "host-model")
        self.assertEqual(original, expected)

    def test_plain_bound_method_requires_explicit_choice_without_patching_owner(self):
        client = Recorder(object())
        original = client.create
        before = vars(client).copy()
        with self.assertRaises((TypeError, ValueError)):
            auto_connect(client.create)
        agent = self.connected(client.create, provider="plain")
        self.assertIs(agent(messages=history()), client.response)
        self.assertEqual(client.create, original)
        self.assertEqual(set(vars(client)), set(before))
        agent.close()
        self.assertFalse(client.closed)

    def test_loaded_public_and_defining_sdk_class_identities_select_provider(self):
        for provider in BASES:
            for public in (False, True):
                with self.subTest(provider=provider, public=public), loaded_sdk(provider, public=public) as cls:
                    # Subclasses retain identity through isinstance, even when
                    # the application owns the subclass's defining module.
                    client = type("ApplicationClient", (cls,), {})(reply(provider))
                    before = vars(client).copy()
                    with auto_connect(client) as agent:
                        self.assertIs(agent(messages=history(), model="host-model"), client.response)
                        self.assertEqual(agent.memory.entries()[0]["value"], "CSV")
                        expected = {"openai": "openai_chat", "anthropic": "plain", "ollama": "ollama"}
                        self.assertEqual(agent.config.protocol, expected[provider])
                    self.assertFalse(client.closed)
                    self.assertEqual(set(vars(client)), set(before))
                    for key, value in before.items():
                        self.assertIs(vars(client)[key], value)

    def test_explicit_provider_connects_custom_clients_without_loaded_sdk(self):
        for provider, (base, _, _, _) in BASES.items():
            client = base(reply(provider))
            with self.subTest(provider=provider), auto_connect(client, provider=provider) as agent:
                self.assertIs(agent(messages=history()), client.response)
                self.assertEqual(len(client.calls), 1)

    def test_unknown_client_shape_and_spoofed_module_are_rejected_before_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "not-created" / "relay.db"
            config = RelayConfig(db_path=str(db))
            with loaded_sdk("openai"):
                spoofed = type("OpenAI", (OpenAIStandIn,), {"__module__": "openai._client"})
                for target in (OpenAIStandIn(object()), spoofed(object()), object()):
                    with self.subTest(target=type(target)), self.assertRaises((TypeError, ValueError)):
                        auto_connect(target, config=config)
                    self.assertFalse(db.parent.exists())

    def test_arbitrary_callable_needs_explicit_plain_provider(self):
        class Callable:
            def __call__(self, *, messages):
                return messages

        target = Callable()
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "not-created" / "relay.db"
            with self.assertRaises((TypeError, ValueError)):
                auto_connect(target, config=RelayConfig(db_path=str(db)))
            self.assertFalse(db.parent.exists())
        with auto_connect(target, provider="plain") as agent:
            self.assertEqual(agent(messages=history())[-1], history()[0])

    def test_classes_modules_bad_providers_and_api_choices_fail_before_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "not-created" / "relay.db"
            config = RelayConfig(db_path=str(db))
            cases = [(Recorder, {}), (types.ModuleType("app"), {}),
                     (lambda **kw: None, {"provider": "unknown"}),
                     (lambda **kw: None, {"provider": 1}),
                     (lambda **kw: None, {"provider": "plain", "api": "chat"}),
                     (OllamaStandIn(None), {"provider": "ollama", "api": "responses"}),
                     (AnthropicStandIn(None), {"provider": "anthropic", "api": "chat"}),
                     (OpenAIStandIn(None), {"provider": "openai", "api": "unknown"})]
            for target, options in cases:
                with self.subTest(options=options), self.assertRaises((TypeError, ValueError)):
                    auto_connect(target, config=config, **options)
                self.assertFalse(db.parent.exists())

    def test_explicit_openai_responses_uses_input_and_preserves_response(self):
        response = {"status": "completed", "output": [{"type": "message", "role": "assistant",
                    "status": "completed", "content": [{"type": "output_text", "text": "Done.",
                    "annotations": []}]}]}
        client = OpenAIStandIn(response)
        with auto_connect(client, provider="openai", api="responses") as agent:
            self.assertIs(agent(messages=history(), model="host-model"), response)
        self.assertIn("input", client.calls[0])
        self.assertNotIn("messages", client.calls[0])
        self.assertEqual(client.calls[0]["input"][-1], history()[0])

    def test_configured_review_policy_is_used_by_memory_true(self):
        agent = self.connected(lambda **kw: None, config=RelayConfig(memory={"mode": "review"}))
        agent(messages=history())
        self.assertEqual(agent.memory.entries(), [])
        self.assertEqual(agent.memory.pending()[0]["value"], "CSV")

    def test_memory_false_overrides_config_and_does_not_write_recall(self):
        agent = self.connected(lambda **kw: None, memory=False,
                               config=RelayConfig(memory=MemoryPolicy()))
        result = agent.invoke(messages=history())
        self.assertIsNone(agent.memory)
        self.assertNotIn("user_memory", json.dumps(result.prepared.messages))
        self.assertEqual(agent.guard.store.events(agent.session_id), [])

    def test_explicit_memory_policy_overrides_config(self):
        agent = self.connected(lambda **kw: None, memory=MemoryPolicy(),
                               config=RelayConfig(memory={"mode": "review"}))
        agent(messages=history())
        self.assertEqual(agent.memory.entries()[0]["value"], "CSV")
        self.assertEqual(agent.memory.pending(), [])

    def test_invalid_memory_values_fail_before_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "not-created" / "relay.db"
            for memory in (None, 0, 1, "true", {}, []):
                with self.subTest(memory=memory), self.assertRaises((TypeError, ValueError)):
                    auto_connect(lambda **kw: None, memory=memory,
                                 config=RelayConfig(db_path=str(db)))
                self.assertFalse(db.parent.exists())

    def test_config_file_is_loaded_once_and_relative_database_keeps_config_location(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "relay.json"
            config.write_text(json.dumps({"db_path": "state/relay.db", "memory": {"mode": "review"}}),
                              encoding="utf-8")
            with patch.object(RelayConfig, "from_file", wraps=RelayConfig.from_file) as load:
                with auto_connect(OllamaStandIn(reply("ollama")), provider="ollama", config=config) as agent:
                    agent(messages=history())
                    self.assertTrue(agent.memory.pending())
                    self.assertEqual(agent.config.protocol, "ollama")
                load.assert_called_once()
            self.assertTrue((Path(temp) / "state" / "relay.db").exists())

    def test_one_client_can_back_independent_sessions_without_memory_leaks(self):
        client = OllamaStandIn(reply("ollama"))
        with auto_connect(client, provider="ollama") as first, auto_connect(client, provider="ollama") as second:
            self.assertNotEqual(first.session_id, second.session_id)
            first(messages=history())
            second(messages=history("Continue."))
            self.assertEqual(first.memory.entries()[0]["value"], "CSV")
            self.assertEqual(second.memory.entries(), [])
            self.assertNotIn('"value":"CSV"', json.dumps(client.calls[-1], separators=(",", ":")))
        self.assertFalse(client.closed)

    def test_closing_connection_keeps_external_guard_and_client_usable(self):
        client = OllamaStandIn(reply("ollama"))
        with ContextGuard() as guard:
            with auto_connect(client, provider="ollama", guard=guard) as agent:
                self.assertIs(agent.guard, guard)
            guard.remember("still_open", True)
            self.assertTrue(guard.turn().context["facts"]["still_open"])
        self.assertIs(client.chat(messages=history()), client.response)
        self.assertFalse(client.closed)

    def test_connected_agent_and_its_bound_methods_cannot_be_wrapped_again(self):
        with tempfile.TemporaryDirectory() as temp, connect(lambda **kw: None) as existing:
            db = Path(temp) / "not-created" / "relay.db"
            for target in (existing, existing.__call__, existing.invoke):
                with self.subTest(target=target), self.assertRaises((TypeError, ValueError)):
                    auto_connect(target, provider="plain", config=RelayConfig(db_path=str(db)))
                self.assertFalse(db.parent.exists())

    def test_attached_method_or_client_is_rejected_before_second_store(self):
        client = OllamaStandIn(reply("ollama"))
        with tempfile.TemporaryDirectory() as temp, attach(client, method="chat"):
            db = Path(temp) / "not-created" / "relay.db"
            for target, provider in ((client.chat, "plain"), (client, "ollama")):
                with self.subTest(provider=provider), self.assertRaises((TypeError, ValueError)):
                    auto_connect(target, provider=provider, config=RelayConfig(db_path=str(db)))
                self.assertFalse(db.parent.exists())

    def test_decorated_connected_callable_cannot_hide_a_second_connection(self):
        with tempfile.TemporaryDirectory() as temp, connect(lambda **kw: None) as existing:
            db = Path(temp) / "not-created" / "relay.db"
            for target in (existing, existing.__call__, existing.invoke):
                @functools.wraps(target)
                def decorated(**kwargs):
                    return target(**kwargs)

                with self.subTest(target=target), self.assertRaises((TypeError, ValueError)):
                    with auto_connect(decorated, provider="plain", config=RelayConfig(db_path=str(db))):
                        pass
                self.assertFalse(db.parent.exists())

    def test_direct_and_decorated_async_generators_fail_before_storage(self):
        async def generate(*, messages):
            yield messages

        @functools.wraps(generate)
        def decorated(**kwargs):
            return generate(**kwargs)

        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "not-created" / "relay.db"
            cases = [(generate, {}), (decorated, {}),
                     (types.SimpleNamespace(chat=decorated), {"provider": "ollama"})]
            for target, options in cases:
                with self.subTest(target=target), self.assertRaises((TypeError, ValueError)):
                    with auto_connect(target, config=RelayConfig(db_path=str(db)), **options):
                        pass
                self.assertFalse(db.parent.exists())

    def test_ollama_native_tool_links_are_preserved_without_flattening(self):
        assistant = {"role": "assistant", "tool_calls": [
            {"function": {"name": "lookup", "arguments": {"id": 7}}}]}
        native = history("Find record 7.") + [assistant,
                  {"role": "tool", "tool_name": "lookup", "content": "Found"}]
        original = copy.deepcopy(native)
        client = OllamaStandIn(reply("ollama"))
        with auto_connect(client, provider="ollama") as agent:
            self.assertIs(agent(messages=native), client.response)
        self.assertEqual(client.calls[0]["messages"][-2], assistant)
        self.assertEqual(client.calls[0]["messages"][-1], native[-1])
        self.assertEqual(native, original)

    def test_ollama_message_objects_and_tuples_are_rejected_before_provider(self):
        sdk_types = types.ModuleType("ollama._types")

        class Message:
            __module__ = "ollama._types"

            def __init__(self, value):
                self.value = value

            def model_dump(self, **kwargs):
                raise AssertionError("Unknown object must not be implicitly flattened")

        sdk_types.Message = Message
        client = OllamaStandIn(reply("ollama"))
        with patch.dict(sys.modules, {"ollama._types": sdk_types}):
            with auto_connect(client, provider="ollama") as agent:
                for messages in ([Message(history()[0])], tuple(history())):
                    with self.subTest(messages=messages), self.assertRaises((TypeError, ValueError)):
                        agent(messages=messages)
        self.assertEqual(client.calls, [])

    def test_ollama_managed_chat_decodes_sdk_shaped_message(self):
        sdk_types = types.ModuleType("ollama._types")

        class Message:
            __module__ = "ollama._types"

            def model_dump(self, **kwargs):
                return {"role": "assistant", "content": "Exact text: 雪\n"}

        sdk_types.Message = Message
        response = types.SimpleNamespace(done=True, done_reason="stop", message=Message())
        with patch.dict(sys.modules, {"ollama._types": sdk_types}):
            with auto_connect(OllamaStandIn(response), provider="ollama") as agent:
                chat = agent.conversation()
                self.assertIs(chat.send("Hello", request_id="first"), response)
                self.assertEqual(chat.history[-1], {"role": "assistant", "content": "Exact text: 雪\n"})

    def test_provider_owned_streams_are_passed_through_without_consumption(self):
        class Stream:
            def __iter__(self):
                raise AssertionError("Relay consumed the stream")

            def close(self):
                raise AssertionError("Relay closed the stream")

        for provider, (base, _, _, _) in BASES.items():
            stream = Stream()
            with self.subTest(provider=provider), auto_connect(base(stream), provider=provider) as agent:
                self.assertIs(agent(messages=history("Hello"), stream=True), stream)

    def test_managed_chat_and_memory_resume_for_all_text_connectors(self):
        for provider, (base, _, _, _) in BASES.items():
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                config = RelayConfig(db_path=str(Path(temp) / "relay.db"))
                client = base(reply(provider))
                with auto_connect(client, provider=provider, config=config) as agent:
                    session_id = agent.session_id
                    chat = agent.conversation(system="Help with the project.")
                    self.assertIs(chat.send("Export as CSV.", request_id="first"), client.response)
                with auto_connect(client, provider=provider, config=config, session_id=session_id) as resumed:
                    chat = resumed.conversation()
                    self.assertIs(chat.send("Export as JSON.", request_id="second"), client.response)
                    self.assertEqual(len(chat.history), 5)
                    self.assertEqual(chat.history[2], {"role": "assistant", "content": "Ready."})
                    self.assertEqual(resumed.memory.entries()[0]["value"], "JSON")
                self.assertEqual(len(client.calls), 2)
                self.assertFalse(client.closed)

    def test_output_policy_uses_provider_decoder_and_rejects_bad_format(self):
        for provider, (base, _, _, _) in BASES.items():
            client = base(reply(provider, '{"id":1}'))
            with self.subTest(provider=provider), auto_connect(client, provider=provider,
                    output=OutputPolicy(format="json", required_keys=("id",))) as agent:
                result = agent.invoke(messages=history("Return the record."))
                self.assertIs(result.response, client.response)
                self.assertEqual(result.output_report.format, "json")
                client.response = reply(provider, "Not JSON")
                with self.assertRaises(OutputRejected):
                    agent(messages=history("Return the record."))
            self.assertEqual(len(client.calls), 2)

    def test_optional_interpreter_is_forwarded_and_suggestions_stay_pending(self):
        calls = []

        def interpret(request):
            calls.append(request)
            text = request["text"]
            return [{"key": "response_style", "value": "brief", "quote": text,
                     "start": 0, "end": len(text)}]

        agent = self.connected(lambda **kw: "Ready.", memory_interpreter=MemoryInterpreter(interpret))
        agent(messages=history("Make it brief."))
        self.assertEqual(len(calls), 1)
        self.assertEqual(agent.memory.entries(), [])
        self.assertEqual(agent.memory.pending()[0]["reason"], "interpreted_needs_review")

    def test_token_budget_stops_provider_before_request(self):
        calls = []
        agent = self.connected(lambda **kw: calls.append(kw),
                               token_budget=TokenBudget(1, lambda messages: 100))
        with self.assertRaises(TokenBudgetExceeded):
            agent(messages=history("Hello"))
        self.assertEqual(calls, [])


class AsyncAutoConnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_async_function_stays_awaitable_with_default_memory(self):
        calls, marker = [], object()

        async def generate(**kwargs):
            await asyncio.sleep(0)
            calls.append(kwargs)
            return marker

        async with auto_connect(generate) as agent:
            self.assertIsInstance(agent, AsyncConnectedAgent)
            self.assertIs(await agent(messages=history()), marker)
            self.assertEqual(agent.memory.entries()[0]["value"], "CSV")
        self.assertEqual(len(calls), 1)

    async def test_decorated_async_plain_function_supports_managed_chat(self):
        calls = []
        response = "Ready: 雪\n"

        async def generate(*, messages, model):
            await asyncio.sleep(0)
            calls.append((messages, model))
            return response

        @functools.wraps(generate)
        def decorated(**kwargs):
            return generate(**kwargs)

        async with auto_connect(decorated) as agent:
            self.assertIsInstance(agent, AsyncConnectedAgent)
            chat = agent.conversation(system="Help with the project.")
            self.assertIs(await chat.send("Export as CSV.", request_id="first", model="host-model"), response)
            self.assertIs(await chat.send("Export as JSON.", request_id="second", model="host-model"), response)
            self.assertEqual(agent.memory.entries()[0]["value"], "JSON")
            self.assertEqual(chat.history[2], {"role": "assistant", "content": response})
        self.assertEqual(len(calls), 2)
        self.assertEqual([call[1] for call in calls], ["host-model", "host-model"])

    async def test_loaded_async_sdk_identities_use_awaitable_connectors(self):
        for provider in BASES:
            for public in (False, True):
                with self.subTest(provider=provider, public=public), loaded_sdk(
                        provider, asynchronous=True, public=public) as cls:
                    client = cls(reply(provider))
                    async with auto_connect(client) as agent:
                        self.assertIsInstance(agent, AsyncConnectedAgent)
                        self.assertIs(await agent(messages=history()), client.response)
                        self.assertEqual(agent.memory.entries()[0]["value"], "CSV")
                    self.assertFalse(client.closed)
                    self.assertEqual(len(client.calls), 1)

    async def test_async_managed_chat_resumes_history_and_default_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            config = RelayConfig(db_path=str(Path(temp) / "relay.db"))
            client = AsyncOllamaStandIn(reply("ollama"))
            async with auto_connect(client, provider="ollama", config=config) as agent:
                session_id = agent.session_id
                chat = agent.conversation(system="Keep helping.")
                self.assertIs(await chat.send("Export as CSV.", request_id="first"), client.response)
            async with auto_connect(client, provider="ollama", config=config, session_id=session_id) as agent:
                chat = agent.conversation()
                self.assertIs(await chat.send("Export as JSON.", request_id="second"), client.response)
                self.assertEqual(agent.memory.entries()[0]["value"], "JSON")
                self.assertEqual(len(chat.history), 5)
            self.assertEqual(len(client.calls), 2)
            self.assertFalse(client.closed)

    async def test_async_stream_remains_provider_owned(self):
        class Stream:
            def __aiter__(self):
                raise AssertionError("Relay consumed the async stream")

            async def aclose(self):
                raise AssertionError("Relay closed the async stream")

        stream = Stream()
        client = AsyncOllamaStandIn(stream)
        async with auto_connect(client, provider="ollama") as agent:
            self.assertIs(await agent(messages=history("Hello"), stream=True), stream)
        self.assertFalse(client.closed)


if __name__ == "__main__":
    unittest.main()
