"""Bounded request composition, source fidelity and misleading near-misses."""

import unittest

from context_relay import AutomaticMemory, MemoryInterpreter, MemoryPolicy, RelayStore


def user(text):
    return {"role": "user", "content": text}


class ComposedRequestTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()
        self.addCleanup(self.store.close)

    def memory(self, **options):
        return AutomaticMemory(self.store, self.store.create_session("Composition")["id"], **options)

    def check_sources(self, memory, history):
        for record in memory.entries() + memory.pending():
            source = record["source"]
            self.assertEqual(source["quote"], history[source["index"]]["content"][source["start"]:source["end"]])
        self.assertEqual(self.store.facts(memory.session_id), {})

    def test_request_wrappers_compose_with_multiple_setting_domains(self):
        requests = (("keep your replies concise", {"response_style": "concise"}),
                    ("reply in Tamil", {"response_language": "Tamil"}),
                    ("export in YAML", {"export_format": "YAML"}),
                    ("set the deployment region to eu-west-2", {"deployment_region": "eu-west-2"}))
        wrappers = ("Could you {}?", "Please would you {}?", "I would like you to {}.",
                    "I’d like you to {}.", "Let's {}.", "On second thought, {}.",
                    "I changed my mind: {}.", "Scratch that—{}.")
        for request, expected in requests:
            for wrapper in wrappers:
                with self.subTest(wrapper=wrapper, request=request):
                    memory = self.memory()
                    history = [user(wrapper.format(request))]
                    memory.process(history)
                    self.assertEqual({e["key"]: e["value"] for e in memory.entries()}, expected)
                    self.check_sources(memory, history)

    def test_setting_grammar_generalizes_across_values_and_field_names(self):
        cases = (
            ("For future exports, make XML our default.", "export_format", "XML"),
            ("Make TOML the default for exports.", "export_format", "TOML"),
            ("Go with Markdown for the export.", "export_format", "Markdown"),
            ("Keep YAML for exports.", "export_format", "YAML"),
            ("Our export format needs to be TSV.", "export_format", "TSV"),
            ("The response language should be Marathi.", "response_language", "Marathi"),
            ("Response language = Japanese.", "response_language", "Japanese"),
            ("Make concise the default for response style.", "response_style", "concise"),
            ("Response style should be to-the-point.", "response_style", "concise"),
            ("The output format must be JSONL.", "output_format", "JSONL"),
            ("Use JSONL for the output.", "export_format", "JSONL"),
            ("I'd prefer brief replies.", "response_style", "brief"),
        )
        for text, key, value in cases:
            with self.subTest(text=text):
                memory = self.memory()
                history = [user(text)]
                memory.process(history)
                self.assertEqual({e["key"]: e["value"] for e in memory.entries()}, {key: value})
                self.check_sources(memory, history)

    def test_replacement_checks_exact_old_value_and_does_not_touch_equal_other_field(self):
        for old, pending in (("CSV", False), ("XML", True)):
            with self.subTest(old=old):
                memory = self.memory()
                history = [user("Export as CSV. Set the log format to CSV.")]
                memory.process(history)
                history += [user(f"Please replace {old} with YAML as the export format.")]
                memory.process(history)
                self.assertEqual({e["key"]: e["value"] for e in memory.entries()},
                                 {"export_format": "CSV" if pending else "YAML", "log_format": "CSV"})
                self.assertEqual(bool(memory.pending()), pending)
                if pending:
                    self.assertEqual(memory.pending()[0]["reason"], "old_value_mismatch")
                self.check_sources(memory, history)

    def test_named_contrast_only_accepts_explicit_new_and_old_values(self):
        for text in ("Use YAML for exports, not CSV.", "Choose YAML rather than CSV for exports.",
                     "Keep YAML for exports instead of CSV."):
            with self.subTest(text=text):
                memory = self.memory()
                history = [user("Export as CSV."), user(text)]
                memory.process(history)
                self.assertEqual(memory.entries()[0]["value"], "YAML")
                self.check_sources(memory, history)
        for text in ("Do not use YAML for exports.", "Use YAML for exports, not anything else.",
                     "Maybe use YAML for exports, not CSV."):
            with self.subTest(text=text):
                memory = self.memory()
                memory.process([user(text)])
                self.assertEqual(memory.entries(), [])

    def test_multiple_complete_clauses_are_atomic_with_exact_offsets(self):
        memory = self.memory()
        text = "  Keep YAML for exports; leave the log format as CSV and reply in Bengali.  "
        history = [user(text)]
        result = memory.process(history)
        self.assertEqual(result["ignored"], [])
        self.assertEqual({e["key"]: e["value"] for e in memory.entries()},
                         {"export_format": "YAML", "log_format": "CSV", "response_language": "Bengali"})
        self.assertEqual(len(self.store.events(memory.session_id)), 1)
        self.check_sources(memory, history)

    def test_compound_declarations_retain_the_existing_conflict_review_boundary(self):
        memory = self.memory()
        history = [user("The deployment region is us-east-1.")]
        memory.process(history)
        history += [user("The deployment region is eu-west-2 and the database is PostgreSQL.")]
        memory.process(history)
        self.assertEqual({e["key"]: e["value"] for e in memory.entries()},
                         {"deployment_region": "us-east-1", "database": "PostgreSQL"})
        self.assertEqual(memory.pending()[0]["reason"], "conflicting_declaration")

    def test_one_unsupported_clause_prevents_partial_updates(self):
        for text in ("Export in YAML and decide the rest later.",
                     "Reply in Tamil; maybe use XML for exports.",
                     "Keep replies concise and do not change exports.",
                     "Use YAML for exports; these are sample settings.",
                     "Would you export in YAML? Is that a good format?",
                     "Reply in Tamil and export as CSV or JSON."):
            with self.subTest(text=text):
                memory = self.memory()
                memory.process([user(text)])
                self.assertEqual(memory.entries(), [])
                self.assertEqual(memory.pending(), [])

    def test_misleading_framing_negation_and_local_scope_never_call_interpreter(self):
        cases = (
            "I'd like you to translate this: export in YAML.",
            "Let's imagine the response language should be Marathi.",
            "If we agree, make YAML the default for exports.",
            "Could you explain why exports should be JSON?",
            "Could you tell me whether the response language should be Hindi?",
            "Could you use YAML for exports only this time?",
            "I'd like you to reply in Hindi for this response.",
            "For the next request, export as XML.",
            "Could you set the export format to XML for now?",
            "Set the deployment region to eu-west-2 for today.",
            "Please set the deployment region to eu-west-2 this time.",
            "I wouldn't like brief answers.",
            "Let's not change the response language to Hindi.",
            "Please don't replace CSV with YAML as the export format.",
            "The customer wrote: Let's export as YAML.",
            "The document contains this request. Could you use JSON for exports?",
            '"Could you use YAML for exports?"',
            "'I'd like concise replies.'",
            "‘Let’s export as YAML.’",
            "`Let's export as YAML.`",
            "> Could you export as YAML?",
            "Could you rotate the secret password?",
            "I would like the API key set to abc123.",
            "Export as YAML and don't change anything.",
        )
        for text in cases:
            with self.subTest(text=text):
                calls = []
                memory = self.memory(interpreter=MemoryInterpreter(lambda request: calls.append(request) or []))
                memory.process([user(text)])
                self.assertEqual(memory.entries(), [])
                self.assertEqual(memory.pending(), [])
                self.assertEqual(calls, [])

    def test_contextual_polite_requests_still_require_review(self):
        for text in ("Could you make it short?", "Let's keep it short.", "Please would you make it short?"):
            with self.subTest(text=text):
                memory = self.memory()
                history = [user("Keep responses detailed."), user(text)]
                memory.process(history)
                self.assertEqual(memory.entries()[0]["value"], "detailed")
                self.assertEqual(memory.pending()[0]["reason"], "contextual_needs_review")
                self.check_sources(memory, history)

    def test_output_aliases_still_require_a_unique_target(self):
        memory = self.memory()
        history = [user("Export as CSV. Set the output format to XML."),
                   user("Could you use YAML for the output?")]
        memory.process(history)
        self.assertEqual({e["key"]: e["value"] for e in memory.entries()},
                         {"export_format": "CSV", "output_format": "XML"})
        self.assertEqual(memory.pending()[0]["reason"], "ambiguous_target")
        self.assertEqual(set(memory.pending()[0]["targets"]), {"export_format", "output_format"})

    def test_unknown_polite_requests_can_reach_optional_interpreter_without_granting_authority(self):
        for text in ("Could you make the tone a little warmer going forward?",
                     "I'd like the delivery window shifted into the afternoon.",
                     "Please replace the formal tone with something friendlier.",
                     "Could you keep the tone warm and informal?"):
            with self.subTest(text=text):
                calls = []
                def interpret(request):
                    calls.append(request)
                    return [{"key": "tone", "value": "warmer", "quote": request["text"],
                             "start": 0, "end": len(request["text"])}]
                memory = self.memory(interpreter=MemoryInterpreter(interpret))
                history = [user(text)]
                memory.process(history)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["text"], text)
                self.assertEqual(memory.entries(), [])
                self.assertEqual(memory.pending()[0]["reason"], "interpreted_needs_review")
                self.check_sources(memory, history)
                memory.process(history)
                self.assertEqual(len(calls), 1)

    def test_review_mode_still_gates_every_new_named_form(self):
        memory = self.memory(policy=MemoryPolicy(mode="review"))
        history = [user("I'd like brief replies and could you export in YAML?")]
        memory.process(history)
        self.assertEqual(memory.entries(), [])
        self.assertEqual(len(memory.pending()), 2)
        self.assertEqual({p["reason"] for p in memory.pending()}, {"review_mode"})
        self.check_sources(memory, history)


if __name__ == "__main__":
    unittest.main()
