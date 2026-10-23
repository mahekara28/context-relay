"""Register Python tools and their declared checks once, then bind each decision.

A registry never obtains a fresh turn during dispatch. It covers only calls
routed through its bound tools; installing Relay cannot intercept arbitrary code.
"""

from __future__ import annotations

import inspect
import threading
from dataclasses import dataclass
from functools import wraps
from collections.abc import Callable, Iterable

from .content import ContentPolicy
from .verification import VerificationPolicy
from .outcomes import OutcomePolicy
from .coverage import CoveragePolicy
from .middleware import ContextGuard, GuardedTurn, _sync_callable, _sync_result, _tool_policy
from .models import clone, nonempty_string, strict_equal


_MEMORY_NAMES = {"format": "export_format", "language": "response_language"}


def _snapshot_signature(signature: inspect.Signature) -> inspect.Signature:
    return signature.replace(parameters=[
        item if item.default is inspect.Parameter.empty else item.replace(default=clone(item.default))
        for item in signature.parameters.values()
    ])


@dataclass(frozen=True)
class _Tool:
    name: str
    function: Callable
    signature: inspect.Signature
    checked: frozenset[str]
    policy: dict
    report: dict

    def arguments(self, arguments: dict, *, final: bool = False) -> dict:
        if type(arguments) is not dict:
            raise TypeError("Tool arguments must be a JSON dictionary.")
        arguments = clone(arguments)
        unknown = set(arguments) - self.signature.parameters.keys()
        if unknown:
            raise TypeError(f"Unknown arguments for {self.name!r}: {', '.join(sorted(unknown))}.")
        for name, parameter in self.signature.parameters.items():
            if name not in arguments:
                # Final dispatch must use exactly the checked argument mapping.
                # A root content repair can omit defaults; never add them here.
                if final:
                    raise TypeError(f"Checked arguments omit parameter {name!r} for {self.name!r}.")
                if parameter.default is not inspect.Parameter.empty:
                    arguments[name] = clone(parameter.default)
                elif name not in self.checked:
                    raise TypeError(f"Missing required argument {name!r} for {self.name!r}.")
        return arguments

    def execute(self, arguments: dict):
        arguments = self.arguments(arguments, final=True)
        # BoundArguments uses signature order, including positional-only arguments.
        bound = inspect.BoundArguments(self.signature, arguments)
        return self.function(*bound.args, **bound.kwargs)


class BoundTools:
    """Registered synchronous tools for one already captured guard revision.

    ``bound[name]`` preserves the Python signature. ``dispatch(name, arguments)``
    accepts the named argument dictionaries returned by agent frameworks.
    A successful action advances the revision; obtain a new model decision and
    bind its own turn before another action. No method refreshes an old turn.
    """

    def __init__(self, turn: GuardedTurn, entries: dict[str, _Tool]):
        self._turn = turn
        self._entries = dict(entries)
        self._dispatch = turn.wrap_dispatcher(self._execute)

    @property
    def revision(self) -> int:
        return self._turn.revision

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def _entry(self, name: str) -> _Tool:
        nonempty_string(name, "tool name")
        try:
            return self._entries[name]
        except KeyError:
            raise KeyError(f"Tool {name!r} is not registered in this bound inventory.") from None

    def _execute(self, name: str, arguments: dict):
        return self._entry(name).execute(arguments)

    def __getitem__(self, name: str) -> Callable:
        entry = self._entry(name)

        @wraps(entry.function)
        def protected(*args, **kwargs):
            arguments = entry.signature.bind(*args, **kwargs)
            arguments.apply_defaults()
            return self.dispatch(name, dict(arguments.arguments))

        protected.__signature__ = _snapshot_signature(entry.signature)
        return protected

    def dispatch(self, name: str, arguments: dict):
        """Check defaults and declared constraints before calling one tool."""
        return self._dispatch(name, self._entry(name).arguments(arguments))

    def execute_with_recovery(self, name: str, arguments: dict, *, repair=None) -> dict:
        """Use the captured turn's optional repair and recheck before execution.

        Missing checked arguments are left for Relay's repair rules. Missing
        unchecked required arguments are setup/proposal errors: Relay has no
        trusted value with which to fill them. Executor failures are never retried.
        """
        entry = self._entry(name)
        arguments = entry.arguments(arguments)
        if repair is not None and not isinstance(repair, str):
            _sync_callable(repair)
            callback = repair

            def repair(request):
                return entry.arguments(_sync_result(callback(request)))

        return self._turn.execute_with_recovery(
            name, arguments, self._execute, repair=repair,
        )


class ToolRegistry:
    """One setup location for Python functions and their argument policies.

    Every parameter, including defaults, must have a fact binding, memory binding,
    content/verification rule or explicit ``unchecked`` declaration. ``memory=True`` opts into
    the conventional format/export_format and language/response_language mapping;
    explicit mappings take precedence. No language-model or network call is made.
    """

    def __init__(self, owner):
        if isinstance(owner, ContextGuard):
            guard = owner
        else:
            from .connection import ConnectedAgent, AsyncConnectedAgent
            if not isinstance(owner, (ConnectedAgent, AsyncConnectedAgent)):
                raise TypeError("ToolRegistry requires a ContextGuard or connected Relay agent.")
            owner._require_open()
            guard = owner.guard
        self._guard = guard
        self._entries: dict[str, _Tool] = {}
        self._lock = threading.RLock()
        self._coverage_policy = None

    def tool(self, function: Callable | None = None, **options):
        """Decorator equivalent of register(); unbound decorated calls fail."""
        if function is None:
            return lambda supplied: self.register(supplied, **options)
        return self.register(function, **options)

    def register(self, function: Callable, *, name: str | None = None,
                 when: dict | None = None, bindings: dict | None = None,
                 memory_bindings: dict | None = None, memory: bool = False,
                 content: ContentPolicy | None = None, verification: VerificationPolicy | None = None,
                 outcome: OutcomePolicy | None = None, unchecked: Iterable[str] = (), title: str | None = None,
                 replace: bool = False, resume: bool = False) -> Callable:
        """Register a function and policy atomically; do not silently replace one.

        Returns a signature-preserving placeholder that requires a bound turn.
        Existing references to the original function remain ordinary Python calls.
        Register before requesting model proposals. ``replace=True`` refreshes
        only a tool owned by this registry whose stored policy is still unchanged;
        old bound turns remain stale after the replacement. ``resume=True`` can
        attach a function to an identical stored contract after restart, without
        changing the revision; changed contracts are refused.
        """
        _sync_callable(function)
        registration = getattr(function, "_relay_registration", None)
        if registration is not None:
            owner, registered_name = registration
            if owner is not self or (name is not None and name != registered_name):
                raise ValueError("A registered placeholder can only refresh its owning registry and tool name.")
            with self._lock:
                function = self._entries[registered_name].function
            name = registered_name
        if type(replace) is not bool or type(resume) is not bool:
            raise TypeError("replace and resume must be booleans.")
        if replace and resume:
            raise ValueError("Choose replace or resume, not both.")
        name = nonempty_string(name if name is not None else getattr(function, "__name__", None), "tool name")
        try:
            signature = inspect.signature(function)
        except (TypeError, ValueError) as exc:
            raise TypeError("A registered tool needs an inspectable Python signature.") from exc
        parameters = list(signature.parameters.values())
        if any(item.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
               for item in parameters):
            raise TypeError("Variadic tools need an explicit adapter with named parameters.")
        # Snapshot mutable defaults so changing the original function later cannot
        # silently change the registered argument mapping.
        signature = _snapshot_signature(signature)
        if type(memory) is not bool:
            raise TypeError("memory must be a boolean.")
        for label, value in (("bindings", bindings), ("memory_bindings", memory_bindings)):
            if value is not None and type(value) is not dict:
                raise TypeError(f"{label} must be a dictionary.")
        bindings = clone(bindings or {})
        memory_bindings = clone(memory_bindings or {})
        if memory:
            for argument, key in _MEMORY_NAMES.items():
                if argument in signature.parameters and argument not in bindings:
                    memory_bindings.setdefault(argument, key)
        if isinstance(unchecked, (str, bytes)):
            raise TypeError("unchecked must contain parameter names, not a string.")
        unchecked = tuple(unchecked)
        for argument in unchecked:
            nonempty_string(argument, "Unchecked parameter")
        if len(set(unchecked)) != len(unchecked):
            raise ValueError("unchecked must not contain duplicate parameters.")
        if content is not None and not isinstance(content, ContentPolicy):
            raise TypeError("content must be a ContentPolicy or None.")
        if verification is not None and not isinstance(verification, VerificationPolicy):
            raise TypeError("verification must be a VerificationPolicy or None.")
        if outcome is not None and not isinstance(outcome, OutcomePolicy):
            raise TypeError("outcome must be an OutcomePolicy or None.")
        content_arguments = set() if content is None else set(content.argument_names)
        verification_arguments = set() if verification is None else set(verification.argument_names)
        checked = set(bindings) | set(memory_bindings) | content_arguments | verification_arguments
        declared = checked | set(unchecked)
        unknown = declared - signature.parameters.keys()
        missing = signature.parameters.keys() - declared
        if unknown:
            raise ValueError(f"Policy names unknown parameters: {', '.join(sorted(unknown))}.")
        if missing:
            raise ValueError(f"Declare checks or unchecked for parameters: {', '.join(sorted(missing))}.")
        if checked & set(unchecked):
            raise ValueError("A checked parameter cannot also be unchecked.")

        report = {
            "parameters": list(signature.parameters),
            "checked": {argument: {
                **({"fact": bindings[argument]} if argument in bindings else {}),
                **({"memory": memory_bindings[argument]} if argument in memory_bindings else {}),
                **({"content": True} if argument in content_arguments else {}),
                **({"verification": True} if argument in verification_arguments else {}),
            } for argument in signature.parameters if argument in checked},
            "unchecked": list(unchecked),
            "content": None if content is None else content.describe(),
        }
        if outcome is not None:
            report["outcome"] = {"required_paths": sorted(outcome.to_dict()["required"]),
                                 "external_truth_verified": False}
        if verification is not None:
            report["verification"] = {"claim_arguments": sorted(verification_arguments),
                                      "semantic_meaning_checked": False}
        # Executors hold the store lock and may read coverage. Always acquire
        # store before registry; reversing that order can deadlock registration.
        with self._guard.store._locked(), self._lock:
            previous = self._entries.get(name)
            if previous is not None and not replace:
                raise ValueError(f"Tool {name!r} is already registered; use replace=True for a reviewed update.")
            if replace and previous is None:
                raise ValueError("replace=True requires a tool already owned by this registry.")
            with self._guard.store._transaction(write=True):
                stored = next((policy for policy in self._guard.store.policies(self._guard.session_id)
                               if policy["tool"] == name), None)
                if replace:
                    if not strict_equal(previous.policy, stored):
                        raise ValueError("A registered policy was changed outside this registry; replacement is blocked.")
                candidate = _tool_policy(name, when=when, bindings=bindings, memory_bindings=memory_bindings,
                                         content=content, verification=verification, outcome=outcome, optional_args=unchecked, title=title)
                if stored is not None and not replace:
                    if not resume or not strict_equal(candidate, stored):
                        raise ValueError(f"Tool {name!r} already has a policy; resume requires the identical contract.")
                    policy = stored
                else:
                    self._guard.store.add_policy(self._guard.session_id, candidate)
                    policy = candidate
                if resume:
                    report["resumed"] = stored is not None
            report["required_facts"] = [item["key"] for item in policy["requirements"]]
            self._entries[name] = _Tool(name, function, signature, frozenset(checked), policy, report)

        @wraps(function)
        def unbound(*args, **kwargs):
            raise RuntimeError(f"Tool {name!r} needs a captured turn: use registry.bind(turn)[{name!r}].")

        unbound.__signature__ = _snapshot_signature(signature)
        unbound._relay_registration = (self, name)
        return unbound

    def bind(self, turn: GuardedTurn) -> BoundTools:
        """Bind this inventory to the turn that produced a model's proposal."""
        if (not isinstance(turn, GuardedTurn) or turn._relay is not self._guard.relay
                or turn._session_id != self._guard.session_id):
            raise ValueError("The turn must belong to this registry's exact guard and session.")
        with self._guard.store._locked(), self._lock:
            policies = {policy["tool"]: policy for policy in self._guard.store.policies(self._guard.session_id)}
            if any(not strict_equal(entry.policy, policies.get(name)) for name, entry in self._entries.items()):
                raise ValueError("A registered policy was changed outside this registry; resolve the competing setup before binding.")
            if self._coverage_policy is not None:
                self._coverage_policy.require(self.coverage(self._coverage_policy.inventory))
            return BoundTools(turn, self._entries)

    def require_coverage(self, policy: CoveragePolicy) -> dict:
        """Validate coverage now and again before subsequent bind operations."""
        if not isinstance(policy, CoveragePolicy):
            raise TypeError("policy must be a CoveragePolicy.")
        with self._guard.store._locked(), self._lock:
            report = policy.require(self.coverage(policy.inventory))
            self._coverage_policy = policy
            return report

    def coverage(self, inventory: Iterable[str] | None = None) -> dict:
        """Describe declared coverage, optionally comparing a host's tool names.

        The host supplies the inventory; Relay cannot discover bypassed actions.
        Reports contain scope metadata, not content reference values.
        """
        supplied = None
        if inventory is not None:
            if isinstance(inventory, (str, bytes)):
                raise TypeError("inventory must contain tool names, not a string.")
            supplied = tuple(inventory)
            for name in supplied:
                nonempty_string(name, "Inventory tool name")
            if len(set(supplied)) != len(supplied):
                raise ValueError("inventory must not contain duplicate tool names.")
        with self._guard.store._locked(), self._lock:
            registered = set(self._entries)
            policies = {item["tool"]: item for item in self._guard.store.policies(self._guard.session_id)}
            return clone({
                "registered_tools": sorted(registered),
                "tools": {name: entry.report for name, entry in sorted(self._entries.items())},
                "provided_tools": None if supplied is None else sorted(supplied),
                "unregistered_tools": None if supplied is None else sorted(set(supplied) - registered),
                "registered_not_provided": None if supplied is None else sorted(registered - set(supplied)),
                "inventory_fully_registered": None if supplied is None else set(supplied) <= registered,
                "direct_calls_protected": False,
                "semantic_meaning_checked": False,
                "stale_contracts": sorted(name for name, entry in self._entries.items()
                                          if not strict_equal(entry.policy, policies.get(name))),
                "enforcement_boundary": "calls_dispatched_through_this_registry",
            })
