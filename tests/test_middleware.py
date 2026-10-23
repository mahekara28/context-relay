"""Behavioral tests for inserting Context Relay around existing Python tools."""

import inspect
import tempfile
import unittest
from pathlib import Path

from context_relay import ActionRejected, BudgetExceeded, ContextGuard, ToolExecutionError


class MiddlewareTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard(db_path=":memory:", name="Existing application")
        self.guard.__enter__()
        self.addCleanup(self.guard.__exit__, None, None, None)
        self.guard.remember("order_id", "ORDER-42")
        self.guard.remember("intent", "replacement")
        self.guard.allow_tool(
            "replace_order",
            when={"intent": "replacement"},
            bindings={"order_id": "order_id"},
            optional_args=("note",),
        )
        self.calls = []

    def replacement(self, order_id, *, note="standard shipping"):
        self.calls.append((order_id, note))
        return {"order": order_id, "note": note, "accepted": True}

    def wrapped(self, turn=None):
        turn = turn if turn is not None else self.guard.turn()
        return turn.wrap(self.replacement, tool="replace_order")

    def test_capture_cannot_exceed_execution_budget(self):
        for budget in (12001, True, -1, 12000.0):
            with self.subTest(budget=budget), self.assertRaisesRegex(ValueError, "execution ceiling"):
                self.guard.turn(max_chars=budget)
        self.assertEqual(self.calls, [])

    def test_smaller_capture_budget_never_silently_drops_facts(self):
        with self.assertRaises(BudgetExceeded):
            self.guard.turn(max_chars=10)
        self.assertEqual(self.guard.turn().context["facts"]["order_id"], "ORDER-42")

    def test_existing_function_returns_its_result_directly(self):
        result = self.wrapped()("ORDER-42", note="Call on arrival")
        self.assertEqual(result, {"order": "ORDER-42", "note": "Call on arrival", "accepted": True})
        self.assertEqual(self.calls, [("ORDER-42", "Call on arrival")])
        receipts = [event for event in self.guard.store.events(self.guard.session_id)
                    if event["kind"] == "tool_result"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["status"], "succeeded")

    def test_function_name_can_supply_the_policy_tool_name(self):
        calls = []

        def replace_order(order_id, *, note="standard shipping"):
            calls.append(order_id)
            return "replacement created"

        wrapped = self.guard.turn().wrap(replace_order)
        self.assertEqual(wrapped(order_id="ORDER-42"), "replacement created")
        self.assertEqual(calls, ["ORDER-42"])
        self.assertEqual(inspect.signature(wrapped), inspect.signature(replace_order))

    def test_correction_after_model_snapshot_prevents_execution(self):
        turn = self.guard.turn()
        wrapped = self.wrapped(turn)
        self.guard.remember("intent", "refund", text="Customer changed their mind")

        with self.assertRaises(ActionRejected) as raised:
            wrapped("ORDER-42")

        rejection = raised.exception
        self.assertEqual(rejection.decision, "block")
        self.assertEqual(rejection.verdict["decision"], rejection.decision)
        self.assertEqual(rejection.recovery_context["facts"]["intent"], "refund")
        self.assertEqual(self.calls, [])
        self.assertEqual(turn.context["facts"]["intent"], "replacement")

    def test_unrelated_change_requires_replanning_without_running_callback(self):
        turn = self.guard.turn()
        self.guard.remember("delivery_note", "Use the side entrance")
        with self.assertRaises(ActionRejected) as raised:
            self.wrapped(turn)("ORDER-42")
        self.assertEqual(raised.exception.decision, "refresh")
        self.assertEqual(self.calls, [])

    def test_a_successful_tool_invalidates_the_same_turn(self):
        wrapped = self.wrapped()
        wrapped("ORDER-42")
        with self.assertRaises(ActionRejected) as raised:
            wrapped("ORDER-42")
        self.assertEqual(raised.exception.decision, "refresh")
        self.assertEqual(len(self.calls), 1)

    def test_new_turn_allows_a_later_deliberate_call(self):
        self.wrapped()("ORDER-42")
        self.wrapped()("ORDER-42")
        self.assertEqual(len(self.calls), 2)

    def test_bound_argument_cannot_target_another_order(self):
        with self.assertRaises(ActionRejected) as raised:
            self.wrapped()("ORDER-99")
        self.assertEqual(raised.exception.decision, "block")
        self.assertTrue(any(issue["code"] == "binding_mismatch"
                            for issue in raised.exception.verdict["issues"]))
        self.assertEqual(self.calls, [])

    def test_matching_default_argument_is_checked_and_passed(self):
        calls = []

        def handler(order_id="ORDER-42", *, note="standard shipping"):
            calls.append((order_id, note))
            return {"order_id": order_id}

        wrapped = self.guard.turn().wrap(handler, tool="replace_order")
        self.assertEqual(wrapped(), {"order_id": "ORDER-42"})
        self.assertEqual(calls, [("ORDER-42", "standard shipping")])

    def test_wrong_default_cannot_bypass_the_binding(self):
        calls = []

        def handler(order_id="ORDER-99", *, note="standard shipping"):
            calls.append(order_id)
            return True

        wrapped = self.guard.turn().wrap(handler, tool="replace_order")
        with self.assertRaises(ActionRejected):
            wrapped()
        self.assertEqual(calls, [])

    def test_positional_only_and_keyword_only_arguments_keep_python_semantics(self):
        calls = []

        def handler(order_id, /, *, note="standard shipping"):
            calls.append((order_id, note))
            return note

        wrapped = self.guard.turn().wrap(handler, tool="replace_order")
        with self.assertRaises(TypeError):
            wrapped(order_id="ORDER-42")
        self.assertEqual(calls, [])
        self.assertEqual(wrapped("ORDER-42", note="Fragile"), "Fragile")
        self.assertEqual(calls, [("ORDER-42", "Fragile")])

    def test_python_argument_errors_do_not_execute_the_function(self):
        wrapped = self.wrapped()
        for args, kwargs in [ ((), {}),
                              (("ORDER-42", "unexpected"), {}),
                              (("ORDER-42",), {"order_id": "ORDER-42"}),
                              (("ORDER-42",), {"force": True}) ]:
            with self.subTest(args=args, kwargs=kwargs), self.assertRaises(TypeError):
                wrapped(*args, **kwargs)
        self.assertEqual(self.calls, [])

    def test_turn_context_is_a_defensive_copy(self):
        self.guard.remember("metadata", {"tags": ["important"]})
        turn = self.guard.turn()
        original_revision = turn.revision
        exported = turn.context
        exported["facts"]["intent"] = "refund"
        exported["facts"]["metadata"]["tags"].append("mutated")
        exported["revision"] = 999999
        exported["session_id"] = "another-session"
        self.assertEqual(turn.revision, original_revision)
        self.assertEqual(turn.context["facts"]["intent"], "replacement")
        self.assertEqual(turn.context["facts"]["metadata"]["tags"], ["important"])
        self.assertEqual(turn.context["session_id"], self.guard.session_id)
        self.assertIn("ORDER-42", turn.context_text)
        self.wrapped(turn)("ORDER-42")
        self.assertEqual(len(self.calls), 1)

    def test_callback_receives_a_copy_of_checked_mutable_arguments(self):
        self.guard.remember("payload", {"items": ["A"]})
        self.guard.allow_tool("submit", bindings={"payload": "payload"})
        original = {"items": ["A"]}

        def submit(payload):
            payload["items"].append("B")
            return payload

        result = self.guard.turn().wrap(submit)(original)
        self.assertEqual(result, {"items": ["A", "B"]})
        self.assertEqual(original, {"items": ["A"]})
        self.assertEqual(self.guard.store.facts(self.guard.session_id)["payload"]["value"], original)

    def test_sync_wrapper_rejects_async_and_generator_functions(self):
        async def asynchronous(order_id):
            return order_id

        def generator(order_id):
            yield order_id

        class AsyncCallable:
            async def __call__(self, order_id):
                return order_id

        for function in (asynchronous, generator, AsyncCallable()):
            with self.subTest(function=type(function).__name__), self.assertRaises((TypeError, ValueError)):
                self.guard.turn().wrap(function, tool="replace_order")

    def test_variadic_signatures_are_rejected(self):
        def positional(*args):
            return list(args)

        def keyword(**kwargs):
            return kwargs

        for function in (positional, keyword):
            with self.subTest(function=function.__name__), self.assertRaises((TypeError, ValueError)):
                self.guard.turn().wrap(function, tool="replace_order")

    def test_sync_decorator_cannot_conceal_an_async_tool_result(self):
        created = []
        ran = []

        async def underlying(order_id):
            ran.append(order_id)
            return True

        def concealed(order_id, *, note="standard shipping"):
            coroutine = underlying(order_id)
            created.append(coroutine)
            self.addCleanup(coroutine.close)
            return coroutine

        with self.assertRaises(ToolExecutionError) as raised:
            self.guard.turn().wrap(concealed, tool="replace_order")("ORDER-42")
        self.assertIsInstance(raised.exception.__cause__, TypeError)
        self.assertEqual(ran, [])
        self.assertEqual(inspect.getcoroutinestate(created[0]), inspect.CORO_CLOSED)

    def test_callback_failure_is_distinct_from_a_policy_rejection(self):
        failure = RuntimeError("Existing service unavailable")

        def broken(order_id, *, note="standard shipping"):
            raise failure

        with self.assertRaises(ToolExecutionError) as raised:
            self.guard.turn().wrap(broken, tool="replace_order")("ORDER-42")
        self.assertIs(raised.exception.__cause__, failure)
        self.assertEqual(raised.exception.receipt["status"], "failed")
        self.assertEqual(raised.exception.check["decision"], "allow")

    def test_defaults_not_permitted_by_policy_are_not_silently_accepted(self):
        calls = []

        def handler(order_id, *, force=True):
            calls.append(order_id)
            return True

        with self.assertRaises(ActionRejected) as raised:
            self.guard.turn().wrap(handler, tool="replace_order")("ORDER-42")
        self.assertTrue(any(issue["code"] == "unexpected_argument"
                            for issue in raised.exception.verdict["issues"]))
        self.assertEqual(calls, [])

    def test_dispatcher_returns_result_and_uses_its_original_snapshot(self):
        calls = []

        def existing_dispatcher(tool, arguments):
            calls.append((tool, arguments))
            return {"dispatched": tool, "order": arguments["order_id"]}

        protected = self.guard.turn().wrap_dispatcher(existing_dispatcher)
        self.assertEqual(protected("replace_order", {"order_id": "ORDER-42"}),
                         {"dispatched": "replace_order", "order": "ORDER-42"})
        with self.assertRaises(ActionRejected) as raised:
            protected("replace_order", {"order_id": "ORDER-42"})
        self.assertEqual(raised.exception.decision, "refresh")
        self.assertEqual(len(calls), 1)

    def test_dispatcher_cannot_execute_an_unknown_tool(self):
        calls = []
        protected = self.guard.turn().wrap_dispatcher(lambda tool, arguments: calls.append(tool))
        with self.assertRaises(ActionRejected) as raised:
            protected("delete_everything", {})
        self.assertEqual(raised.exception.decision, "block")
        self.assertEqual(calls, [])

    def test_dispatcher_does_not_accept_a_model_supplied_revision(self):
        protected = self.guard.turn().wrap_dispatcher(lambda tool, arguments: True)
        with self.assertRaises(TypeError):
            protected("replace_order", {"order_id": "ORDER-42"}, context_revision=999999)

    def test_closed_store_prevents_a_saved_wrapper_from_executing(self):
        with ContextGuard(db_path=":memory:") as guard:
            guard.allow_tool("ping")
            calls = []

            def ping():
                calls.append(True)
                return "pong"

            wrapped = guard.turn().wrap(ping)
        with self.assertRaises((ValueError, RuntimeError)):
            wrapped()
        self.assertEqual(calls, [])


class PersistedMiddlewareTests(unittest.TestCase):
    def test_an_existing_session_can_resume_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "agent.sqlite3"
            with ContextGuard(db_path=db_path, name="Persistent workflow") as guard:
                session_id = guard.session_id
                guard.remember("order_id", "ORDER-42")
                guard.allow_tool("replace_order", bindings={"order_id": "order_id"})

            with ContextGuard(db_path=db_path, session_id=session_id) as resumed:
                self.assertEqual(resumed.session_id, session_id)
                self.assertEqual(resumed.turn().context["facts"]["order_id"], "ORDER-42")
                calls = []

                def replace_order(order_id):
                    calls.append(order_id)
                    return True

                self.assertIs(resumed.turn().wrap(replace_order)("ORDER-42"), True)
                self.assertEqual(calls, ["ORDER-42"])
                self.assertEqual(len(resumed.store.list_sessions()), 1)

    def test_sessions_sharing_one_database_do_not_share_facts_or_policy_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "agent.sqlite3"
            with ContextGuard(db_path=db_path, name="Customer A") as first:
                first.remember("order_id", "A-42")
                first.allow_tool("replace_order", bindings={"order_id": "order_id"})
                first_turn = first.turn()
                with ContextGuard(db_path=db_path, name="Customer B") as second:
                    second.remember("order_id", "B-77")
                    second.allow_tool("replace_order", bindings={"order_id": "order_id"})
                    second_turn = second.turn()
                    calls = []

                    def replace_order(order_id):
                        calls.append(order_id)
                        return order_id

                    with self.assertRaises(ActionRejected):
                        first_turn.wrap(replace_order)("B-77")
                    self.assertEqual(calls, [])
                    self.assertEqual(second_turn.wrap(replace_order)("B-77"), "B-77")
                    self.assertEqual(first_turn.wrap(replace_order)("A-42"), "A-42")
                    self.assertNotEqual(first_turn.context["session_id"], second_turn.context["session_id"])


if __name__ == "__main__":
    unittest.main()
