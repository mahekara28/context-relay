"""Connect model proposals to one declared, checked Python tool inventory.

Only calls made through this harness are protected. Tool schemas and provider
instructions remain application-owned; arbitrary native envelopes are not guessed.
"""
from __future__ import annotations

import json

from .action_repair import _json_value
from .content import _parse
from .middleware import _sync_callable, _sync_result
from .models import clone, encode, nonempty_string
from .tools import ToolRegistry


_MAX_PROPOSAL_CHARS = 64000
_CONTRACT_OPTIONS = frozenset({"when", "bindings", "memory_bindings", "memory", "content",
                               "verification", "outcome", "unchecked", "title"})


class ActionProposalError(ValueError):
    """A completed response is not one supported, unambiguous tool proposal."""

    def __init__(self, response, *, code="invalid_action_proposal"):
        self.response = response
        self.code = code
        super().__init__("Return one registered tool proposal with exactly tool and arguments.")


class ActionResult:
    """The original AgentResult/NativeResult and a defensive execution report."""

    def __init__(self, agent_result, execution):
        self._agent_result = agent_result
        self._execution = clone(execution)

    @property
    def agent_result(self):
        return self._agent_result

    @property
    def execution(self):
        return clone(self._execution)

    @property
    def completed(self):
        """Whether the declared business receipt checks passed, not just callback return."""
        return self._execution.get("completed", False)

    @property
    def executed(self):
        return self._execution["executed"]


def _proposal(value):
    if type(value) is str:
        # Preserve decimal tokens until a checked conversion to Python JSON;
        # values that would silently round are not executable proposals.
        proposal = _json_value(_parse(value, _MAX_PROPOSAL_CHARS))
    elif type(value) is dict:
        text = encode(value)
        if len(text) > _MAX_PROPOSAL_CHARS:
            raise ValueError("Action proposal exceeds the character limit.")
        proposal = json.loads(text)
    else:
        raise TypeError("A complete canonical dictionary or JSON object is required.")
    if type(proposal) is not dict or set(proposal) != {"tool", "arguments"}:
        raise ValueError("Unsupported action proposal fields.")
    nonempty_string(proposal["tool"], "Proposed tool")
    if type(proposal["arguments"]) is not dict:
        raise TypeError("Proposed arguments must be a JSON dictionary.")
    return proposal


class _ActionHarnessBase:
    """Shared inventory setup and captured-turn dispatch for text/native agents."""

    def _initialize(self, agent, tools, *, contracts, decoder, repair, resume, memory):
        if type(tools) is not dict or not 1 <= len(tools) <= 128:
            raise ValueError("tools must be a dictionary containing 1 through 128 named Python functions.")
        inventory = tuple(tools.items())
        for name, _ in inventory:
            nonempty_string(name, "Tool inventory name")
        if contracts is not None and type(contracts) is not dict:
            raise TypeError("contracts must be a dictionary keyed by inventory tool name.")
        contracts = {} if contracts is None else dict(contracts)
        if set(contracts) - set(tools):
            raise ValueError("Contracts refer to tools outside the declared inventory.")
        for options in contracts.values():
            if type(options) is not dict or set(options) - _CONTRACT_OPTIONS:
                raise ValueError("A tool contract must contain only supported registration options.")
        if type(resume) is not bool:
            raise TypeError("resume must be a boolean.")
        if decoder is not None:
            _sync_callable(decoder)
        if type(repair) is str:
            if repair != "known":
                raise ValueError("repair must be 'known', a synchronous callback, or None.")
        elif repair is not None:
            _sync_callable(repair)
        registry = ToolRegistry(agent.guard)
        # A failed later tool cannot leave earlier policies/events registered.
        # The registry is private until this entire transaction commits.
        with agent.guard.store._transaction(write=True):
            for name, function in inventory:
                options = {"memory": memory, **contracts.get(name, {})}
                registry.register(function, name=name, resume=resume, **options)
        self._agent, self._registry = agent, registry
        self._names = tuple(name for name, _ in inventory)
        self._decoder, self._repair = decoder, repair

    def coverage(self, inventory=None):
        """Describe registered checks; direct calls are explicitly unprotected."""
        return self._registry.coverage(self._names if inventory is None else inventory)

    def require_coverage(self, policy):
        """Require a reviewed inventory and argument scope before binding actions."""
        return self._registry.require_coverage(policy)

    def _finish(self, result, turn=None):
        self._agent._require_open()
        try:
            decoded = (result.response if self._decoder is None
                       else _sync_result(self._decoder(result.response)))
            proposal = _proposal(decoded)
        except Exception as exc:
            raise ActionProposalError(result.response) from exc
        if proposal["tool"] not in self._names:
            raise ActionProposalError(result.response, code="unknown_action_tool")
        bound = self._registry.bind(result.prepared if turn is None else turn)
        try:
            arguments = bound._entry(proposal["tool"]).arguments(proposal["arguments"])
        except (TypeError, ValueError) as exc:
            raise ActionProposalError(result.response) from exc
        # Execution exceptions may follow an external effect. Do not translate
        # them into proposal failures or try another action.
        execution = bound.execute_with_recovery(proposal["tool"], arguments, repair=self._repair)
        return ActionResult(result, execution)


class ActionHarness(_ActionHarnessBase):
    """Register once and check each model-proposed action before dispatch.

    Conventional format/language parameters default to automatic-memory bindings.
    Every other parameter still requires a declared contract or ``unchecked``.
    The model must return one canonical ``{tool, arguments}`` proposal, or a
    synchronous application decoder must provide it. Direct Python calls and
    actions hidden inside the provider callback are outside this boundary.
    """

    def __init__(self, agent, tools: dict, *, contracts: dict | None = None,
                 decoder=None, repair=None, resume=False):
        from .connection import AsyncConnectedAgent, ConnectedAgent

        if not isinstance(agent, (ConnectedAgent, AsyncConnectedAgent)):
            raise TypeError("ActionHarness requires a connected Relay agent; native connections need an explicit adapter.")
        agent._require_open()
        if agent.output is not None:
            raise ValueError("ActionHarness cannot combine tool proposals with an enabled OutputPolicy. Use tool contracts instead.")
        self._initialize(agent, tools, contracts=contracts, decoder=decoder, repair=repair, resume=resume, memory=True)
        self._async = isinstance(agent, AsyncConnectedAgent)

    def invoke(self, *, messages, **provider_options):
        """Generate and dispatch once; await the return for an async connection."""
        self._agent._require_open()
        if "stream" in provider_options and provider_options["stream"] is not False:
            raise ValueError("ActionHarness requires complete proposals with stream=False.")
        if self._async:
            return self._invoke_async(messages, provider_options)
        return self._finish(self._agent.invoke(messages=messages, **provider_options))

    async def _invoke_async(self, messages, provider_options):
        return self._finish(await self._agent.invoke(messages=messages, **provider_options))
