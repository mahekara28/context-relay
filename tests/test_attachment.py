"""Instance attachment contracts; stand-ins do not imply real SDK validation."""

import asyncio
import copy
import inspect
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay import ContextGuard, RelayConfig, connect
from context_relay.attachment import attach
from context_relay.memory import MemoryPolicy


def history(text="Keep working offline"):
    return [{"role": "user", "content": text}]


class Provider:
    """Official Ollama chat-shaped signature; no SDK or model is involved."""

    def __init__(self):
        self.calls = []
        self.response = object()
        self.closed = False

    def chat(self, model="", messages=None, *, tools=None, stream=False, think=None,
             logprobs=None, top_logprobs=None, format=None, options=None, keep_alive=None):
        self.calls.append({"model": model, "messages": messages, "tools": tools,
                           "stream": stream, "think": think, "logprobs": logprobs,
                           "top_logprobs": top_logprobs, "format": format,
                           "options": options, "keep_alive": keep_alive})
        return self.response

    def close(self):
        self.closed = True


class AsyncProvider(Provider):
    async def chat(self, model="", messages=None, **kwargs):
        await asyncio.sleep(0)
        return super().chat(model, messages, **kwargs)


def packet(messages):
    for message in messages:
        content = message.get("content", "")
        if content.startswith("APPLICATION-RECORDED TASK DATA"):
            return json.loads(content[content.index("{"):])
    raise AssertionError("No Relay data in provider request")


class AttachmentTests(unittest.TestCase):
    def attached(self, client, **options):
        handle = attach(client, **options)
        self.addCleanup(handle.close)
        return handle

    def test_existing_positional_and_keyword_calls_are_prepared(self):
        client = Provider()
        original_signature = inspect.signature(client.chat)
        handle = self.attached(client, method="chat")
        handle.agent.remember("format", "csv")
        self.assertEqual(inspect.signature(client.chat), original_signature)
        self.assertIs(client.chat("local", history()), client.response)
        self.assertIs(client.chat(model="local", messages=history("Next task")), client.response)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(packet(client.calls[0]["messages"])["state"]["facts"]["format"], "csv")
        self.assertEqual(client.calls[1]["messages"][-1]["content"], "Next task")
        self.assertEqual(handle.session_id, handle.agent.session_id)
        self.assertIs(handle.memory, handle.agent.memory)

    def test_all_provider_options_keep_identity(self):
        client = Provider()
        self.attached(client, method="chat")
        tools, options, schema = [object()], object(), object()
        client.chat("local", history(), tools=tools, options=options, stream=True,
                    think="low", logprobs=True, top_logprobs=4, format=schema, keep_alive="5m")
        call = client.calls[0]
        self.assertIs(call["tools"], tools)
        self.assertIs(call["options"], options)
        self.assertIs(call["format"], schema)
        self.assertEqual((call["think"], call["logprobs"], call["top_logprobs"], call["keep_alive"]),
                         ("low", True, 4, "5m"))
        self.assertTrue(call["stream"])

    def test_original_history_is_not_mutated(self):
        client = Provider()
        self.attached(client, method="chat")
        messages = history()
        expected = copy.deepcopy(messages)
        client.chat(messages=messages)
        client.calls[0]["messages"][-1]["content"] = "Provider mutation"
        self.assertEqual(messages, expected)

    def test_only_selected_instance_changes(self):
        first, second = Provider(), Provider()
        class_method = Provider.chat
        self.attached(first, method="chat")
        second.chat(messages=history())
        self.assertEqual(second.calls[0]["messages"], history())
        self.assertIs(Provider.chat, class_method)
        self.assertNotIn("chat", vars(second))

    def test_prebound_original_reference_is_explicitly_outside_attachment(self):
        client = Provider()
        before = client.chat
        self.attached(client, method="chat")
        before(messages=history())
        client.chat(messages=history())
        self.assertEqual(client.calls[0]["messages"], history())
        self.assertIn("state", packet(client.calls[1]["messages"]))

    def test_memory_policy_applies_to_unchanged_client_calls(self):
        client = Provider()
        handle = self.attached(client, method="chat", memory=MemoryPolicy())
        messages = history("Set export format to JSON.")
        client.chat(messages=messages)
        messages += [{"role": "assistant", "content": "Ready"},
                     {"role": "user", "content": "Use CSV instead of JSON."}]
        client.chat(messages=messages)
        self.assertEqual(handle.memory.snapshot()["entries"][0]["value"], "CSV")
        self.assertEqual(handle.agent.guard.turn().context["facts"], {})

    def test_already_connected_callable_is_rejected_before_second_connection(self):
        client = Provider()
        with connect(lambda *, messages: None) as connected_agent:
            client.chat = connected_agent
            with patch("context_relay.attachment.connect") as connected:
                with self.assertRaisesRegex(ValueError, "already connected"):
                    attach(client, method="chat")
                connected.assert_not_called()

    def test_detach_restores_inherited_method_lookup(self):
        client = Provider()
        handle = self.attached(client, method="chat")
        cached_wrapper = client.chat
        self.assertTrue(handle.detach())
        self.assertNotIn("chat", vars(client))
        self.assertIs(client.chat.__func__, Provider.chat)
        self.assertFalse(handle.attached)
        self.assertFalse(handle.detach())
        self.assertFalse(client.closed)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            cached_wrapper(messages=history())

    def test_detach_restores_original_instance_callable_identity(self):
        client = Provider()
        original = lambda *, messages: messages
        client.chat = original
        handle = self.attached(client, method="chat")
        self.assertIsNot(client.chat, original)
        handle.close()
        self.assertIs(vars(client)["chat"], original)

    def test_later_method_replacement_survives_detach(self):
        client = Provider()
        handle = self.attached(client, method="chat")
        replacement = lambda **kwargs: "new host method"
        client.chat = replacement
        self.assertFalse(handle.attached)
        with self.assertRaisesRegex(RuntimeError, "replaced"):
            handle.__enter__()
        self.assertFalse(handle.detach())
        self.assertIs(client.chat, replacement)

    def test_double_attachment_rejected_before_store_creation(self):
        client = Provider()
        self.attached(client, method="chat")
        with patch("context_relay.attachment.connect") as connected:
            with self.assertRaisesRegex(ValueError, "already attached"):
                attach(client, method="chat")
            connected.assert_not_called()

    def test_replaced_but_unclosed_handle_must_detach_before_reattach(self):
        client = Provider()
        handle = self.attached(client, method="chat")
        client.chat = lambda *, messages: "replacement"
        with self.assertRaisesRegex(ValueError, "already attached"):
            attach(client, method="chat")
        handle.detach()
        next_handle = self.attached(client, method="chat")
        self.assertEqual(client.chat(messages=history()), "replacement")
        self.assertTrue(next_handle.attached)

    def test_dotted_path_changes_only_final_resource_instance(self):
        client = types.SimpleNamespace(api=Provider())
        handle = self.attached(client, method="api.chat")
        self.assertIs(client.api.chat("local", history()), client.api.response)
        self.assertTrue(handle.attached)
        handle.close()
        self.assertNotIn("chat", vars(client.api))

    def test_unknown_client_requires_method_no_guessing(self):
        with patch("context_relay.attachment.connect") as connected:
            with self.assertRaisesRegex(ValueError, "Unknown client"):
                attach(Provider())
            connected.assert_not_called()

    def test_module_class_and_nested_module_rejected_without_store(self):
        with patch("context_relay.attachment.connect") as connected:
            for target in (Provider, types.ModuleType("provider")):
                with self.subTest(target=target), self.assertRaises(TypeError):
                    attach(target, method="chat")
            client = types.SimpleNamespace(api=types.ModuleType("provider"))
            with self.assertRaises(TypeError):
                attach(client, method="api.chat")
            connected.assert_not_called()

    def test_readonly_custom_setter_and_slot_targets_rejected_before_db(self):
        class ReadOnly:
            @property
            def chat(self):
                return lambda *, messages: None

        class CustomSetter(Provider):
            def __setattr__(self, key, value):
                super().__setattr__(key, value)

        class Slots:
            __slots__ = ()

            def chat(self, *, messages):
                return None

        class CustomLookup(Provider):
            def __getattribute__(self, name):
                return super().__getattribute__(name)

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "not-created" / "relay.db"
            with patch("context_relay.attachment.connect") as connected:
                for client in (ReadOnly(), CustomSetter(), Slots(), CustomLookup()):
                    with self.subTest(client=type(client).__name__), self.assertRaises(TypeError):
                        attach(client, method="chat", config=RelayConfig(db_path=str(db)))
                connected.assert_not_called()
            self.assertFalse(db.parent.exists())

    def test_invalid_method_and_missing_messages_signature_fail_before_store(self):
        client = types.SimpleNamespace(chat=lambda prompt: prompt, not_callable=1)
        with patch("context_relay.attachment.connect") as connected:
            for method in ("", "_private", "api..chat", "chat()", 4):
                with self.subTest(method=method), self.assertRaises(ValueError):
                    attach(client, method=method)
            with self.assertRaisesRegex(TypeError, "messages"):
                attach(client, method="chat")
            with self.assertRaisesRegex(TypeError, "callable"):
                attach(client, method="not_callable")
            connected.assert_not_called()

    def test_call_binding_rejects_invalid_calls_before_provider(self):
        client = Provider()
        self.attached(client, method="chat")
        for call in (lambda: client.chat("m", history(), messages=history()),
                     lambda: client.chat(unexpected=True, messages=history()),
                     lambda: client.chat("m", history(), "too many")):
            with self.assertRaises(TypeError):
                call()
        for call in (lambda: client.chat(), lambda: client.chat(messages=None)):
            with self.assertRaisesRegex(ValueError, "complete conversation"):
                call()
        self.assertFalse(client.calls)

    def test_positional_only_arguments_and_varargs_keep_binding(self):
        class Positional:
            def chat(self, model, messages, /, *args, **kwargs):
                self.seen = (model, messages, args, kwargs)
                return marker

        marker = object()
        client = Positional()
        self.attached(client, method="chat")
        option = object()
        self.assertIs(client.chat("m", history(), option, private=True), marker)
        self.assertEqual(client.seen[0], "m")
        self.assertIs(client.seen[2][0], option)
        self.assertEqual(client.seen[3], {"private": True})
        self.assertIn("state", packet(client.seen[1]))

    def test_messages_in_kwargs_preserve_other_arguments_including_private_name(self):
        class Flexible:
            def chat(self, *args, **kwargs):
                self.seen = (args, kwargs)
                return kwargs

        client = Flexible()
        self.attached(client, method="chat")
        private = object()
        response = client.chat("first", messages=history(), _relay_bound_arguments=private)
        self.assertEqual(client.seen[0], ("first",))
        self.assertIs(response["_relay_bound_arguments"], private)
        self.assertIn("state", packet(response["messages"]))

    def test_provider_exception_propagates_without_retry(self):
        client = Provider()
        failure = RuntimeError("Provider unavailable")

        def fail(*, messages):
            client.calls.append(messages)
            raise failure

        client.chat = fail
        self.attached(client, method="chat")
        with self.assertRaises(RuntimeError) as caught:
            client.chat(messages=history())
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(client.calls), 1)

    def test_stream_is_never_consumed_or_closed(self):
        class Stream:
            def __iter__(self):
                raise AssertionError("Relay consumed the stream")

            def close(self):
                raise AssertionError("Relay closed the stream")

        client = Provider()
        stream = client.response = Stream()
        with attach(client, method="chat"):
            self.assertIs(client.chat("m", history(), stream=True), stream)
        self.assertFalse(client.closed)

    def test_external_guard_remains_open_after_context_manager(self):
        with ContextGuard() as guard:
            client = Provider()
            with attach(client, method="chat", guard=guard) as handle:
                self.assertEqual(handle.session_id, guard.session_id)
                client.chat(messages=history())
            guard.remember("still_open", True)
            self.assertTrue(guard.turn().context["facts"]["still_open"])

    def test_owned_guard_is_closed_and_provider_remains_open(self):
        client = Provider()
        handle = attach(client, method="chat")
        with patch.object(handle.agent.guard, "close", wraps=handle.agent.guard.close) as close:
            handle.close()
            handle.close()
            close.assert_called_once()
        self.assertFalse(client.closed)

    def test_auto_ollama_detection_uses_loaded_class_identity(self):
        sdk = types.ModuleType("ollama._client")
        sdk.Client = type("Client", (Provider,), {"__module__": "ollama._client"})
        with patch.dict(sys.modules, {"ollama._client": sdk}):
            client = sdk.Client()
            handle = self.attached(client)
            self.assertEqual(handle.agent.config.protocol, "ollama")
            self.assertIs(client.chat("m", tuple(history())), client.response)
            unrelated = type("Client", (Provider,), {"__module__": "unrelated"})()
            with self.assertRaisesRegex(ValueError, "Unknown client"):
                attach(unrelated)

    def test_ollama_explicit_incompatible_config_rejected_before_connect(self):
        sdk = types.ModuleType("ollama._client")
        sdk.Client = type("Client", (Provider,), {"__module__": "ollama._client"})
        with patch.dict(sys.modules, {"ollama._client": sdk}), patch("context_relay.attachment.connect") as connected:
            with self.assertRaisesRegex(ValueError, "protocol='ollama'"):
                attach(sdk.Client(), config=RelayConfig(protocol="plain"))
            connected.assert_not_called()

    def test_known_sdk_message_conversion_and_native_tools(self):
        sdk = types.ModuleType("ollama._client")
        sdk.Client = type("Client", (Provider,), {"__module__": "ollama._client"})
        sdk_types = types.ModuleType("ollama._types")

        class Message:
            __module__ = "ollama._types"

            def __init__(self, value):
                self.value = value

            def model_dump(self, **kwargs):
                self.dump_options = kwargs
                return copy.deepcopy(self.value)

        sdk_types.Message = Message
        assistant = Message({"role": "assistant", "tool_calls": [
            {"function": {"name": "lookup", "arguments": {"id": 7}}}]})
        native = history("Look up 7") + [assistant, {"role": "tool", "tool_name": "lookup", "content": "Found"}]
        with patch.dict(sys.modules, {"ollama._client": sdk, "ollama._types": sdk_types}):
            client = sdk.Client()
            self.attached(client)
            client.chat("m", native)
        delivered = client.calls[0]["messages"]
        self.assertEqual(delivered[-2], assistant.value)
        self.assertEqual(delivered[-1]["role"], "tool")
        self.assertEqual(assistant.dump_options, {"mode": "json", "exclude_none": True})

    def test_native_images_are_rejected_before_provider(self):
        sdk = types.ModuleType("ollama._client")
        sdk.Client = type("Client", (Provider,), {"__module__": "ollama._client"})
        with patch.dict(sys.modules, {"ollama._client": sdk}):
            client = sdk.Client()
            self.attached(client)
            with self.assertRaisesRegex(ValueError, "unsupported fields"):
                client.chat(messages=[{"role": "user", "content": "What is this", "images": ["abc"]}])
            self.assertFalse(client.calls)

    def test_direct_async_generator_rejected_before_connect(self):
        class AsyncGenerator:
            async def chat(self, messages=None):
                yield messages

        with patch("context_relay.attachment.connect") as connected:
            with self.assertRaisesRegex(TypeError, "async-generator"):
                attach(AsyncGenerator(), method="chat")
            connected.assert_not_called()

    def test_async_generator_callable_rejected_before_connect(self):
        class GeneratorCallable:
            async def __call__(self, messages=None):
                yield messages

        client = types.SimpleNamespace(chat=GeneratorCallable())
        with patch("context_relay.attachment.connect") as connected:
            with self.assertRaisesRegex(TypeError, "async-generator"):
                attach(client, method="chat")
            connected.assert_not_called()


class AsyncAttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_callable_instance_is_awaitable_through_attachment(self):
        class Callable:
            async def __call__(self, model, messages=None):
                return model, messages

        client = types.SimpleNamespace(chat=Callable())
        async with attach(client, method="chat"):
            model, delivered = await client.chat("m", history())
        self.assertEqual(model, "m")
        self.assertIn("state", packet(delivered))

    async def test_async_existing_call_and_context_manager(self):
        client = AsyncProvider()
        async with attach(client, method="chat") as handle:
            self.assertTrue(inspect.iscoroutinefunction(client.chat))
            handle.agent.remember("format", "csv")
            self.assertIs(await client.chat("m", history()), client.response)
            self.assertEqual(packet(client.calls[0]["messages"])["state"]["facts"]["format"], "csv")
        self.assertNotIn("chat", vars(client))
        self.assertFalse(client.closed)

    async def test_async_concurrent_calls_keep_separate_bound_arguments(self):
        client = AsyncProvider()
        async with attach(client, method="chat"):
            await asyncio.gather(client.chat("first", history("First")), client.chat("second", history("Second")))
        self.assertEqual([(call["model"], call["messages"][-1]["content"]) for call in client.calls],
                         [("first", "First"), ("second", "Second")])

    async def test_async_stream_passes_through_without_consumption(self):
        class AsyncStream:
            def __aiter__(self):
                raise AssertionError("Relay consumed the async stream")

            async def aclose(self):
                raise AssertionError("Relay closed the async stream")

        client = AsyncProvider()
        stream = client.response = AsyncStream()
        async with attach(client, method="chat"):
            self.assertIs(await client.chat("m", history(), stream=True), stream)

    async def test_async_cancellation_propagates(self):
        class Cancellable:
            async def chat(self, messages=None):
                raise asyncio.CancelledError()

        client = Cancellable()
        async with attach(client, method="chat"):
            with self.assertRaises(asyncio.CancelledError):
                await client.chat(messages=history())


if __name__ == "__main__":
    unittest.main()
