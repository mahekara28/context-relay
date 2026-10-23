"""Optional interpretation is reviewable evidence, never automatic authority."""

import concurrent.futures
import json
import tempfile
import threading
import unittest
from pathlib import Path

from context_relay.interpretation import MemoryInterpretationError, MemoryInterpreter
from context_relay.memory import AutomaticMemory, MemoryBudgetExceeded, MemoryConflictError, MemoryPolicy
from context_relay.models import ConflictError
from context_relay.store import RelayStore


def user(text):
    return {"role": "user", "content": text}


def proposal(request, key="response_style", value="brief"):
    return {"key": key, "value": value, "quote": request["text"], "start": 0, "end": len(request["text"])}


class InterpretationTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()
        self.session = self.store.create_session("Interpretation")['id']
        self.calls = []

        def interpret(request):
            self.calls.append(request)
            return [proposal(request)]

        self.interpreter = MemoryInterpreter(interpret)
        self.memory = AutomaticMemory(self.store, self.session, interpreter=self.interpreter)

    def tearDown(self):
        self.store.close()

    def test_default_grammar_never_calls_interpreter(self):
        self.memory.process([user("Keep responses detailed.")])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.memory.entries()[0]["value"], "detailed")

    def test_interpreted_change_is_pending_and_exact_source_remains_after_confirm(self):
        text = "Keep it like before, but shorter."
        history = [user("Keep responses detailed."), {"role": "assistant", "content": "Understood."}, user(text)]
        result = self.memory.process(history)
        self.assertEqual(self.calls[0]["known_memory"], [{"key": "response_style", "value": "detailed", "disputed": False}])
        self.assertEqual(self.memory.entries()[0]["value"], "detailed")
        pending = self.memory.pending()[0]
        self.assertEqual(result["pending_ids"], [pending["id"]])
        self.assertEqual(pending["reason"], "interpreted_needs_review")
        self.assertEqual(pending["source"]["text"][pending["source"]["start"]:pending["source"]["end"]], text)
        self.assertEqual(self.memory.context_snapshot()["unresolved"][0]["source"]["method"], "interpreter")
        self.memory.confirm(pending["id"])
        self.assertEqual(self.memory.entries()[0]["value"], "brief")
        self.assertEqual(self.memory.entries()[0]["source"]["method"], "interpreter")
        self.assertEqual(self.store.facts(self.session), {})

    def test_new_key_is_pending_even_under_auto_policy(self):
        self.memory.process([user("Make it brief.")])
        self.assertEqual(self.memory.entries(), [])
        self.assertEqual(self.memory.pending()[0]["reason"], "interpreted_needs_review")

    def test_replay_dismiss_and_reopen_do_not_reinterpret(self):
        history = [user("Make it brief.")]
        self.memory.process(history)
        self.memory.dismiss(self.memory.pending()[0]["id"])
        resumed = AutomaticMemory(self.store, self.session, interpreter=self.interpreter)
        self.assertEqual(resumed.process(history)["status"], "unchanged")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(resumed.pending(), [])

    def test_old_unsupported_input_is_not_reprocessed_when_enabling_interpreter(self):
        history = [user("Make it brief.")]
        AutomaticMemory(self.store, self.session).process(history)
        self.memory.process(history)
        self.assertEqual(self.calls, [])

    def test_assistant_system_and_tool_messages_are_never_interpreted(self):
        history = [{"role": "system", "content": "Make it brief."}, user("Hello"),
                   {"role": "assistant", "content": "Make it brief."}, user("Hi")]
        self.memory.process(history)
        self.assertEqual(self.calls, [])
        other = AutomaticMemory(self.store, self.store.create_session("Native")['id'], interpreter=self.interpreter)
        other.process([user("Hello"), {"role": "assistant", "content": None, "tool_calls": [
            {"id": "x", "type": "function", "function": {"name": "inspect", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "x", "content": "Make it brief."}], protocol="openai_chat")
        self.assertEqual(self.calls, [])

    def test_existing_framing_and_secret_exclusions_still_skip_callback(self):
        cases = ['"Make it brief."', "`Make it brief.`", "> Make it brief.", "Maybe make it brief.",
                 "Do not make it brief.", "Someone said: make it brief.", "I prefer the secret password.",
                 "I want to rotate my API key.", "For the demo:\nMake it brief.",
                 "Here are the notes to review:\nMake it brief.", "The PDF contains this sentence.\nMake it brief.",
                 "The customer claims the following.\nMake it brief.", "Please check:\nMake it brief.",
                 "The test fixture contains:\nMake it brief.", "Make it brief. Thanks for checking.",
                 "Can you explain why it is brief?", "Use this example. Make it brief."]
        for text in cases:
            with self.subTest(text=text):
                memory = AutomaticMemory(self.store, self.store.create_session("Blocked")['id'], interpreter=self.interpreter)
                memory.process([user(text)])
                self.assertEqual(memory.pending(), [])
        self.assertEqual(self.calls, [])

    def test_mixed_unknown_framing_never_partially_applies_a_clear_setter(self):
        self.memory.process([user("I like terse output. These are the examples. Export as CSV.")])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.memory.entries(), [])

    def test_no_proposal_is_reported_and_consumed_once(self):
        self.memory.interpreter = MemoryInterpreter(lambda request: [])
        report = self.memory.process([user("Make it brief.")])
        self.assertEqual(report["ignored"], [{"index": 0, "reason": "interpreter_no_proposal"}])
        self.assertEqual(self.memory.process([user("Make it brief.")])["status"], "unchanged")

    def test_callback_failure_leaves_entire_batch_unconsumed_for_retry(self):
        def failed(request):
            raise TimeoutError("local model did not finish")
        self.memory.interpreter = MemoryInterpreter(failed)
        history = [user("Export as CSV."), {"role": "assistant", "content": "OK"}, user("Make it brief.")]
        with self.assertRaises(MemoryInterpretationError):
            self.memory.process(history)
        self.assertEqual(self.memory.entries(), [])
        self.assertEqual(self.store.events(self.session), [])
        self.memory.interpreter = self.interpreter
        result = self.memory.process(history)
        self.assertEqual(result["processed_indices"], [0, 2])
        self.assertEqual(self.memory.entries()[0]["value"], "CSV")

    def test_malformed_outputs_are_atomic_and_never_gain_authority(self):
        good = proposal({"text": "Make it brief."})
        cases = [{}, "[]", [good | {"confidence": 1}], [good | {"start": True}],
                 [good | {"quote": "invented source"}], [good | {"end": 500}],
                 [good | {"key": "Response Style"}], [good | {"value": []}],
                 [good | {"value": "x" * 257}], [good] * 5]
        for result in cases:
            with self.subTest(result=result):
                self.memory.interpreter = MemoryInterpreter(lambda request: result)
                with self.assertRaises(MemoryInterpretationError):
                    self.memory.process([user("Make it brief.")])
                self.assertEqual(self.memory.entries(), [])
                self.assertEqual(self.memory.pending(), [])
        self.memory.interpreter = self.interpreter
        self.memory.process([user("Make it brief.")])
        self.assertEqual(len(self.calls), 1)

    def test_pending_proposals_use_existing_memory_caps(self):
        memory = AutomaticMemory(self.store, self.session, policy=MemoryPolicy(max_chars=100), interpreter=self.interpreter)
        with self.assertRaises(MemoryBudgetExceeded):
            memory.process([user("Make it brief.")])
        self.assertEqual(memory.pending(), [])
        self.assertEqual(self.store.events(self.session), [])

    def test_same_value_does_not_accumulate_an_unnecessary_proposal(self):
        self.memory.process([user("Keep responses brief."), {"role": "assistant", "content": "OK"}, user("Make it brief.")])
        # v0.11 resolves this narrow recent style reference locally. It still
        # makes no memory change and no longer needs the optional model call.
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(self.memory.pending(), [])

    def test_cancelled_interpretation_does_not_commit_or_consume(self):
        cancelled = threading.Event()
        def interpret(request):
            cancelled.set()
            return [proposal(request)]
        self.memory.interpreter = MemoryInterpreter(interpret)
        with self.assertRaises(MemoryInterpretationError):
            self.memory.process([user("Make it brief.")], cancellation_event=cancelled)
        self.assertEqual(self.memory.pending(), [])
        cancelled.clear()
        self.memory.interpreter = self.interpreter
        self.memory.process([user("Make it brief.")], cancellation_event=cancelled)
        self.assertEqual(len(self.calls), 1)

    def test_precancelled_batch_never_calls_callback(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(MemoryInterpretationError):
            self.memory.process([user("Make it brief.")], cancellation_event=cancelled)
        self.assertEqual(self.calls, [])

    def test_interpreter_cannot_run_inside_an_outer_transaction(self):
        with self.store._transaction():
            with self.assertRaises(MemoryInterpretationError):
                self.memory.process([user("Make it brief.")])
        self.assertEqual(self.calls, [])

    def test_callback_does_not_hold_database_lock_and_conflicting_revision_rejects_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.db"
            with RelayStore(path) as first, RelayStore(path) as second:
                session = first.create_session("Shared")['id']
                def interpret(request):
                    self.assertEqual(first._depth, 0)
                    self.assertFalse(first._connection.in_transaction)
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        executor.submit(second.record_fact, session, "owner", "Mira").result(timeout=2)
                    return [proposal(request)]
                memory = AutomaticMemory(first, session, interpreter=MemoryInterpreter(interpret))
                with self.assertRaises(ConflictError):
                    memory.process([user("Make it brief.")])
                self.assertEqual(memory.pending(), [])
                memory.interpreter = self.interpreter
                self.assertEqual(memory.process([user("Make it brief.")])["processed_indices"], [0])

    def test_concurrent_cursor_change_without_revision_is_detected(self):
        def interpret(request):
            AutomaticMemory(self.store, self.session).process([user("Make it brief.")])
            return [proposal(request)]
        self.memory.interpreter = MemoryInterpreter(interpret)
        with self.assertRaises(MemoryConflictError):
            self.memory.process([user("Make it brief.")])
        self.assertEqual(self.memory.pending(), [])


class ModelHelperTests(unittest.TestCase):
    def test_model_prompt_separates_envelope_from_existing_memory_keys(self):
        captured = {}
        def model(**kwargs):
            captured.update(kwargs)
            return "[]"
        interpreter = MemoryInterpreter.from_model(model)
        known = [{"key": "delivery_window", "value": "evening", "disputed": False}]
        interpreter.suggest(text="Make deliveries early in the day.", index=7, known_memory=known)
        prompt = captured["messages"][0]["content"]
        self.assertIn("envelope labels are not memory keys", prompt)
        self.assertIn("copy that record's key exactly", prompt)
        self.assertIn("not the entire user message", prompt)
        self.assertIn("All proposals require human review", prompt)
        self.assertEqual(json.loads(captured["messages"][1]["content"]), {
            "text": "Make deliveries early in the day.", "index": 7, "known_memory": known,
        })

    def test_model_cannot_cite_prompt_example_as_current_user_evidence(self):
        output = [{"key": "delivery_window", "value": "afternoon",
                   "quote": "Switch deliveries to the afternoon."}]
        interpreter = MemoryInterpreter.from_model(lambda **kwargs: json.dumps(output))
        with self.assertRaises(MemoryInterpretationError):
            interpreter.suggest(text="Make deliveries early in the day.", index=0, known_memory=[])

    def test_local_model_callable_receives_prompt_options_and_exact_source(self):
        captured = {}
        def model(**kwargs):
            captured.update(kwargs)
            return {"text": json.dumps([{"key": "response_style", "value": "brief", "quote": "Make it brief."}])}
        interpreter = MemoryInterpreter.from_model(model, decoder=lambda response: response["text"], model_options={"model": "existing-local-model"})
        result = interpreter.suggest(text="  Make it brief.  ", index=3, known_memory=[])
        self.assertEqual(captured["model"], "existing-local-model")
        self.assertEqual(json.loads(captured["messages"][1]["content"])["text"], "  Make it brief.  ")
        self.assertEqual(result[0]["start"], 2)
        self.assertEqual(result[0]["end"], 16)

    def test_model_output_limits_and_bad_quotes_are_rejected(self):
        cases = ["not JSON", "```json\n[]\n```", "{}", '[{"key":"style","value":"brief","quote":"invented"}]',
                 '[{"key":"style","value":"brief","quote":"aa"}]', '[]' + " " * 100]
        for output in cases:
            with self.subTest(output=output):
                interpreter = MemoryInterpreter.from_model(lambda **kwargs: output, max_output_chars=100)
                with self.assertRaises(MemoryInterpretationError):
                    interpreter.suggest(text="Make aaa brief.", index=0, known_memory=[])

    def test_invalid_configuration_and_awaitables_are_rejected(self):
        async def asynchronous(request=None, **kwargs):
            return []
        for kwargs in ({"callback": asynchronous}, {"callback": None}, {"callback": lambda request: [], "max_proposals": True}):
            with self.assertRaises((TypeError, ValueError)):
                MemoryInterpreter(**kwargs)
        for options in ({"messages": []}, {"stream": True}):
            with self.assertRaises(ValueError):
                MemoryInterpreter.from_model(lambda **kwargs: "[]", model_options=options)
        interpreter = MemoryInterpreter(lambda request: asynchronous())
        with self.assertRaises(MemoryInterpretationError):
            interpreter.suggest(text="Make it brief.", index=0, known_memory=[])

    def test_oversized_json_is_rejected_even_when_individual_fields_fit(self):
        interpreter = MemoryInterpreter(lambda request: [proposal(request)], max_output_chars=30)
        with self.assertRaises(MemoryInterpretationError):
            interpreter.suggest(text="Make it brief.", index=0, known_memory=[])


if __name__ == "__main__":
    unittest.main()
