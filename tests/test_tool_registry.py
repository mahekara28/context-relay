"""One-time tool registration with fixed-turn execution; no model/network calls."""

import asyncio
import inspect
import json
import tempfile
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from context_relay import (
    ActionRejected, AutomaticMemory, ContentPolicy, ContextGuard, MemoryPolicy,
    ToolExecutionError, connect,
)
from context_relay.tools import ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.registry = ToolRegistry(self.guard)
        self.calls = []

    def test_decorator_registers_policy_and_preserves_bound_signature(self):
        self.guard.remember("approved", True)
        self.guard.remember("destination", "local.json")

        @self.registry.tool(when={"approved": True}, bindings={"destination": "destination"},
                            unchecked=("summary",))
        def save(destination, /, *, summary="Ready"):
            """Write one approved export."""
            self.calls.append((destination, summary))
            return {"destination": destination, "summary": summary}

        with self.assertRaisesRegex(RuntimeError, "captured turn"):
            save("local.json")
        self.assertEqual(self.calls, [])
        bound = self.registry.bind(self.guard.turn())
        self.assertEqual(bound.names, ("save",))
        wrapped = bound["save"]
        self.assertEqual(wrapped.__name__, "save")
        self.assertEqual(wrapped.__doc__, "Write one approved export.")
        self.assertEqual(str(inspect.signature(wrapped)), "(destination, /, *, summary='Ready')")
        self.assertEqual(wrapped("local.json"), {"destination": "local.json", "summary": "Ready"})
        self.assertEqual(self.calls, [("local.json", "Ready")])
        with self.assertRaises(ActionRejected):
            wrapped("local.json")
        self.assertEqual(len(self.calls), 1)

    def test_dispatch_supports_positional_only_and_keyword_only_defaults(self):
        self.guard.remember("one", 1)

        def existing(first, /, second=2, *, third=3):
            self.calls.append([first, second, third])
            return [first, second, third]

        self.registry.register(existing, name="sum", bindings={"first": "one"}, unchecked=("second", "third"))
        bound = self.registry.bind(self.guard.turn())
        self.assertEqual(bound.dispatch("sum", {"first": 1}), [1, 2, 3])
        self.assertEqual(self.calls, [[1, 2, 3]])

    def test_mutable_defaults_are_snapshotted_and_copied(self):
        default = ["source"]

        def existing(rows=default):
            rows.append("executor")
            return rows

        placeholder = self.registry.register(existing, unchecked=("rows",))
        default.append("later source mutation")
        inspect.signature(placeholder).parameters["rows"].default.append("inspection mutation")
        wrapped = self.registry.bind(self.guard.turn())["existing"]
        inspect.signature(wrapped).parameters["rows"].default.append("wrapped inspection mutation")
        self.assertEqual(wrapped(), ["source", "executor"])
        self.assertEqual(self.registry.bind(self.guard.turn())["existing"](), ["source", "executor"])
        self.assertEqual(default, ["source", "later source mutation"])

    def test_missing_coverage_and_misspellings_never_register_policy(self):
        def export(format="JSON", *, rows=None):
            return rows

        revision = self.guard.turn().revision
        for options in (
            {"unchecked": ("format",)},
            {"unchecked": ("format", "rows", "rowz")},
            {"bindings": {"formta": "export_format"}, "unchecked": ("rows",)},
            {"content": ContentPolicy({"rowz": []}, exact=("/rowz",)), "unchecked": ("format", "rows")},
            {"memory_bindings": {"language": "response_language"}, "unchecked": ("format", "rows")},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.registry.register(export, **options)
        self.assertEqual(self.guard.store.policies(self.guard.session_id), [])
        self.assertEqual(self.guard.turn().revision, revision)
        self.assertEqual(self.registry.coverage()["registered_tools"], [])

    def test_default_is_checked_even_when_caller_omits_it(self):
        self.guard.remember("export_format", "JSON")
        self.registry.register(lambda format="CSV": self.calls.append(format),
                               name="export", bindings={"format": "export_format"})
        bound = self.registry.bind(self.guard.turn())
        with self.assertRaises(ActionRejected):
            bound["export"]()
        with self.assertRaises(ActionRejected):
            bound.dispatch("export", {})
        self.assertEqual(self.calls, [])

    def test_memory_mapping_is_opt_in_and_missing_memory_blocks(self):
        def export(format, language):
            self.calls.append((format, language))
            return [format, language]

        with self.assertRaisesRegex(ValueError, "Declare checks"):
            self.registry.register(export)
        self.registry.register(export, memory=True)
        with self.assertRaises(ActionRejected):
            self.registry.bind(self.guard.turn()).dispatch("export", {"format": "JSON", "language": "Hindi"})
        self.assertEqual(self.calls, [])
        memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        memory.process([{"role": "user", "content": "Export as JSON. Reply in Hindi."}])
        self.assertEqual(self.registry.bind(self.guard.turn()).dispatch("export", {
            "format": "json", "language": "hindi",
        }), ["JSON", "Hindi"])

    def test_explicit_memory_mapping_and_fact_binding_take_precedence(self):
        self.guard.remember("allowed_format", "JSON")
        self.registry.register(lambda format, language: None, name="export", memory=True,
                               bindings={"format": "allowed_format"},
                               memory_bindings={"language": "customer_language"})
        coverage = self.registry.coverage()["tools"]["export"]["checked"]
        self.assertEqual(coverage, {"format": {"fact": "allowed_format"},
                                    "language": {"memory": "customer_language"}})

    def test_content_rule_and_inventory_report_are_explicit_and_redacted(self):
        source = {"rows": [{"item": "Private product", "quantity": 4}]}
        self.registry.register(lambda rows, summary="": rows, name="export",
                               content=ContentPolicy(source, exact=("/rows",)), unchecked=("summary",))
        report = self.registry.coverage(["export", "delete_file"])
        self.assertEqual(report["unregistered_tools"], ["delete_file"])
        self.assertFalse(report["inventory_fully_registered"])
        self.assertFalse(report["direct_calls_protected"])
        self.assertFalse(report["semantic_meaning_checked"])
        self.assertEqual(report["tools"]["export"]["unchecked"], ["summary"])
        self.assertEqual(report["tools"]["export"]["checked"], {"rows": {"content": True}})
        self.assertNotIn("Private product", json.dumps(report))
        report["tools"]["export"]["unchecked"].append("rows")
        self.assertEqual(self.registry.coverage()["tools"]["export"]["unchecked"], ["summary"])
        self.assertIsNone(self.registry.coverage()["inventory_fully_registered"])
        self.assertEqual(self.registry.coverage([])["registered_not_provided"], ["export"])

    def test_content_wrong_proposal_and_wrong_repair_never_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.json"
            source = {"rows": [{"item": "Folders", "quantity": 4}]}

            @self.registry.tool(content=ContentPolicy(source, exact=("/rows",)), unchecked=("summary",))
            def export(rows, summary="Ready"):
                self.calls.append(rows)
                path.write_text(json.dumps(rows), encoding="utf-8")
                return {"written": True}

            bound = self.registry.bind(self.guard.turn())
            result = bound.execute_with_recovery("export", {"rows": [{"item": "Changed", "quantity": 4}]},
                                                 repair=lambda request: {"rows": [{"item": "Folders", "quantity": 5}]})
            self.assertFalse(result["executed"])
            self.assertEqual(result["repair_attempts"], 1)
            self.assertFalse(path.exists())
            good = bound.execute_with_recovery("export", {"rows": [{"item": "Changed", "quantity": 4}]},
                                               repair=lambda request: source)
            self.assertTrue(good["executed"])
            self.assertEqual(good["repair_attempts"], 1)
            self.assertEqual(json.loads(path.read_text()), source["rows"])
            self.assertEqual(self.calls, [source["rows"]])

    def test_missing_checked_argument_can_repair_but_missing_unchecked_cannot(self):
        self.guard.remember("expected", "JSON")
        self.registry.register(lambda format, note: [format, note], name="export",
                               bindings={"format": "expected"}, unchecked=("note",))
        bound = self.registry.bind(self.guard.turn())
        result = bound.execute_with_recovery("export", {"note": "supplied"},
                                             repair=lambda request: {"format": "JSON", "note": "supplied"})
        self.assertTrue(result["executed"])
        self.assertEqual(result["result"], ["JSON", "supplied"])
        with self.assertRaisesRegex(TypeError, "Missing required argument 'note'"):
            self.registry.bind(self.guard.turn()).execute_with_recovery("export", {"format": "CSV"}, repair=lambda _: {})

    def test_repaired_required_unchecked_omission_is_pre_execution_failure(self):
        self.guard.remember("expected", 1)
        self.registry.register(lambda value, note: self.calls.append([value, note]), name="write",
                               bindings={"value": "expected"}, unchecked=("note",))
        result = self.registry.bind(self.guard.turn()).execute_with_recovery(
            "write", {"value": 2, "note": "keep"}, repair=lambda request: {"value": 1})
        self.assertEqual(result["status"], "repair_failed")
        self.assertFalse(result["executed"])
        self.assertEqual(self.calls, [])

    def test_unknown_tool_or_argument_cannot_call_executor(self):
        self.registry.register(lambda value: self.calls.append(value), name="write", unchecked=("value",))
        bound = self.registry.bind(self.guard.turn())
        for name, arguments, error in (("other", {"value": 1}, KeyError),
                                        ("write", {"value": 1, "extra": 2}, TypeError),
                                        ("write", [], TypeError)):
            with self.subTest(name=name, arguments=arguments), self.assertRaises(error):
                bound.dispatch(name, arguments)
        self.assertEqual(self.calls, [])

    def test_wrong_guard_even_same_persistent_session_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.sqlite3"
            with ContextGuard(path) as owner, ContextGuard(path) as other_session:
                registry = ToolRegistry(owner)
                registry.register(lambda: None, name="work")
                with ContextGuard(path, session_id=owner.session_id) as other_guard:
                    for turn in (other_guard.turn(), other_session.turn(), object()):
                        with self.subTest(turn=type(turn)), self.assertRaises(ValueError):
                            registry.bind(turn)

    def test_state_change_does_not_refresh_bound_turn(self):
        self.guard.remember("destination", "old.json")
        self.registry.register(lambda destination: self.calls.append(destination), name="write",
                               bindings={"destination": "destination"})
        bound = self.registry.bind(self.guard.turn())
        revision = bound.revision
        self.guard.remember("destination", "new.json")
        with self.assertRaises(ActionRejected):
            bound.dispatch("write", {"destination": "new.json"})
        result = bound.execute_with_recovery("write", {"destination": "old.json"},
                                             repair=lambda request: self.fail("stale proposal cannot request repair"))
        self.assertFalse(result["executed"])
        self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual(bound.revision, revision)
        self.assertEqual(self.calls, [])

    def test_registry_inventory_is_snapshotted_and_policy_drift_rejected(self):
        self.registry.register(lambda: "first", name="first")
        bound = self.registry.bind(self.guard.turn())
        self.registry.register(lambda: "second", name="second")
        self.assertEqual(bound.names, ("first",))
        with self.assertRaises(KeyError):
            bound.dispatch("second", {})
        with self.assertRaises(ActionRejected):
            bound.dispatch("first", {})
        self.guard.allow_tool("first", when={"new_permission": True})
        with self.assertRaisesRegex(ValueError, "changed outside"):
            self.registry.bind(self.guard.turn())

    def test_duplicate_registration_never_replaces_existing_policy(self):
        self.guard.allow_tool("existing", when={"approved": True})
        revision = self.guard.turn().revision
        with self.assertRaisesRegex(ValueError, "already has a policy"):
            self.registry.register(lambda: None, name="existing")
        self.assertEqual(self.guard.turn().revision, revision)
        self.registry.register(lambda: None, name="new")
        revision = self.guard.turn().revision
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.registry.register(lambda: None, name="new")
        self.assertEqual(self.guard.turn().revision, revision)

    def test_explicit_source_replacement_invalidates_old_bound_turn(self):
        def write(rows):
            self.calls.append(rows)
            return rows

        old = {"rows": [{"name": "Original", "quantity": 4}]}
        new = {"rows": [{"name": "Updated", "quantity": 5}]}
        self.registry.register(write, content=ContentPolicy(old, exact=("/rows",)))
        bound = self.registry.bind(self.guard.turn())
        self.registry.register(write, content=ContentPolicy(new, exact=("/rows",)), replace=True)
        self.assertGreater(self.guard.turn().revision, bound.revision)
        for arguments in (old, new):
            with self.subTest(arguments=arguments), self.assertRaises(ActionRejected):
                bound.dispatch("write", arguments)
        current = self.registry.bind(self.guard.turn())
        with self.assertRaises(ActionRejected):
            current.dispatch("write", old)
        self.assertEqual(current.dispatch("write", new), new["rows"])
        self.assertEqual(self.calls, [new["rows"]])

    def test_decorated_function_can_refresh_source_without_installing_placeholder(self):
        @self.registry.tool(content=ContentPolicy({"value": 1}, exact=("/value",)))
        def write(value):
            self.calls.append(value)
            return value

        self.registry.register(write, content=ContentPolicy({"value": 2}, exact=("/value",)), replace=True)
        self.assertEqual(self.registry.bind(self.guard.turn())["write"](2), 2)
        self.assertEqual(self.calls, [2])
        with self.assertRaises(ValueError):
            self.registry.register(write, name="different", unchecked=("value",), replace=True)
        other = ToolRegistry(self.guard)
        with self.assertRaises(ValueError):
            other.register(write, unchecked=("value",), replace=True)

    def test_replacement_cannot_claim_unowned_or_changed_policy(self):
        self.guard.allow_tool("other", when={"approved": True})
        revision = self.guard.turn().revision
        for name in ("other", "missing"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "owned"):
                self.registry.register(lambda: None, name=name, replace=True)
        self.assertEqual(self.guard.turn().revision, revision)
        self.registry.register(lambda: None, name="owned")
        self.guard.allow_tool("owned", when={"approved": True})
        revision = self.guard.turn().revision
        with self.assertRaisesRegex(ValueError, "changed outside"):
            self.registry.register(lambda: None, name="owned", replace=True)
        self.assertEqual(self.guard.turn().revision, revision)

    def test_invalid_declarations_are_atomic(self):
        def work(value):
            return value

        configurations = (
            {"unchecked": "value"},
            {"unchecked": ("value", "value")},
            {"unchecked": ("value",), "bindings": {"value": "fact"}},
            {"memory": 1, "unchecked": ("value",)},
            {"bindings": ["value"]},
            {"bindings": {"value": "fact"}, "memory_bindings": {"value": "memory"}},
            {"unchecked": ("value",), "when": ["approved"]},
            {"unchecked": ("value",), "title": ""},
            {"unchecked": ("value",), "content": {}},
            {"unchecked": ("value",), "replace": "yes"},
        )
        revision = self.guard.turn().revision
        for options in configurations:
            with self.subTest(options=options), self.assertRaises((TypeError, ValueError)):
                self.registry.register(work, **options)
        self.assertEqual(self.guard.turn().revision, revision)
        self.assertEqual(self.guard.store.policies(self.guard.session_id), [])
        self.assertEqual(self.registry.coverage()["registered_tools"], [])

    def test_variadic_async_generator_and_non_json_default_rejected(self):
        async def async_tool():
            return 1

        def generator_tool():
            yield 1

        def kwargs_tool(**options):
            return options

        def args_tool(*values):
            return list(values)

        def object_default(value=object()):
            return value

        for function in (async_tool, generator_tool, kwargs_tool, args_tool, object_default):
            with self.subTest(function=function.__name__), self.assertRaises((TypeError, ValueError)):
                self.registry.register(function, unchecked=("value",) if function is object_default else ())
        self.assertEqual(self.guard.store.policies(self.guard.session_id), [])

    def test_executor_failure_is_never_retried(self):
        @self.registry.tool()
        def write():
            self.calls.append("side effect started")
            raise RuntimeError("failure after side effect")

        with self.assertRaises(ToolExecutionError):
            self.registry.bind(self.guard.turn()).execute_with_recovery(
                "write", {}, repair=lambda request: self.fail("must not retry executor failure"))
        self.assertEqual(self.calls, ["side effect started"])

    def test_connected_agent_uses_its_own_prepared_turn(self):
        def provider(*, messages):
            return {"tool": "export", "arguments": {"format": "json", "language": "hindi"}}

        with connect(provider, memory=MemoryPolicy()) as agent:
            registry = ToolRegistry(agent)
            registry.register(lambda format, language: [format, language], name="export", memory=True)
            result = agent.invoke(messages=[{"role": "user", "content": "Export as JSON. Reply in Hindi."}])
            proposal = result.response
            self.assertEqual(registry.bind(result.prepared).dispatch(proposal["tool"], proposal["arguments"]),
                             ["JSON", "Hindi"])

    def test_async_connected_agent_can_dispatch_sync_tools_after_await(self):
        async def provider(*, messages):
            return {"tool": "status", "arguments": {}}

        async def run():
            async with connect(provider) as agent:
                registry = ToolRegistry(agent)
                registry.register(lambda: "ready", name="status")
                result = await agent.invoke(messages=[{"role": "user", "content": "Status?"}])
                return registry.bind(result.prepared).dispatch("status", {})

        self.assertEqual(asyncio.run(run()), "ready")

    def test_known_repair_fills_checked_value_and_preserves_default(self):
        self.guard.remember("expected", "JSON")
        self.registry.register(lambda format, note="unchanged": [format, note], name="export",
                               bindings={"format": "expected"}, unchecked=("note",))
        result = self.registry.bind(self.guard.turn()).execute_with_recovery("export", {}, repair="known")
        self.assertTrue(result["executed"])
        self.assertEqual(result["result"], ["JSON", "unchanged"])
        self.assertEqual(result["repair_callback_calls"], 0)
        self.assertEqual(result["repair_attempts"], 1)

    def test_root_known_repair_cannot_add_default_after_content_check(self):
        self.registry.register(lambda format, note="default": self.calls.append([format, note]),
                               name="export", content=ContentPolicy({"format": "JSON"}, exact=("",)),
                               unchecked=("note",))
        with self.assertRaises(ToolExecutionError) as caught:
            self.registry.bind(self.guard.turn()).execute_with_recovery(
                "export", {"format": "CSV"}, repair="known")
        self.assertEqual(caught.exception.receipt["arguments"], {"format": "JSON"})
        self.assertEqual(self.calls, [])

    def test_concurrent_registration_and_executor_coverage_do_not_deadlock(self):
        # A subprocess bounds a possible deadlock without leaving stuck test
        # workers or locked SQLite stores in the main suite process.
        import context_relay
        import_root = str(Path(context_relay.__file__).resolve().parents[1])
        script = "import sys; sys.path.insert(0, " + repr(import_root) + ");\n" + textwrap.dedent("""
            import threading
            from context_relay import ContextGuard, ToolRegistry
            guard = ContextGuard()
            registry = ToolRegistry(guard)
            entered = threading.Event()
            registration_waiting = threading.Event()
            outcomes, errors = [], []
            lock = guard.store._lock

            class ObservedStoreLock:
                def acquire(self, *args, **kwargs):
                    if threading.current_thread().name == "registration":
                        registration_waiting.set()
                    return lock.acquire(*args, **kwargs)

                def release(self):
                    return lock.release()

                def __enter__(self):
                    self.acquire()
                    return self

                def __exit__(self, *args):
                    self.release()

            guard.store._lock = ObservedStoreLock()

            def work():
                entered.set()
                assert registration_waiting.wait(3), "registration did not attempt store lock"
                assert registry.coverage()["registered_tools"] == ["work"]
                return "complete"

            registry.register(work)
            bound = registry.bind(guard.turn())

            def run(function):
                try:
                    outcomes.append(function())
                except BaseException as exc:
                    errors.append(repr(exc))

            writer = threading.Thread(target=lambda: run(lambda: bound.dispatch("work", {})), daemon=True)
            registration = threading.Thread(name="registration", daemon=True,
                target=lambda: run(lambda: registry.register(lambda: None, name="second")))
            writer.start()
            assert entered.wait(3), "tool did not enter"
            registration.start()
            writer.join(4)
            registration.join(4)
            assert not writer.is_alive() and not registration.is_alive(), "registry/store lock deadlock"
            assert not errors, errors
            assert len(outcomes) == 2 and "complete" in outcomes
            guard.close()
            print("registry-store-lock-order-ok")
        """)
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=12)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("registry-store-lock-order-ok", completed.stdout)

    def test_bad_owner_and_inventory_are_rejected(self):
        with self.assertRaises(TypeError):
            ToolRegistry(object())
        with connect(lambda messages: None) as agent:
            pass
        with self.assertRaises(RuntimeError):
            ToolRegistry(agent)
        for inventory in ("export", ["export", "export"], [""]):
            with self.subTest(inventory=inventory), self.assertRaises((TypeError, ValueError)):
                self.registry.coverage(inventory)


if __name__ == "__main__":
    unittest.main()
