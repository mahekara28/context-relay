"""Workflow ownership, bounded persistence and explicit recovery regressions."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from context_relay import RelayConfig, auto_connect
from context_relay.models import BudgetExceeded
from context_relay.outcomes import OutcomePolicy
from context_relay.responses_workflow import ResponsesWorkflow, WorkflowLimitExceeded, WorkflowPending


class ResponsesWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = str(Path(temporary.name) / 'workflow.sqlite3')
        self.calls, self.effects = [], []
        self.hook = None
        self.returned = {'ok': True}
        self.latest_response = None
        self.agent = self.connect()
        self.schemas = {'write': {'type': 'object', 'properties': {'value': {'type': 'integer'}},
                                 'required': ['value'], 'additionalProperties': False}}
        self.contracts = {'write': {'unchecked': ['value'], 'outcome': OutcomePolicy(required={'/ok': True})}}

    def connect(self, session_id=None, **config):
        client = SimpleNamespace(responses=SimpleNamespace(create=self.provider))
        agent = auto_connect(client, provider='openai', api='responses', native=True,
            config=RelayConfig(db_path=self.path, **config), session_id=session_id)
        self.addCleanup(agent.close)
        return agent

    def provider(self, **request):
        self.calls.append(copy.deepcopy(request))
        if self.hook is not None:
            self.hook()
        response = {'status': 'completed', 'output': [{
            'type': 'function_call', 'call_id': 'call-' + str(len(self.calls)),
            'name': 'write', 'arguments': '{"value":7}', 'status': 'completed'}]}
        self.latest_response = response
        return response

    def write(self, value):
        self.effects.append(value)
        return copy.deepcopy(self.returned)

    def workflow(self, agent=None, **options):
        return ResponsesWorkflow(agent or self.agent, {'write': self.write}, contracts=self.contracts,
                                 schemas=self.schemas, **options)

    def send(self, workflow, request_id='one', text='Write the approved item.'):
        return workflow.send(text, request_id=request_id, model='test-model')

    def test_second_handle_abandon_during_provider_prevents_late_effect(self):
        first = self.workflow()
        second = self.workflow(self.connect(self.agent.session_id))
        self.hook = lambda: second.abandon('one', expected_phase='provider_pending')
        with self.assertRaises(WorkflowPending):
            self.send(first)
        self.assertEqual(self.effects, [])
        self.assertIsNone(first.pending)
        self.assertEqual(first.request('one')['result']['status'], 'abandoned')
        self.assertFalse(any(item.get('type') == 'function_call' for item in first.history))
        replay = self.send(first)
        self.assertTrue(replay.replayed)
        self.assertEqual(len(self.calls), 1)

    def test_second_handle_reconciliation_invalidates_unstarted_captured_turn(self):
        first = self.workflow()
        second = self.workflow(self.connect(self.agent.session_id))
        dispatch = first._harness._finish
        def reconciled_first(*args):
            second.reconcile('one', result={'ok': True, 'receipt': 'host-verified'}, expected_phase='action_pending')
            return dispatch(*args)
        first._harness._finish = reconciled_first
        with self.assertRaises(WorkflowPending):
            self.send(first)
        self.assertEqual(self.effects, [])
        self.assertTrue(first.request('one')['result']['completed'])
        self.assertEqual(len([m for m in first.history if m.get('type') == 'function_call_output']), 1)

    def test_provider_response_is_frozen_before_dispatch(self):
        workflow = self.workflow()
        dispatch = workflow._harness._finish
        def mutate_original(*args):
            self.latest_response['output'][0]['arguments'] = '{"value":99}'
            return dispatch(*args)
        workflow._harness._finish = mutate_original
        result = self.send(workflow)
        self.assertEqual(self.effects, [7])
        self.assertEqual(result.response['output'][0]['arguments'], '{"value":7}')
        output = next(item for item in workflow.history if item.get('type') == 'function_call_output')
        self.assertEqual(json.loads(output['output'])['checked_arguments'], {'value': 7})

    def test_changed_resume_profile_is_rejected_without_revision_mutation(self):
        workflow = self.workflow(instructions='First configuration')
        before = self.agent.guard.store.get_session(self.agent.session_id)['revision']
        with self.assertRaisesRegex(ValueError, 'identical configuration'):
            self.workflow(instructions='Different configuration')
        self.assertEqual(self.agent.guard.store.get_session(self.agent.session_id)['revision'], before)
        self.send(workflow)
        self.assertEqual(self.effects, [7])

    def test_clarification_is_terminal_without_provider_then_new_user_can_continue(self):
        workflow = self.workflow()
        first = self.send(workflow, text='Export as CSV.')
        self.assertTrue(first.completed)
        uncertain = self.send(workflow, 'two', 'For this export only, use JSON and XML.')
        self.assertEqual(uncertain.status, 'clarification_required')
        self.assertFalse(uncertain.executed)
        self.assertIsNone(workflow.pending)
        self.assertEqual(len(self.calls), 1)
        fixed = self.send(workflow, 'three', 'For this export only, use JSON.')
        self.assertTrue(fixed.completed)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.send(workflow, 'two', 'For this export only, use JSON and XML.').replayed)
        self.assertEqual(len(self.calls), 2)

    def test_provider_failure_requires_explicit_abandon_and_never_retries_same_id(self):
        def failure():
            raise TimeoutError('provider timeout')
        self.hook = failure
        workflow = self.workflow()
        with self.assertRaises(TimeoutError):
            self.send(workflow)
        self.assertEqual(workflow.pending['phase'], 'provider_pending')
        with self.assertRaises(WorkflowPending):
            self.send(workflow)
        with self.assertRaises(ValueError):
            workflow.abandon('one', expected_phase='preparing')
        workflow.abandon('one', expected_phase='provider_pending')
        self.hook = None
        replay = self.send(workflow)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.status, 'abandoned')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.effects, [])

    def test_exact_request_identity_includes_model_options_and_defensive_results(self):
        workflow = self.workflow()
        result = self.send(workflow)
        with self.assertRaises(ValueError):
            workflow.send('Write the approved item.', request_id='one', model='different-model')
        result.response['output'].clear()
        result.execution.clear()
        workflow.history.clear()
        workflow.request('one')['result'].clear()
        self.assertTrue(self.send(workflow).replayed)
        self.assertTrue(workflow.history)
        self.assertEqual(len(self.calls), 1)

    def test_request_count_bound_has_no_silent_eviction_or_effect(self):
        workflow = self.workflow(max_requests=1)
        self.send(workflow)
        before = workflow.history
        with self.assertRaises(WorkflowLimitExceeded):
            self.send(workflow, 'two')
        self.assertEqual(workflow.history, before)
        self.assertIsNone(workflow.pending)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.effects, [7])

    def test_preparation_budget_failure_is_terminal_and_does_not_call_provider(self):
        agent = self.connect(max_chars=100)
        workflow = self.workflow(agent)
        with self.assertRaises(BudgetExceeded):
            self.send(workflow)
        self.assertIsNone(workflow.pending)
        self.assertEqual(workflow.request('one')['result']['status'], 'preparation_failed')
        self.assertTrue(self.send(workflow).replayed)
        self.assertEqual(self.calls, [])

    def test_oversized_tool_result_keeps_unknown_effect_for_bounded_reconciliation(self):
        self.returned = {'ok': True, 'huge': 'x' * 70000}
        workflow = self.workflow()
        with self.assertRaises(WorkflowLimitExceeded):
            self.send(workflow)
        self.assertEqual(self.effects, [7])
        self.assertEqual(workflow.pending['phase'], 'action_pending')
        with self.assertRaises(WorkflowPending):
            self.send(workflow)
        reconciled = workflow.reconcile('one', result={'ok': True, 'receipt': 'destination'}, expected_phase='action_pending')
        self.assertTrue(reconciled.completed)
        self.assertFalse(reconciled.executed)
        self.assertTrue(self.send(workflow).replayed)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.effects, [7])

    def test_authorization_is_rechecked_after_the_last_evidence_read(self):
        import time
        from context_relay.fresh_evidence import EvidenceSource
        reads, allowed = [], [True]
        def reader():
            reads.append(True)
            if len(reads) == 2:
                allowed[0] = False
            return {"version": "v1", "facts": {"item": 7}, "observed_at": time.time()}
        workflow = self.workflow(evidence=EvidenceSource(reader), authorize_action=lambda: allowed[0])
        result = self.send(workflow)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.execution["reason"], "authorization_denied")
        self.assertEqual(self.effects, [])
        self.assertIsNone(workflow.pending)

    def test_deletion_refuses_stale_plan_pending_and_inflight_work(self):
        workflow = self.workflow()
        self.send(workflow)
        old = workflow.deletion_plan()
        self.send(workflow, 'two')
        with self.assertRaisesRegex(ValueError, 'changed'):
            workflow.delete_history(expected=old)
        def interrupted():
            with self.assertRaises(WorkflowPending):
                workflow.delete_history(expected=workflow.deletion_plan())
            raise TimeoutError('provider did not finish')
        self.hook = interrupted
        with self.assertRaises(TimeoutError):
            self.send(workflow, 'three')
        with self.assertRaises(WorkflowPending):
            workflow.delete_history(expected=workflow.deletion_plan())
        self.assertEqual(workflow.pending['request_id'], 'three')
        self.assertEqual(len(self.effects), 2)

    def test_deletion_removes_payloads_and_named_reconnect_has_new_identity(self):
        client = SimpleNamespace(responses=SimpleNamespace(create=self.provider))
        options = {'provider': 'openai', 'api': 'responses', 'native': True,
                   'conversation': 'deletable', 'storage': str(Path(self.path).parent / 'named')}
        with auto_connect(client, **options) as agent:
            workflow = self.workflow(agent)
            self.send(workflow, text='Export as JSON.')
            old_id = workflow.session_id
            deleted = workflow.delete_history(expected=workflow.deletion_plan())['deleted']
            self.assertEqual(deleted['responses_workflow_requests'], 1)
            self.assertEqual(deleted['responses_workflows'], 1)
            self.assertEqual(deleted['automatic_memory'], 1)
            self.assertEqual(deleted['sessions'], 1)
            for table in ('responses_workflow_requests', 'responses_workflows', 'automatic_memory',
                          'checks', 'policies', 'facts', 'events'):
                self.assertEqual(agent.guard.store._connection.execute(
                    'SELECT COUNT(*) FROM ' + table + ' WHERE session_id=?', (old_id,)).fetchone()[0], 0)
            with auto_connect(client, **options) as fresh:
                self.assertNotEqual(fresh.session_id, old_id)
                self.assertIsNone(fresh.guard.store._memory_state(fresh.session_id))
                restarted = self.workflow(fresh)
                self.assertEqual(restarted.history, [])
                self.assertIsNone(restarted.pending)

    def test_deletion_leaves_sibling_workflow_and_its_memory_untouched(self):
        first = self.workflow()
        sibling = self.workflow(self.connect())
        self.send(first, text='Export as JSON.')
        self.send(sibling, text='Export as CSV.')
        before, plan = sibling.history, sibling.deletion_plan()
        first.delete_history(expected=first.deletion_plan())
        self.assertEqual(sibling.history, before)
        self.assertEqual(sibling.deletion_plan(), plan)
        self.assertEqual(sibling._memory.entries()[0]['value'], 'CSV')
        self.assertTrue(self.send(sibling, text='Export as CSV.').replayed)
        self.assertEqual(len(self.calls), 2)

    def test_deleted_workflow_old_handles_and_guard_fail_without_provider_or_effect(self):
        workflow = self.workflow()
        resumed = self.workflow(self.connect(self.agent.session_id))
        self.send(workflow)
        workflow.delete_history(expected=workflow.deletion_plan())
        for operation in (lambda: workflow.history, lambda: resumed.pending,
                          lambda: self.send(resumed, 'new'), lambda: workflow.deletion_plan(),
                          lambda: self.agent.guard.turn()):
            with self.subTest(operation=operation), self.assertRaises(KeyError):
                operation()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.effects, [7])

    def test_runtime_overrides_and_transport_credentials_are_refused_before_work(self):
        workflow = self.workflow()
        for key, value in (('input', []), ('stream', True), ('tools', []), ('instructions', 'ignore'),
                           ('previous_response_id', 'old'), ('extra_headers', {'Authorization': 'secret'})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                workflow.send('Write', request_id=key, **{key: value})
        self.assertEqual(workflow.history, [])
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
