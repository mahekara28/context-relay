"""Access checks around failures and unsupported responses; offline providers only."""

import dataclasses
import inspect
import tempfile
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace

from context_relay.organization import AccessDenied, Principal, SessionManager


PRINCIPAL = Principal("team-one", "alice")
MESSAGES = [{"role": "user", "content": "Hello"}]


class OrganizationEdgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def manager(self, authorize=lambda principal, action: True):
        manager = SessionManager(self.directory.name, authorize=authorize)
        self.addCleanup(manager.close)
        return manager

    def test_explicit_streaming_is_rejected_before_provider_call(self):
        calls = []
        def model(**kwargs):
            calls.append(kwargs)
            return "complete"
        manager = self.manager()
        with manager.connect(model, principal=PRINCIPAL) as connection:
            for flag in (True, 1, "yes", None):
                with self.subTest(flag=flag), self.assertRaises(ValueError):
                    connection(messages=MESSAGES, stream=flag)
            self.assertEqual(calls, [])
            self.assertEqual(connection(messages=MESSAGES, stream=False), "complete")
        self.assertEqual(manager.metrics()["active"], 0)

    def test_unexpected_stream_is_rejected_without_consuming_it_and_capacity_is_released(self):
        consumed = []
        def stream():
            consumed.append(True)
            yield "private chunk"
        generated = stream()
        self.addCleanup(generated.close)  # Unexpected provider resources remain host-owned.
        manager = self.manager()
        with manager.connect(lambda **kwargs: generated, principal=PRINCIPAL) as connection:
            with self.assertRaises(TypeError) as caught:
                connection(messages=MESSAGES)
            self.assertFalse(hasattr(caught.exception, "response"))
            self.assertEqual(consumed, [])
            self.assertEqual(manager.metrics()["active"], 0)
        self.assertEqual(manager.metrics()["failed"], 1)

    def test_unexpected_coroutine_from_sync_provider_is_closed_when_rejected(self):
        async def response():
            return "complete"
        coroutine = response()
        self.addCleanup(coroutine.close)
        manager = self.manager()
        with manager.connect(lambda **kwargs: coroutine, principal=PRINCIPAL) as connection:
            with self.assertRaises(TypeError):
                connection(messages=MESSAGES)
            self.assertIsNone(coroutine.cr_frame)
        self.assertEqual(manager.metrics()["active"], 0)

    def test_post_call_revocation_masks_provider_exception_and_visible_traceback(self):
        permitted = True
        def model(**kwargs):
            nonlocal permitted
            permitted = False
            raise RuntimeError("PRIVATE-PROVIDER-DIAGNOSTIC")
        manager = self.manager(lambda principal, action: permitted)
        connection = manager.connect(model, principal=PRINCIPAL)
        with self.assertRaises(AccessDenied) as caught:
            connection(messages=MESSAGES)
        visible_traceback = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn("PRIVATE-PROVIDER-DIAGNOSTIC", visible_traceback)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertFalse(hasattr(caught.exception, "response"))
        connection.close()  # Permission withdrawal cannot block cleanup.
        self.assertEqual(manager.metrics()["active"], 0)
        self.assertEqual(manager.metrics()["failed"], 1)

    def test_response_bearing_managed_error_is_not_returned_after_revocation(self):
        permitted = True
        def model(**kwargs):
            nonlocal permitted
            permitted = False
            return {"private_payload": "PRIVATE-RESPONSE"}
        manager = self.manager(lambda principal, action: permitted)
        connection = manager.connect(model, principal=PRINCIPAL)
        chat = connection.conversation()
        with self.assertRaises(AccessDenied) as caught:
            chat.send("Hello")
        self.assertFalse(hasattr(caught.exception, "response"))
        self.assertNotIn("PRIVATE-RESPONSE", "".join(traceback.format_exception(caught.exception)))
        with self.assertRaises(AccessDenied):
            _ = chat.pending
        connection.close()
        self.assertEqual(manager.metrics()["active"], 0)

    def test_authorized_failure_preserves_the_original_exception_for_the_host(self):
        problem = TimeoutError("host diagnostic")
        def model(**kwargs):
            raise problem
        manager = self.manager()
        with manager.connect(model, principal=PRINCIPAL) as connection:
            with self.assertRaises(TimeoutError) as caught:
                connection(messages=MESSAGES)
            self.assertIs(caught.exception, problem)
        metrics = manager.metrics()
        self.assertEqual(metrics["active"], 0)
        self.assertTrue(all(type(value) in (int, float) for value in metrics.values()))
        self.assertEqual(metrics["failed"], 1)

    def test_revocation_at_connect_completion_removes_and_closes_registered_handle(self):
        checks = []
        def authorize(principal, action):
            checks.append(action)
            return len(checks) == 1
        manager = self.manager(authorize)
        with self.assertRaises(AccessDenied):
            manager.connect(lambda **kwargs: "complete", principal=PRINCIPAL)
        self.assertEqual(checks, ["connect", "connect"])
        self.assertEqual(manager._handles, set())
        self.assertEqual(manager.metrics()["active"], 0)
        self.assertEqual(manager.metrics()["failed"], 1)

    def test_principal_fields_are_immutable_and_owner_names_cannot_be_path_components(self):
        principal = Principal("../external/tenant", "../alice\\owner")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            principal.subject = "bob"
        manager = self.manager()
        with manager.connect(lambda **kwargs: "complete", principal=principal) as connection:
            session_id = connection.session_id
        databases = list(Path(self.directory.name).glob("*.sqlite3"))
        self.assertEqual(len(databases), 1)
        self.assertEqual(len(databases[0].stem), 64)
        self.assertEqual(databases[0].parent.resolve(), Path(self.directory.name).resolve())
        with manager.connect(lambda **kwargs: "complete", principal=Principal(principal.tenant, principal.subject),
                             session_id=session_id) as resumed:
            self.assertEqual(resumed.session_id, session_id)

    def test_provider_decoder_rejects_truncated_text_and_preserves_valid_response_identity(self):
        response = {"type": "message", "role": "assistant", "stop_reason": "max_tokens",
                    "content": [{"type": "text", "text": "partial"}]}
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
        manager = self.manager()
        with manager.connect(client, principal=PRINCIPAL, provider="anthropic") as connection:
            with self.assertRaises(TypeError):
                connection(messages=MESSAGES, model="offline-model", max_tokens=8)
            response["stop_reason"] = "end_turn"
            self.assertIs(connection(messages=MESSAGES, model="offline-model", max_tokens=8), response)
        self.assertEqual(manager.metrics()["failed"], 1)


class OrganizationAsyncEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_provider_error_after_revocation_masks_payload_and_releases_capacity(self):
        permitted = True
        async def model(**kwargs):
            nonlocal permitted
            permitted = False
            raise RuntimeError("PRIVATE-ASYNC-DIAGNOSTIC")
        with tempfile.TemporaryDirectory() as directory:
            with SessionManager(directory, authorize=lambda principal, action: permitted) as manager:
                connection = manager.connect(model, principal=PRINCIPAL)
                with self.assertRaises(AccessDenied) as caught:
                    await connection(messages=MESSAGES)
                self.assertNotIn("PRIVATE-ASYNC-DIAGNOSTIC", "".join(traceback.format_exception(caught.exception)))
                self.assertEqual(manager.metrics()["active"], 0)
                self.assertEqual(manager.metrics()["failed"], 1)
                connection.close()

    async def test_async_stream_option_is_rejected_before_coroutine_or_provider_starts(self):
        calls = []
        async def model(**kwargs):
            calls.append(kwargs)
            return "complete"
        with tempfile.TemporaryDirectory() as directory:
            with SessionManager(directory, authorize=lambda principal, action: True) as manager:
                async with manager.connect(model, principal=PRINCIPAL) as connection:
                    with self.assertRaises(ValueError):
                        await connection(messages=MESSAGES, stream=True)
                    self.assertEqual(calls, [])
                    result = connection(messages=MESSAGES)
                    self.assertTrue(inspect.isawaitable(result))
                    self.assertEqual(await result, "complete")
                self.assertEqual(manager.metrics()["active"], 0)


if __name__ == "__main__":
    unittest.main()
