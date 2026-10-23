"""Strict settings and a no-overwrite setup command for existing projects."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
from pathlib import Path

from context_relay.cli import main
from context_relay.config import RelayConfig


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(previous)


class RelayConfigTests(unittest.TestCase):
    def test_defaults_are_data_only_and_round_trip(self):
        config = RelayConfig()
        self.assertEqual(config.to_dict(), {
            "max_chars": 16000, "recent_turns": 2, "retrieve_turns": 2,
            "pinned": [], "db_path": ":memory:", "name": "Connected agent", "protocol": "plain",
            "memory": None, "output": None, "busy_timeout_ms": 10000,
        })
        self.assertEqual(RelayConfig(**json.loads(json.dumps(config.to_dict()))), config)

    def test_pins_are_frozen_without_reordering_the_callers_indices(self):
        source = [5, 2, 5]
        config = RelayConfig(pinned=source)
        source.append(9)
        self.assertEqual(config.pinned, (5, 2, 5))
        with self.assertRaises(FrozenInstanceError):
            config.max_chars = 20000
        exported = config.to_dict()
        exported["pinned"].append(12)
        self.assertEqual(config.pinned, (5, 2, 5))

    def test_numeric_limits_are_type_strict(self):
        for field in ("max_chars", "recent_turns", "retrieve_turns"):
            for value in (True, False, -1, 1.5, "2", None, float("inf"), float("nan")):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    RelayConfig(**{field: value})
        with self.assertRaises(ValueError):
            RelayConfig(recent_turns=0)
        config = RelayConfig(max_chars=0, recent_turns=1, retrieve_turns=0)
        self.assertEqual(config.max_chars, 0)

    def test_invalid_pin_collections_and_indices_are_rejected(self):
        for value in (None, "1", {1}, {"0": 1}, [True], [-1], [1.0], ["1"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RelayConfig(pinned=value)

    def test_paths_and_names_are_not_coerced(self):
        for value in (None, 1, True, "", " ", "bad\x00path", Path("data.db")):
            with self.subTest(db_path=value), self.assertRaises(ValueError):
                RelayConfig(db_path=value)
        for value in (None, 1, True, "", " ", "a" * 201):
            with self.subTest(name=value), self.assertRaises(ValueError):
                RelayConfig(name=value)
        self.assertEqual(RelayConfig(name="a" * 200).name, "a" * 200)

    def test_loading_resolves_relative_database_beside_configuration_without_io(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            elsewhere = root / "unrelated"
            elsewhere.mkdir()
            settings = project / "relay.json"
            settings.write_text('{"db_path":"data/agent.db","name":"工程 agent"}', encoding="utf-8")
            with working_directory(elsewhere):
                config = RelayConfig.from_file(settings)
            self.assertEqual(config.db_path, str((project / "data" / "agent.db").resolve()))
            self.assertEqual(config.name, "工程 agent")
            self.assertFalse((project / "data").exists())

    def test_loading_keeps_memory_and_absolute_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "relay.json"
            settings.write_text("{}", encoding="utf-8")
            self.assertEqual(RelayConfig.from_file(str(settings)), RelayConfig())
            absolute = str(Path(directory) / "no-database.db")
            settings.write_text(json.dumps({"db_path": absolute}), encoding="utf-8")
            self.assertEqual(RelayConfig.from_file(settings).db_path, absolute)
            self.assertFalse(Path(absolute).exists())

    def test_environment_and_home_shorthand_are_literal_path_components(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "relay.json"
            for value in ("$HOME/data.db", "%USERPROFILE%/data.db", "~/data.db"):
                with self.subTest(value=value):
                    settings.write_text(json.dumps({"db_path": value}), encoding="utf-8")
                    config = RelayConfig.from_file(settings)
                    self.assertEqual(config.db_path, str((settings.parent / value).resolve()))

    def test_unknown_secret_and_executable_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "relay.json"
            for field in ("max_tokens", "api_key", "model", "callback", "command", "session_id"):
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Unknown configuration"):
                    settings.write_text(json.dumps({field: "ignored?"}), encoding="utf-8")
                    RelayConfig.from_file(settings)

    def test_duplicate_keys_nonfinite_numbers_and_nonobjects_are_rejected(self):
        payloads = [
            '{"max_chars":1000,"max_chars":2000}',
            '{"name":"x","name":"y"}',
            '{"max_chars":NaN}', '{"max_chars":Infinity}', '{"max_chars":-Infinity}',
            "[]", "null", "true", '"string"', "1", "{bad JSON}",
        ]
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "relay.json"
            for payload in payloads:
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    settings.write_text(payload, encoding="utf-8")
                    RelayConfig.from_file(settings)

    def test_missing_and_invalid_utf8_files_fail_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "relay.json"
            with self.assertRaises(FileNotFoundError):
                RelayConfig.from_file(settings)
            settings.write_bytes(b"\xff\xfe")
            with self.assertRaises(UnicodeDecodeError):
                RelayConfig.from_file(settings)


class ConfigurationInitTests(unittest.TestCase):
    def run_init(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(["init", *args])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_default_init_creates_loadable_settings_in_current_project(self):
        with tempfile.TemporaryDirectory() as directory, working_directory(directory):
            result, stdout, stderr = self.run_init()
            self.assertEqual(result, 0)
            self.assertIn("Created", stdout)
            self.assertEqual(stderr, "")
            settings = Path("relay.json")
            self.assertTrue(settings.read_bytes().endswith(b"\n"))
            self.assertEqual(RelayConfig.from_file(settings), RelayConfig())

    def test_init_creates_requested_parent_directories_without_starting_services(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "new" / "nested" / "relay.json"
            result, _, stderr = self.run_init(str(settings))
            self.assertEqual(result, 0, stderr)
            self.assertEqual(RelayConfig.from_file(settings), RelayConfig())
            self.assertEqual(sorted(path.name for path in settings.parent.iterdir()), ["relay.json"])

    def test_existing_settings_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "relay.json"
            original = b'{"max_chars": 1234}\n'
            settings.write_bytes(original)
            result, stdout, stderr = self.run_init(str(settings))
            self.assertEqual(result, 1)
            self.assertEqual(stdout, "")
            self.assertIn("already exists", stderr)
            self.assertEqual(settings.read_bytes(), original)

    def test_unusable_parent_returns_a_clean_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "file"
            parent.write_text("keep me", encoding="utf-8")
            result, stdout, stderr = self.run_init(str(parent / "relay.json"))
            self.assertEqual(result, 1)
            self.assertEqual(stdout, "")
            self.assertIn("Could not create", stderr)
            self.assertEqual(parent.read_text(encoding="utf-8"), "keep me")


if __name__ == "__main__":
    unittest.main()
