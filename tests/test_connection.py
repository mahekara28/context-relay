"""Existing-client integration contracts; no provider or network is required."""

import asyncio
import copy
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from functools import partial
from pathlib import Path

from context_relay import (
    ActionRejected,
    AgentResult,
    AsyncConnectedAgent,
    BudgetExceeded,
    ConnectedAgent,
    ContextGuard,
    RelayConfig,
    TokenBudget,
    TokenBudgetExceeded,
    connect,
)


def history(request="Continue the current task"):
    return [
        {"role": "system", "content": "Answer the user's task using recorded facts."},
        {"role": "user", "content": "Our application must work offline."},
        {"role": "assistant", "content": "I suggest using a local database."},
        {"role": "user", "content": request},
    ]


class ConnectionTests(unittest.TestCase):
    def connection(self, model_call, **kwargs):
        connected = connect(model_call, **kwargs)
        self.addCleanup(connected.close)
        return connected

    def test_one_connection_prepares_every_call_and_returns_opaque_responses(self):
        calls = []
        responses = [object(), object()]

        def provider(*, messages, **kwargs):
            calls.append((messages, kwargs))
            return responses[len(calls) - 1]

        agent = self.connection(provider)
        self.assertIsInstance(agent, ConnectedAgent)
        agent.remember("mode", "offline")
        first = agent(messages=history())
        agent.remember("mode", "connected")
        second = agent(messages=history("Continue after the change"))

        self.assertIs(first, responses[0])
        self.assertIs(second, responses[1])
        self.assertEqual(len(calls), 2)
        self.assertTrue(any('"mode":"offline"' in message["content"] for message in calls[0][0]))
        self.assertTrue(any('"mode":"connected"' in message["content"] for message in calls[1][0]))
        self.assertEqual(calls[1][0][-1]["content"], "Continue after the change")

    def test_provider_arguments_and_response_identity_are_preserved(self):
        marker = object()
        response = {"opaque_sdk_value": object()}
        seen = []

        def provider(*, messages, **kwargs):
            seen.append(kwargs)
            return response

        agent = self.connection(provider)
        result = agent.invoke(messages=history(), model="local-model", options=marker,
                              temperature=0, stream=True)

        self.assertIsInstance(result, AgentResult)
        self.assertIs(result.response, response)
        self.assertIs(seen[0]["options"], marker)
        self.assertEqual(seen[0]["model"], "local-model")
        self.assertEqual(seen[0]["temperature"], 0)
        self.assertIs(seen[0]["stream"], True)
        self.assertEqual(result.prepared.report["session_id"], agent.session_id)
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            result.response = None

    def test_provider_mutation_cannot_change_original_or_captured_evidence(self):
        original = history()
        expected = copy.deepcopy(original)

        def provider(*, messages):
            messages[0]["content"] = "mutated by provider"
            messages.clear()
            original[-1]["content"] = "later host edit"
            return None

        agent = self.connection(provider)
        result = agent.invoke(messages=original)
        self.assertEqual(result.prepared.messages[0], expected[0])
        self.assertEqual(result.prepared.messages[-1], expected[-1])
        recovered = result.prepared.recover_evidence([1], history_id=result.prepared.history_id)
        self.assertEqual(recovered.messages, expected[1:3])
        # The provider explicitly edited its host closure, but not the copied
        # earlier messages. The prepared archive still predates that host edit.
        self.assertEqual(original[:-1], expected[:-1])

    def test_normal_provider_input_mutation_leaves_host_history_unchanged(self):
        original = history()
        expected = copy.deepcopy(original)

        def provider(*, messages):
            messages[1]["content"] = "changed"
            messages.pop()
            return "done"

        self.connection(provider)(messages=original)
        self.assertEqual(original, expected)

    def test_configured_selection_settings_apply_to_every_call(self):
        messages = [{"role": "system", "content": "Follow the user's task."}]
        for index in range(6):
            messages.extend([
                {"role": "user", "content": f"Topic {index}. " + "archive detail " * 25},
                {"role": "assistant", "content": f"Response {index}."},
            ])
        messages.append({"role": "user", "content": "Continue topic 0"})
        config = RelayConfig(max_chars=2000, recent_turns=1, retrieve_turns=1, pinned=(3,))
        agent = self.connection(lambda **kwargs: None, config=config)
        result = agent.invoke(messages=messages)

        report = result.prepared.report
        self.assertEqual(report["max_chars"], 2000)
        self.assertLessEqual(report["size_chars"], 2000)
        self.assertTrue({0, 3, 4, len(messages) - 1}.issubset(report["required_indices"]))
        self.assertNotIn(len(messages) - 2, report["required_indices"])
        self.assertGreater(len(report["omitted_indices"]), 0)
        self.assertEqual(report["retrieval"]["requested_turns"], 1)

    def test_config_file_is_loaded_once_and_used_by_factory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.json"
            path.write_text(json.dumps({"max_chars": 2100, "recent_turns": 1}), encoding="utf-8")
            agent = self.connection(lambda **kwargs: None, config=path)
            path.write_text("invalid after connection", encoding="utf-8")
            self.assertEqual(agent.invoke(messages=history()).prepared.report["max_chars"], 2100)

    def test_preparation_failures_never_reach_provider(self):
        calls = []
        agent = self.connection(lambda **kwargs: calls.append(kwargs),
                                config=RelayConfig(max_chars=10))
        with self.assertRaises(BudgetExceeded):
            agent(messages=history())
        with self.assertRaises(ValueError):
            agent(messages=[{"role": "tool", "content": "opaque tool response"}])
        self.assertEqual(calls, [])

    def test_token_budget_is_enforced_before_provider_call(self):
        counts = []
        calls = []

        def counter(messages):
            counts.append(copy.deepcopy(messages))
            return 500

        budget = TokenBudget(1000, counter, reserve_output_tokens=100, counter_name="fixture")
        agent = self.connection(lambda **kwargs: calls.append(kwargs), token_budget=budget)
        result = agent.invoke(messages=history())
        self.assertTrue(counts)
        self.assertEqual(result.prepared.report["token_budget"]["input_tokens"], 500)
        self.assertEqual(result.prepared.report["token_budget"]["input_limit"], 900)
        self.assertEqual(len(calls), 1)

        blocked = self.connection(lambda **kwargs: calls.append(kwargs),
                                  token_budget=TokenBudget(400, counter))
        with self.assertRaises(TokenBudgetExceeded):
            blocked(messages=history())
        self.assertEqual(len(calls), 1)

    def test_provider_exception_is_not_retried_or_replaced(self):
        failure = RuntimeError("provider unavailable")
        calls = []

        def provider(*, messages):
            calls.append(messages)
            raise failure

        agent = self.connection(provider)
        with self.assertRaises(RuntimeError) as raised:
            agent(messages=history())
        self.assertIs(raised.exception, failure)
        self.assertEqual(len(calls), 1)

    def test_sync_stream_is_returned_without_consumption(self):
        consumed = []

        def chunks():
            consumed.append("started")
            yield "first chunk"

        stream = chunks()
        self.addCleanup(stream.close)
        agent = self.connection(lambda **kwargs: stream)
        self.assertIs(agent(messages=history()), stream)
        self.assertEqual(consumed, [])
        self.assertEqual(next(stream), "first chunk")
        self.assertEqual(consumed, ["started"])

    def test_generator_model_function_is_supported_without_eager_iteration(self):
        calls = []

        def provider(*, messages):
            calls.append(messages)
            yield "chunk"

        agent = self.connection(provider)
        result = agent.invoke(messages=history())
        self.addCleanup(result.response.close)
        self.assertEqual(calls, [])
        self.assertEqual(next(result.response), "chunk")
        self.assertEqual(calls[0], result.prepared.messages)

    def test_disguised_coroutine_is_closed_in_sync_path(self):
        ran = []

        async def deferred():
            ran.append("body")
            return "answer"

        coroutine = deferred()
        self.addCleanup(coroutine.close)
        agent = self.connection(lambda **kwargs: coroutine)
        with self.assertRaises(TypeError):
            agent(messages=history())
        self.assertIsNone(coroutine.cr_frame)
        self.assertEqual(ran, [])

    def test_connections_create_separate_sessions_even_in_same_database(self):
        with tempfile.TemporaryDirectory() as directory:
            config = RelayConfig(db_path=str(Path(directory) / "relay.db"))
            first = connect(lambda **kwargs: None, config=config)
            second = connect(lambda **kwargs: None, config=config)
            try:
                first.remember("customer", "first customer")
                second.remember("customer", "second customer")
                self.assertNotEqual(first.session_id, second.session_id)
                self.assertEqual(first.invoke(messages=history()).prepared.context["facts"]["customer"], "first customer")
                self.assertEqual(second.invoke(messages=history()).prepared.context["facts"]["customer"], "second customer")
            finally:
                first.close()
                second.close()

    def test_explicit_session_resume_preserves_current_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            config = RelayConfig(db_path=str(Path(directory) / "relay.db"))
            with connect(lambda **kwargs: None, config=config) as agent:
                agent.remember("destination", "Pune")
                session_id = agent.session_id
            with connect(lambda **kwargs: None, config=config, session_id=session_id) as resumed:
                self.assertEqual(resumed.session_id, session_id)
                self.assertEqual(resumed.invoke(messages=history()).prepared.context["facts"]["destination"], "Pune")

    def test_external_guard_is_used_and_remains_open_after_connection_close(self):
        with ContextGuard() as guard:
            agent = connect(lambda **kwargs: None, guard=guard)
            self.assertIs(agent.guard, guard)
            self.assertEqual(agent.session_id, guard.session_id)
            agent.remember("destination", "Delhi")
            self.assertEqual(guard.turn().context["facts"]["destination"], "Delhi")
            agent.close()
            agent.close()
            guard.remember("destination", "Chennai")
            self.assertEqual(guard.turn().context["facts"]["destination"], "Chennai")

    def test_external_guard_rejects_ignored_scope_configuration(self):
        with ContextGuard() as guard:
            with self.assertRaises(ValueError):
                connect(lambda **kwargs: None, guard=guard, session_id=guard.session_id)
            with self.assertRaises(ValueError):
                connect(lambda **kwargs: None, guard=guard, config=RelayConfig(db_path="ignored.db"))
            allowed = self.connection(lambda **kwargs: None, guard=guard,
                                      config=RelayConfig(name="Different label is harmless"))
            self.assertIs(allowed.guard, guard)

    def test_closing_owned_connection_rejects_calls_and_mutations_before_side_effects(self):
        calls = []
        agent = self.connection(lambda **kwargs: calls.append(kwargs))
        agent.close()
        agent.close()
        for operation in (
            lambda: agent(messages=history()),
            lambda: agent.invoke(messages=history()),
            lambda: agent.remember("key", "value"),
            lambda: agent.allow_tool("tool"),
        ):
            with self.subTest(operation=operation), self.assertRaises(RuntimeError):
                operation()
        self.assertEqual(calls, [])
        with self.assertRaises(ValueError):
            agent.guard.turn()

    def test_double_wrapping_is_rejected(self):
        agent = self.connection(lambda **kwargs: None)
        with self.assertRaises((TypeError, ValueError)):
            connect(agent)

    def test_invalid_callables_are_rejected_at_connection_time(self):
        for value in (None, "model name", 42, object()):
            with self.subTest(value=value), self.assertRaises(TypeError):
                connect(value)

    def test_required_positional_only_parameters_fail_before_database_creation(self):
        def provider(client, /, *, messages):
            return client

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "not-created" / "relay.db"
            with self.assertRaises(TypeError):
                connect(provider, config=RelayConfig(db_path=str(database)))
            self.assertFalse(database.parent.exists())

    def test_adapter_and_partial_can_bind_required_positional_only_parameters(self):
        marker = object()
        calls = []

        def provider(client, /, *, messages, model):
            calls.append((client, messages, model))
            return marker

        def adapter(*, messages, **kwargs):
            return provider(marker, messages=messages, **kwargs)

        for model_call in (adapter, partial(provider, marker)):
            with self.subTest(model_call=model_call):
                agent = self.connection(model_call)
                self.assertIs(agent(messages=history(), model="local-model"), marker)
                self.assertIs(calls[-1][0], marker)
                self.assertEqual(calls[-1][1][-1], history()[-1])
                self.assertEqual(calls[-1][2], "local-model")

    def test_defaulted_positional_only_parameter_can_keep_its_default(self):
        marker = object()
        seen = []

        def provider(client=marker, /, *, messages):
            seen.append((client, messages))
            return client

        agent = self.connection(provider)
        self.assertIs(agent(messages=history()), marker)
        self.assertIs(seen[0][0], marker)
        self.assertEqual(seen[0][1][-1], history()[-1])

    def test_model_without_keyword_messages_fails_before_database_creation(self):
        def no_messages(*, prompt):
            return prompt

        def positional_messages(messages, /):
            return messages

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "not-created" / "relay.db"
            for model_call in (no_messages, positional_messages):
                with self.subTest(model_call=model_call), self.assertRaises(TypeError):
                    connect(model_call, config=RelayConfig(db_path=str(database)))
                self.assertFalse(database.parent.exists())

    def test_invalid_runtime_dependencies_fail_before_database_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "not-created" / "relay.db"
            config = RelayConfig(db_path=str(database))
            for kwargs in ({"token_budget": object()}, {"guard": object()}):
                with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                    connect(lambda **kwargs: None, config=config, **kwargs)
                self.assertFalse(database.parent.exists())
        with self.assertRaises(TypeError):
            connect(lambda **kwargs: None, config={"max_chars": 1000})

    def test_each_result_keeps_the_turn_needed_for_its_own_action_guard(self):
        agent = self.connection(lambda **kwargs: object())
        agent.remember("destination", "Pune")
        agent.allow_tool("ship", bindings={"destination": "destination"})
        earlier = agent.invoke(messages=history("Ship to Pune"))
        agent.remember("destination", "Delhi")
        latest = agent.invoke(messages=history("Ship to Delhi"))
        calls = []

        def ship(destination):
            calls.append(destination)
            return destination

        with self.assertRaises(ActionRejected):
            earlier.prepared.wrap(ship)("Pune")
        self.assertEqual(calls, [])
        self.assertEqual(latest.prepared.wrap(ship)("Delhi"), "Delhi")
        self.assertEqual(calls, ["Delhi"])
        self.assertLess(earlier.prepared.revision, latest.prepared.revision)


class AsyncConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_function_and_async_callable_are_detected(self):
        response = object()

        async def provider(*, messages, marker):
            self.assertIs(marker, response)
            return response

        class Client:
            async def __call__(self, **kwargs):
                return await provider(**kwargs)

        for model_call in (provider, Client()):
            with self.subTest(model_call=model_call):
                async with connect(model_call) as agent:
                    self.assertIsInstance(agent, AsyncConnectedAgent)
                    self.assertIs(await agent(messages=history(), marker=response), response)
                    result = await agent.invoke(messages=history(), marker=response)
                    self.assertIs(result.response, response)
                    self.assertEqual(result.prepared.messages[-1], history()[-1])

    async def test_async_context_closes_on_provider_exception_without_replacing_it(self):
        failure = RuntimeError("provider failed")
        calls = []

        async def provider(*, messages):
            calls.append(messages)
            raise failure

        agent = connect(provider)
        with self.assertRaises(RuntimeError) as raised:
            async with agent:
                await agent(messages=history())
        self.assertIs(raised.exception, failure)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(RuntimeError):
            await agent(messages=history())

    async def test_cancellation_propagates_without_retry(self):
        started = asyncio.Event()
        stopped = []
        calls = []

        async def provider(*, messages):
            calls.append(messages)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append("provider cancelled")

        async with connect(provider) as agent:
            pending = asyncio.create_task(agent(messages=history()))
            await asyncio.wait_for(started.wait(), timeout=2)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        self.assertEqual(len(calls), 1)
        self.assertEqual(stopped, ["provider cancelled"])

    async def test_out_of_order_async_results_keep_their_own_captured_revision(self):
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        responses = {"first": object(), "second": object()}

        async def provider(*, messages, label):
            if label == "first":
                first_started.set()
                await release_first.wait()
            return responses[label]

        async with connect(provider) as agent:
            agent.remember("destination", "Pune")
            first_pending = asyncio.create_task(agent.invoke(messages=history("First task"), label="first"))
            try:
                await asyncio.wait_for(first_started.wait(), timeout=2)
                agent.remember("destination", "Delhi")
                second = await agent.invoke(messages=history("Second task"), label="second")
            finally:
                release_first.set()
            first = await asyncio.wait_for(first_pending, timeout=2)

        self.assertIs(first.response, responses["first"])
        self.assertIs(second.response, responses["second"])
        self.assertEqual(first.prepared.context["facts"]["destination"], "Pune")
        self.assertEqual(second.prepared.context["facts"]["destination"], "Delhi")
        self.assertLess(first.prepared.revision, second.prepared.revision)
        self.assertNotEqual(first.prepared.history_id, second.prepared.history_id)

    async def test_async_stream_response_passes_through_without_consumption(self):
        consumed = []

        async def chunks():
            consumed.append("stream body")
            yield "chunk"

        stream = chunks()

        async def provider(*, messages):
            return stream

        try:
            async with connect(provider) as agent:
                self.assertIs(await agent(messages=history()), stream)
                self.assertEqual(consumed, [])
            self.assertEqual(await anext(stream), "chunk")
            self.assertEqual(consumed, ["stream body"])
        finally:
            await stream.aclose()

    async def test_direct_async_generator_clients_are_rejected_before_execution(self):
        ran = []

        async def provider(*, messages):
            ran.append("body")
            yield "chunk"

        class StreamingClient:
            async def __call__(self, *, messages):
                yield "chunk"

        for model_call in (provider, StreamingClient()):
            with self.subTest(model_call=model_call), self.assertRaises(TypeError):
                connect(model_call)
        self.assertEqual(ran, [])

    async def test_async_double_wrapping_is_rejected(self):
        async def provider(*, messages):
            return None

        async with connect(provider) as agent:
            with self.assertRaises((TypeError, ValueError)):
                connect(agent)

    async def test_async_budget_failure_never_reaches_provider(self):
        calls = []

        async def provider(*, messages):
            calls.append(messages)

        async with connect(provider, config=RelayConfig(max_chars=10)) as agent:
            with self.assertRaises(BudgetExceeded):
                await agent(messages=history())
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
