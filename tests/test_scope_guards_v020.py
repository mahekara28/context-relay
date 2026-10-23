"""Scope enforcement and revision safety across public preparation boundaries."""
import unittest
import tempfile
from context_relay import auto_connect, OutputPolicy, SessionManager, Principal, AccessDenied


def u(text):
    return {"role": "user", "content": text}


class ScopeGuardTests(unittest.TestCase):
    def test_old_turn_cannot_execute_after_temporary_scope_expires(self):
        effects = []
        with auto_connect(lambda **kw: "ready") as agent:
            agent.guard.allow_tool("export", memory_bindings={"format": "export_format"})
            history = [u("Export as CSV."), u("For this export, use JSON.")]
            first = agent.invoke(messages=history).prepared
            self.assertEqual(agent.guard.relay.check(agent.session_id, {"tool": "export", "arguments": {"format": "JSON"}, "context_revision": first.revision})["decision"], "allow")
            history.extend([{"role": "assistant", "content": "Ready."}, u("Prepare the next export.")])
            latest = agent.invoke(messages=history).prepared
            self.assertGreater(latest.revision, first.revision)
            result = first.execute_with_recovery("export", {"format": "JSON"},
                lambda name, arguments: effects.append(arguments) or {}, repair="known")
            self.assertFalse(result["executed"])
            self.assertEqual(effects, [])
            self.assertEqual(agent.guard.relay.check(agent.session_id, {"tool": "export", "arguments": {"format": "CSV"}, "context_revision": latest.revision})["decision"], "allow")

    def test_managed_task_operations_recheck_owner_permission(self):
        permitted = True
        with tempfile.TemporaryDirectory() as directory:
            with SessionManager(directory, authorize=lambda principal, action: permitted) as manager:
                with manager.connect(lambda **kw: "Ready.", principal=Principal("team", "reader")) as session:
                    chat = session.conversation()
                    initial = chat.task_snapshot()
                    changed = chat.update_task("Compare invoices A17 and B22.", expected_revision=initial["revision"])
                    self.assertEqual(changed["task"]["requests"][0]["content"], "Compare invoices A17 and B22.")
                    permitted = False
                    for operation in (chat.task_snapshot,
                                      lambda: chat.update_task("Replace", expected_revision=changed["revision"]),
                                      lambda: chat.finish_task(expected_revision=changed["revision"])):
                        with self.assertRaises(AccessDenied):
                            operation()

    def test_repreparing_same_input_does_not_expire_override(self):
        with auto_connect(lambda **kw: "ready") as agent:
            history = [u("Export as CSV."), u("For this export, use JSON.")]
            first = agent.invoke(messages=history).prepared
            second = agent.invoke(messages=history).prepared
            self.assertEqual(first.revision, second.revision)
            self.assertEqual(first.memory_snapshot, second.memory_snapshot)
            self.assertEqual(first.memory_snapshot["entries"][0]["value"], "JSON")
            self.assertEqual(agent.memory.entries()[0]["value"], "CSV")

    def test_output_validation_uses_temporary_format_then_standing_format(self):
        responses = iter(['{"name":"ACME-17"}', 'name\nACME-17\n'])
        with auto_connect(lambda **kw: next(responses),
                          output=OutputPolicy(format="memory", memory_key="export_format",
                                              csv_columns=("name",))) as agent:
            history = [u("Export as CSV."), u("For this export, use JSON.")]
            first = agent.invoke(messages=history)
            history.extend([{"role": "assistant", "content": first.response}, u("Export the next record.")])
            second = agent.invoke(messages=history)
            self.assertEqual(first.response, '{"name":"ACME-17"}')
            self.assertEqual(second.response, 'name\nACME-17\n')
            self.assertEqual(agent.memory.entries()[0]["value"], "CSV")


if __name__ == "__main__":
    unittest.main()
