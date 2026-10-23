"""Host-authorized, owner-scoped local sessions and content-free operation metrics.

This is a trusted application boundary, not authentication or a Python sandbox.
Keep the manager and its filesystem private to the server application.
"""

from __future__ import annotations

import hashlib
import inspect
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from .automatic import auto_connect
from .config import RelayConfig
from .connection import AsyncConnectedAgent
from .conversation import _text_response
from .models import encode, nonempty_string
from .store import RelayStore


class AccessDenied(PermissionError):
    """The host denied access, or the session does not belong to this owner."""


class CapacityExceeded(RuntimeError):
    """This manager's concurrent-operation limit was reached; nothing was retried."""


@dataclass(frozen=True, slots=True)
class Principal:
    """Identity supplied by the host's authentication layer, never model input."""

    tenant: str
    subject: str

    def __post_init__(self):
        for name in ("tenant", "subject"):
            nonempty_string(getattr(self, name), name, limit=200)


class SessionManager:
    """Open isolated owner databases; authorize every exposed operation.

    authorize(principal, action) must return exactly True to allow connect,
    call, read, or manage. No default allow policy exists. Permission is checked
    again before returning results; revocation cannot cancel an already sent
    model request or undo local changes. Limits and metrics are process-local.
    """

    def __init__(self, directory, *, authorize, max_concurrent=16):
        if not callable(authorize) or any(inspect.iscoroutinefunction(item) or inspect.isasyncgenfunction(item)
                                         or inspect.isgeneratorfunction(item)
                                         for item in (authorize, getattr(authorize, "__call__", None))):
            raise TypeError("authorize must be a synchronous permission callback.")
        if type(max_concurrent) is not int or not 1 <= max_concurrent <= 10000:
            raise ValueError("max_concurrent must be an integer from 1 to 10000.")
        self.directory = Path(directory).resolve()
        self._authorize, self._limit = authorize, max_concurrent
        self._lock, self._handles = threading.RLock(), set()
        self._closed, self._active = False, 0
        self._metrics = dict(started=0, completed=0, failed=0, denied=0, capacity_rejected=0,
                             peak_active=0, duration_seconds=0.0, max_duration_seconds=0.0)

    def _check(self, principal, action):
        if not isinstance(principal, Principal):
            raise TypeError("principal must be a host-authenticated Principal.")
        allowed = self._authorize(principal, action)
        if inspect.iscoroutine(allowed):
            allowed.close()
        if allowed is not True:
            self._deny()

    def _deny(self, message="Access denied."):
        with self._lock:
            self._metrics["denied"] += 1
        raise AccessDenied(message)

    @contextmanager
    def _operation(self, principal, action, owner=None):
        self._check(principal, action)
        with self._lock:
            if self._closed or owner is not None and owner._closed:
                raise RuntimeError("This scoped connection or session manager is closed.")
            if self._active >= self._limit:
                self._metrics["capacity_rejected"] += 1
                raise CapacityExceeded("Concurrent operation limit reached; retry only under host policy.")
            self._active += 1
            if owner is not None:
                owner._active += 1
            self._metrics["started"] += 1
            self._metrics["peak_active"] = max(self._metrics["peak_active"], self._active)
        started = time.perf_counter()
        succeeded = False
        try:
            try:
                yield
            except Exception:
                try:
                    self._check(principal, action)
                except AccessDenied:
                    raise AccessDenied("Access denied.") from None
                raise
            self._check(principal, action)
            succeeded = True
        finally:
            duration = time.perf_counter() - started
            with self._lock:
                self._active -= 1
                if owner is not None:
                    owner._active -= 1
                self._metrics["completed" if succeeded else "failed"] += 1
                self._metrics["duration_seconds"] += duration
                self._metrics["max_duration_seconds"] = max(self._metrics["max_duration_seconds"], duration)

    def metrics(self):
        """Aggregate counters only; no identities, prompts, responses, or errors."""
        with self._lock:
            return {**self._metrics, "active": self._active, "max_concurrent": self._limit}

    def connect(self, client, *, principal, session_id=None, conversation=None, config=None, **options):
        """Connect/resume in this authenticated owner's database; no client patch.

        Model/provider options go to the returned connection's calls. Relay
        connection settings go here. guard and db_path overrides are rejected.
        """
        if {"guard", "storage", "namespace"} & options.keys():
            raise ValueError("A scoped connection owns its guard and storage namespace.")
        if conversation is not None and session_id is not None:
            raise ValueError("Choose a named conversation or a session_id, not both.")
        agent = handle = None
        try:
            with self._operation(principal, "connect"):
                if config is None:
                    settings = RelayConfig()
                elif isinstance(config, RelayConfig):
                    settings = config
                elif isinstance(config, (str, Path)):
                    settings = RelayConfig.from_file(config)
                else:
                    raise TypeError("config must be a RelayConfig, JSON file path, or None.")
                if settings.db_path != ":memory:":
                    raise ValueError("SessionManager owns db_path; configure other settings only.")
                if session_id is not None:
                    nonempty_string(session_id, "session_id")
                digest = hashlib.sha256(encode([principal.tenant, principal.subject]).encode()).hexdigest()
                if conversation is not None:
                    owner_directory = self.directory / digest
                    if owner_directory.is_symlink() or owner_directory.resolve() != owner_directory:
                        self._deny("Scoped storage path is unavailable.")
                    agent = auto_connect(client, config=settings, conversation=conversation,
                                         storage=owner_directory, namespace="owner", **options)
                else:
                    database = self.directory / (digest + ".sqlite3")
                    if database.is_symlink() or database.resolve().parent != self.directory:
                        self._deny("Scoped storage path is unavailable.")
                    if session_id is not None:
                        if not database.is_file():
                            self._deny("Session is unavailable to this owner.")
                        with RelayStore(database, busy_timeout_ms=settings.busy_timeout_ms) as store:
                            try:
                                store.get_session(session_id)
                            except KeyError:
                                self._deny("Session is unavailable to this owner.")
                    agent = auto_connect(client, config=replace(settings, db_path=str(database)),
                                         session_id=session_id, **options)
                handle = _ScopedSession(self, principal, agent)
                with self._lock:
                    self._handles.add(handle)
            return handle
        except BaseException:
            if handle is not None:
                handle.close()
            elif agent is not None:
                agent.close()
            raise

    def close(self):
        with self._lock:
            if self._active:
                raise CapacityExceeded("Finish outstanding operations before closing the manager.")
            if not self._closed:
                for handle in tuple(self._handles):
                    handle.close()
                self._closed = True

    def __enter__(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("SessionManager is closed.")
        return self

    def __exit__(self, *args):
        self.close()


class _ScopedSession:
    __relay_attachment__ = True  # Do not wrap this access boundary a second time.

    def __init__(self, manager, principal, agent):
        self._manager, self._principal, self._agent = manager, principal, agent
        self._closed, self._active = False, 0

    def _call(self, action, function, *args, **kwargs):
        with self._manager._operation(self._principal, action, self):
            return function(*args, **kwargs)

    async def _acall(self, action, function, *args, **kwargs):
        with self._manager._operation(self._principal, action, self):
            return await function(*args, **kwargs)

    @property
    def session_id(self):
        return self._call("read", lambda: self._agent.session_id)

    def __call__(self, **kwargs):
        if kwargs.get("stream", False) is not False:
            raise ValueError("Scoped sessions require complete text; streaming is unsupported.")
        def complete(response):
            decoder = getattr(self._agent, "_response_decoder", None) or _text_response
            try:
                text = decoder(response)
                if type(text) is not str or not text.strip():
                    raise ValueError("Empty response")
            except Exception:
                raise TypeError("Scoped sessions require completed plain text; streams and tools are unsupported.") from None
            return response
        async def acall():
            return complete(await self._agent(**kwargs))
        def sync_call():
            return complete(self._agent(**kwargs))
        return (self._acall("call", acall) if isinstance(self._agent, AsyncConnectedAgent)
                else self._call("call", sync_call))

    def responses_workflow(self, tools, **options):
        """Compose native Responses history and protected actions under this owner.

        The host must allow the additional execute action. Authorization is
        rechecked after inference and immediately before each original tool.
        """
        if "authorize_action" in options:
            raise ValueError("Scoped workflows own action authorization.")
        from .responses_workflow import ResponsesWorkflow
        def authorize_action():
            self._manager._check(self._principal, "call")
            self._manager._check(self._principal, "execute")
            return True
        workflow = self._call("manage", ResponsesWorkflow, self._agent, tools,
                              authorize_action=authorize_action, **options)
        return _ScopedResponsesWorkflow(self, workflow)

    def conversation(self, **options):
        return _ScopedConversation(self, self._call("manage", self._agent.conversation, **options))

    def privacy_status(self):
        return self._call("read", self._agent.guard.store.privacy_status)

    def checkpoint(self, *, mode="passive"):
        return self._call("manage", self._agent.guard.store.checkpoint, mode=mode)

    def close(self):
        with self._manager._lock:
            if self._active:
                raise CapacityExceeded("Finish this connection's operations before closing it.")
            if not self._closed:
                self._agent.close()
                self._closed = True
                self._manager._handles.discard(self)

    def __enter__(self):
        self._call("read", lambda: None)
        return self

    def __exit__(self, *args):
        self.close()

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *args):
        self.close()


class _ScopedConversation:
    def __init__(self, owner, chat):
        self._owner, self._chat = owner, chat

    @property
    def session_id(self):
        return self._owner._call("read", lambda: self._chat.session_id)

    @property
    def history(self):
        return self._owner._call("read", lambda: self._chat.history)

    @property
    def pending(self):
        return self._owner._call("read", lambda: self._chat.pending)

    def _dispatch(self, name, **options):
        call = self._owner._acall if isinstance(self._chat.agent, AsyncConnectedAgent) else self._owner._call
        return call("call", getattr(self._chat, name), **options)

    def send(self, text, **options):
        return self._dispatch("send", text=text, **options)

    def retry(self, **options):
        return self._dispatch("retry", **options)

    def history_page(self, **options):
        return self._owner._call("read", self._chat.history_page, **options)

    def search_history(self, query, **options):
        return self._owner._call("read", self._chat.search_history, query, **options)

    def task_snapshot(self):
        return self._owner._call("read", self._chat.task_snapshot)

    def update_task(self, text, **options):
        return self._owner._call("manage", self._chat.update_task, text, **options)

    def finish_task(self, **options):
        return self._owner._call("manage", self._chat.finish_task, **options)

    def enable_history_index(self):
        return self._owner._call("manage", self._chat.agent.guard.store.enable_history_index)

    def memory_snapshot(self):
        return self._owner._call("read", lambda: None if self._chat.agent.memory is None
                                else self._chat.agent.memory.snapshot())

    def confirm_memory(self, proposal_id):
        return self._owner._call("manage", lambda: self._chat.agent.memory.confirm(proposal_id))

    def review_plan(self):
        return self._owner._call("read", lambda: self._chat.agent.memory.review_plan())

    def resolve_review(self, plan, *, decisions):
        return self._owner._call("manage", lambda: self._chat.agent.memory.resolve_review(plan, decisions=decisions))

    def deletion_plan(self):
        return self._owner._call("manage", self._chat.deletion_plan)

    def delete_history(self, plan):
        return self._owner._call("manage", self._chat.delete_history, expected=plan)


class _ScopedResponsesWorkflow:
    """Expose only authorized data and operations, never the underlying guard."""
    __relay_attachment__ = True

    def __init__(self, owner, workflow):
        self._owner, self._workflow = owner, workflow

    @property
    def session_id(self):
        return self._owner.session_id

    @property
    def history(self):
        return self._owner._call("read", lambda: self._workflow.history)

    @property
    def pending(self):
        return self._owner._call("read", lambda: self._workflow.pending)

    def request(self, request_id):
        return self._owner._call("read", self._workflow.request, request_id)

    def protection_report(self, inventory=None):
        return self._owner._call("read", self._workflow.protection_report, inventory)

    def history_status(self):
        return self._owner._call("read", self._workflow.history_status)

    def archive_segments(self, **options):
        return self._owner._call("read", self._workflow.archive_segments, **options)

    def read_archive(self, segment_id, **options):
        return self._owner._call("read", self._workflow.read_archive, segment_id, **options)

    def task_snapshot(self):
        return self._owner._call("read", self._workflow.task_snapshot)

    def update_task(self, text, **options):
        return self._owner._call("manage", self._workflow.update_task, text, **options)

    def finish_task(self, **options):
        return self._owner._call("manage", self._workflow.finish_task, **options)

    def review_plan(self):
        return self._owner._call("read", self._workflow.review_plan)

    def resolve_review(self, plan, *, decisions):
        return self._owner._call("manage", self._workflow.resolve_review, plan, decisions=decisions)

    def resume_review(self, request_id):
        return self._owner._call("call", self._workflow.resume_review, request_id)

    def send(self, text, *, request_id, **options):
        return self._owner._call("call", self._workflow.send, text,
                                 request_id=request_id, **options)

    def reconcile(self, request_id, *, result, expected_phase):
        return self._owner._call("manage", self._workflow.reconcile, request_id,
                                 result=result, expected_phase=expected_phase)

    def abandon(self, request_id, *, expected_phase):
        return self._owner._call("manage", self._workflow.abandon, request_id,
                                 expected_phase=expected_phase)

    def deletion_plan(self):
        return self._owner._call("read", self._workflow.deletion_plan)

    def delete_history(self, *, expected):
        return self._owner._call("manage", self._workflow.delete_history, expected=expected)
