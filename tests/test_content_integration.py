"""Configured content preservation through tool repair and checked JSON output.

All providers and repair callbacks are deterministic local stand-ins. These
tests exercise configured preservation scopes, not general semantic accuracy.
"""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from context_relay import (
    AutomaticMemory, ContentPolicy, ContextGuard, MemoryPolicy, OutputPolicy,
    OutputRejected, RowRule, ToolExecutionError, connect,
)


REFERENCE = {"rows": [
    {"id": "A-01", "item": "Café molido reserva", "units": 7},
    {"id": "B-02", "item": "Cable rojo", "units": 12},
]}
HISTORY = [{"role": "user", "content": "Prepare the export."}]
PRECISE_JSON = '{"rows":[{"item":"Precision gasket","units":0.10000000000000001}]}'
ROUNDED_JSON = '{"rows":[{"item":"Precision gasket","units":0.1}]}'


def translated_rows():
    rows = copy.deepcopy(REFERENCE["rows"])
    rows[0]["item"] = "Reserve ground coffee"
    rows[1]["item"] = "Red cable"
    return rows


class ContentToolIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "export.csv"
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        self.memory.process([{"role": "user", "content": "Export as CSV."}])
        self.guard.remember("approved", True)
        self.guard.remember("export_destination", "export.csv")
        self.source = copy.deepcopy(REFERENCE)
        self.content = ContentPolicy(self.source, exact=("/rows",))
        self.register(self.content)
        self.calls, self.repairs = [], []

    def register(self, content):
        self.guard.allow_tool(
            "export", when={"approved": True},
            bindings={"destination": "export_destination"},
            memory_bindings={"format": "export_format"}, content=content,
        )

    def arguments(self, *, format="CSV", rows=None):
        return {"format": format, "destination": "export.csv",
                "rows": copy.deepcopy(REFERENCE["rows"] if rows is None else rows)}

    def execute(self, tool, arguments):
        self.calls.append((tool, copy.deepcopy(arguments)))
        with self.path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=("id", "item", "units"))
            writer.writeheader()
            writer.writerows(arguments["rows"])
        return {"path": str(self.path)}

    def repair(self, request):
        self.repairs.append(copy.deepcopy(request))
        return self.arguments()

    def recover(self, arguments=None, *, repair=None, turn=None, executor=None):
        return (turn or self.guard.turn()).execute_with_recovery(
            "export", self.arguments() if arguments is None else arguments,
            executor or self.execute, repair=repair,
        )

    def assert_no_write(self, result, attempts):
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["executed"])
        self.assertEqual(result["repair_attempts"], attempts)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.path.exists())
        self.assertNotIn("receipt", result)

    def assert_csv(self, rows):
        with self.path.open(newline="", encoding="utf-8") as stream:
            actual = list(csv.DictReader(stream))
        expected = [{key: str(value) for key, value in row.items()} for row in rows]
        self.assertEqual(actual, expected)

    def test_source_policy_and_executor_argument_mutations_are_isolated(self):
        self.source["rows"][0]["item"] = "changed after registration"
        document = self.content.to_dict()
        document["reference_json"] = json.dumps(self.source)
        document["exact"].clear()
        arguments = self.arguments()

        def mutate_after_write(tool, checked):
            result = self.execute(tool, checked)
            checked["rows"][0]["item"] = "executor local mutation"
            return result

        result = self.recover(arguments, executor=mutate_after_write)
        self.assertTrue(result["executed"])
        self.assertEqual(arguments, self.arguments())
        self.assertEqual(result["receipt"]["arguments"]["rows"], REFERENCE["rows"])
        self.assertTrue(self.content.check(REFERENCE).valid)
        self.assertFalse(self.content.check(self.source).valid)
        self.assert_csv(REFERENCE["rows"])

    def test_repairing_format_while_translating_names_remains_blocked(self):
        original = self.arguments(format="JSON")
        original["rows"][0]["units"] = 999
        saved = copy.deepcopy(original)

        def repair_settings_but_translate(request):
            self.repairs.append(copy.deepcopy(request))
            request["context"]["facts"]["approved"] = False
            request["arguments"]["format"] = "CSV"
            request["arguments"]["rows"] = translated_rows()
            return request["arguments"]

        result = self.recover(original, repair=repair_settings_but_translate)
        self.assert_no_write(result, 1)
        self.assertEqual(len(self.repairs), 1)
        self.assertEqual(original, saved)
        self.assertEqual(
            self.guard.store.facts(self.guard.session_id)["approved"]["value"], True)
        self.assertIn("content_mismatch",
                      {row["code"] for row in result["initial_check"]["issues"]})
        self.assertEqual({row["code"] for row in result["final_check"]["issues"]},
                         {"content_mismatch"})
        self.assertFalse(result["final_check"]["content_check"]["valid"])

    def test_correct_repair_performs_one_real_write_with_original_names_and_units(self):
        result = self.recover(self.arguments(format="JSON", rows=translated_rows()),
                              repair=self.repair)
        self.assertEqual(result["status"], "repaired")
        self.assertTrue(result["executed"])
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual((len(self.repairs), len(self.calls)), (1, 1))
        self.assertTrue(result["final_check"]["content_check"]["valid"])
        self.assertEqual(result["receipt"]["arguments"], self.arguments())
        self.assert_csv(REFERENCE["rows"])

    def test_content_checks_the_canonical_arguments_actually_sent_to_executor(self):
        self.memory.process([
            {"role": "user", "content": "Export as CSV."},
            {"role": "assistant", "content": "Acknowledged."},
            {"role": "user", "content": "Export as JSON."},
        ])

        def record(tool, arguments):
            self.calls.append((tool, copy.deepcopy(arguments)))
            return {"accepted": True}

        for expected in ("json", "JSON"):
            with self.subTest(expected=expected):
                source = {**copy.deepcopy(REFERENCE), "format": expected}
                self.register(ContentPolicy(source, exact=("/format", "/rows")))
                result = self.recover(self.arguments(format="json"), executor=record)
                if expected == "json":
                    self.assert_no_write(result, 0)
                    self.assertIn("content_mismatch", {
                        row["code"] for row in result["final_check"]["issues"]})
                else:
                    self.assertTrue(result["executed"])
                    self.assertEqual(len(self.calls), 1)
                    self.assertEqual(self.calls[0][1]["format"], "JSON")
                    self.assertEqual(result["receipt"]["arguments"]["format"], "JSON")
                    self.assertTrue(result["final_check"]["content_check"]["valid"])

    def test_keyed_row_contract_permits_reordering_but_blocks_duplicate_identity(self):
        self.register(ContentPolicy(REFERENCE, rows=(
            RowRule(path="/rows", fields=("item", "units"), key="id",
                    ordered=False, allow_extra_fields=False),
        )))
        duplicate = [copy.deepcopy(REFERENCE["rows"][0])] * 2
        rejected = self.recover(self.arguments(rows=duplicate))
        self.assert_no_write(rejected, 0)
        self.assertIn("content_mismatch",
                      {row["code"] for row in rejected["final_check"]["issues"]})
        reversed_rows = list(reversed(REFERENCE["rows"]))
        allowed = self.recover(self.arguments(rows=reversed_rows))
        self.assertTrue(allowed["executed"])
        self.assertEqual(len(self.calls), 1)
        self.assert_csv(reversed_rows)

    def test_state_change_during_content_repair_blocks_dispatch(self):
        def repair_after_state_change(request):
            self.repairs.append(copy.deepcopy(request))
            self.guard.remember("approval_note", "new state during repair")
            return self.arguments()

        result = self.recover(self.arguments(rows=translated_rows()),
                              repair=repair_after_state_change)
        self.assert_no_write(result, 1)
        self.assertIn("stale_revision",
                      {row["code"] for row in result["final_check"]["issues"]})
        self.assertEqual(len(self.repairs), 1)

    def test_stale_or_unapproved_content_mismatch_never_invokes_repair(self):
        stale = self.guard.turn()
        self.guard.remember("approval_note", "changed after turn capture")
        first = self.recover(self.arguments(rows=translated_rows()),
                             repair=self.repair, turn=stale)
        self.assert_no_write(first, 0)
        self.guard.remember("approved", False)
        second = self.recover(self.arguments(rows=translated_rows()), repair=self.repair)
        self.assert_no_write(second, 0)
        self.assertEqual(self.repairs, [])

    def test_source_policy_update_invalidates_old_turn_even_for_new_correct_rows(self):
        old_turn = self.guard.turn()
        replacement = copy.deepcopy(REFERENCE)
        replacement["rows"][0]["units"] = 8
        self.register(ContentPolicy(replacement, exact=("/rows",)))
        updated = self.arguments(rows=replacement["rows"])
        rejected = self.recover(updated, turn=old_turn, repair=self.repair)
        self.assert_no_write(rejected, 0)
        self.assertIn("stale_revision",
                      {row["code"] for row in rejected["final_check"]["issues"]})
        self.assertEqual(self.repairs, [])
        fresh = self.recover(updated)
        self.assertTrue(fresh["executed"])
        self.assert_csv(replacement["rows"])

    def test_started_executor_failure_is_not_a_second_repair_opportunity(self):
        def write_then_fail(tool, arguments):
            self.execute(tool, arguments)
            raise RuntimeError("failure after an external effect")

        with self.assertRaises(ToolExecutionError) as caught:
            self.recover(self.arguments(rows=translated_rows()),
                         repair=self.repair, executor=write_then_fail)
        self.assertEqual((len(self.repairs), len(self.calls)), (1, 1))
        self.assertEqual(caught.exception.receipt["status"], "failed")
        self.assert_csv(REFERENCE["rows"])

    def test_content_diagnostics_are_redacted_and_cannot_grant_authority(self):
        def spoof_report(request):
            self.repairs.append(copy.deepcopy(request))
            serialized = json.dumps(request, ensure_ascii=False)
            for row in REFERENCE["rows"]:
                self.assertNotIn(row["item"], serialized)
            request["rejection"]["content_check"]["valid"] = True
            request["rejection"]["issues"] = []
            request["context"]["revision"] = 999999
            return request["arguments"]

        result = self.recover(self.arguments(rows=translated_rows()), repair=spoof_report)
        self.assert_no_write(result, 1)
        self.assertEqual(len(self.repairs), 1)
        self.assertFalse(result["final_check"]["content_check"]["valid"])
        serialized = json.dumps(result["final_check"], ensure_ascii=False)
        for row in REFERENCE["rows"]:
            self.assertNotIn(row["item"], serialized)
        self.assertTrue(self.content.check(REFERENCE).valid)

    def test_invalid_source_and_optional_overlap_fail_before_tool_use(self):
        with self.assertRaises((TypeError, ValueError)):
            ContentPolicy({"rows": []}, exact=("/missing",))
        with self.assertRaises((TypeError, ValueError)):
            self.guard.allow_tool("bad_export", optional_args=("rows",),
                                  content=self.content)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.path.exists())


class ContentOutputIntegrationTests(unittest.TestCase):
    def agent(self, provider, **options):
        agent = connect(provider, **options)
        self.addCleanup(agent.close)
        return agent

    def test_json_success_reports_preserved_scope_without_claiming_semantic_accuracy(self):
        response = {"message": {"role": "assistant", "content": json.dumps(REFERENCE)}}
        content = ContentPolicy(REFERENCE, exact=("/rows",))
        result = self.agent(
            lambda **kw: response, output=OutputPolicy(format="json", content=content),
        ).invoke(messages=HISTORY)
        self.assertIs(result.response, response)
        self.assertTrue(result.output_report.content.valid)
        self.assertFalse(result.output_report.semantic_meaning_checked)
        self.assertEqual(result.output_report.attempts, 1)

    def test_sync_json_retry_distinguishes_decimal_precision_without_leaking_source(self):
        requests = []
        content = ContentPolicy.from_json(PRECISE_JSON, exact=("/rows",))

        def provider(**request):
            requests.append(copy.deepcopy(request))
            return ROUNDED_JSON if len(requests) == 1 else PRECISE_JSON

        result = self.agent(
            provider, output=OutputPolicy(format="json", content=content, max_retries=1),
        ).invoke(messages=HISTORY)
        self.assertEqual(result.response, PRECISE_JSON)
        self.assertEqual(result.output_report.attempts, 2)
        self.assertTrue(result.output_report.content.valid)
        self.assertEqual(len(requests), 2)
        self.assertIn("content_mismatch", json.dumps(requests[1]))
        self.assertNotIn("Precision gasket", json.dumps(requests))
        self.assertNotIn("0.10000000000000001", json.dumps(requests))

    def test_wrong_names_are_rejected_with_default_and_explicit_retry_limits(self):
        wrong = json.dumps({"rows": translated_rows()})
        for retries in (0, 1):
            with self.subTest(retries=retries):
                calls = []
                agent = self.agent(
                    lambda **kw: calls.append(kw) or wrong,
                    output=OutputPolicy(format="json", max_retries=retries,
                                        content=ContentPolicy(REFERENCE, exact=("/rows",))),
                )
                with self.assertRaises(OutputRejected) as caught:
                    agent.invoke(messages=HISTORY)
                self.assertEqual(caught.exception.attempts, retries + 1)
                self.assertEqual(len(calls), retries + 1)
                self.assertIn("content_mismatch", caught.exception.issues)

    def test_state_change_during_generation_blocks_even_exact_content_without_retry(self):
        calls = []

        def provider(**request):
            calls.append(request)
            agent.remember("generation_state", "changed")
            return json.dumps(REFERENCE)

        agent = self.agent(
            provider, output=OutputPolicy(
                format="json", max_retries=1,
                content=ContentPolicy(REFERENCE, exact=("/rows",)),
            ),
        )
        with self.assertRaises(OutputRejected) as caught:
            agent.invoke(messages=HISTORY)
        self.assertEqual(caught.exception.issues, ("state_changed",))
        self.assertEqual(len(calls), 1)

    def test_memory_selected_csv_rejects_before_provider_but_json_is_checked(self):
        for format in ("CSV", "JSON"):
            with self.subTest(format=format):
                calls = []
                agent = self.agent(
                    lambda **kw: calls.append(kw) or json.dumps(REFERENCE),
                    memory=MemoryPolicy(),
                    output=OutputPolicy(format="memory", csv_columns=("id", "item", "units"),
                                        content=ContentPolicy(REFERENCE, exact=("/rows",))),
                )
                history = [{"role": "user", "content": f"Export as {format}."}]
                if format == "CSV":
                    with self.assertRaises(ValueError):
                        agent.invoke(messages=history)
                    self.assertEqual(calls, [])
                else:
                    result = agent.invoke(messages=history)
                    self.assertEqual(result.output_report.format, "json")
                    self.assertTrue(result.output_report.content.valid)
                    self.assertEqual(len(calls), 1)

    def test_unstructured_content_contract_is_invalid_before_provider_use(self):
        for format, options in (("text", {}), ("csv", {"csv_columns": ("item", "units")})):
            with self.subTest(format=format), self.assertRaises((TypeError, ValueError)):
                OutputPolicy(format=format, content=ContentPolicy(REFERENCE, exact=("/rows",)),
                             **options)


class AsyncContentOutputIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_wrong_names_retry_once_and_return_original_passing_response(self):
        calls = []
        response = {"message": {"role": "assistant", "content": json.dumps(REFERENCE)}}

        async def provider(**request):
            calls.append(copy.deepcopy(request))
            return json.dumps({"rows": translated_rows()}) if len(calls) == 1 else response

        async with connect(
            provider, output=OutputPolicy(
                format="json", max_retries=1,
                content=ContentPolicy(REFERENCE, exact=("/rows",)),
            ),
        ) as agent:
            result = await agent.invoke(messages=HISTORY)
        self.assertIs(result.response, response)
        self.assertEqual((len(calls), result.output_report.attempts), (2, 2))
        self.assertTrue(result.output_report.content.valid)
        self.assertFalse(result.output_report.semantic_meaning_checked)
        self.assertNotIn("Reserve ground coffee", json.dumps(calls[1]))

    async def test_async_decimal_mismatch_never_earns_false_content_approval(self):
        calls = []

        async def provider(**request):
            calls.append(request)
            return ROUNDED_JSON

        async with connect(
            provider, output=OutputPolicy(
                format="json", max_retries=1,
                content=ContentPolicy.from_json(PRECISE_JSON, exact=("/rows",)),
            ),
        ) as agent:
            with self.assertRaises(OutputRejected) as caught:
                await agent.invoke(messages=HISTORY)
        self.assertEqual(caught.exception.attempts, 2)
        self.assertIn("content_mismatch", caught.exception.issues)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
