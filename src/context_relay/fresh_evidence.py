"""Fresh host-owned evidence at preparation and immediately before a tool effect."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import time

from .middleware import _sync_callable, _sync_result
from .models import encode, nonempty_string, strict_equal


_RESERVED = frozenset({"workflow_request_id", "workflow_source_version", "workflow_evidence_keys"})


class EvidenceChanged(RuntimeError):
    """Evidence changed or expired; no permission to renew the old proposal."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__("Fresh evidence is unavailable: " + reason)


def _finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(name + " must be a finite number.")
    return value


@dataclass(frozen=True, init=False)
class EvidenceSnapshot:
    version: str
    observed_at: float
    max_age_seconds: float
    _facts_json: str
    fingerprint: str

    def __init__(self, *, version, facts, observed_at, max_age_seconds):
        nonempty_string(version, "evidence version")
        _finite(observed_at, "observed_at")
        _finite(max_age_seconds, "max_age_seconds")
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive.")
        if type(facts) is not dict or not 1 <= len(facts) <= 64:
            raise ValueError("Evidence facts must contain 1 through 64 named JSON values.")
        for key in facts:
            nonempty_string(key, "evidence fact")
        if set(facts) & _RESERVED:
            raise ValueError("Evidence cannot override reserved workflow metadata.")
        serialized = encode(facts)
        if len(serialized) > 64000:
            raise ValueError("Evidence facts exceed 64000 characters; select relevant fields.")
        serialized.encode("utf-8")
        for name, value in (("version", version), ("observed_at", observed_at),
                            ("max_age_seconds", max_age_seconds), ("_facts_json", serialized),
                            ("fingerprint", hashlib.sha256(json.dumps([version, json.loads(serialized)], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest())):
            object.__setattr__(self, name, value)
        self.require_fresh()

    @property
    def facts(self):
        return json.loads(self._facts_json)

    def require_fresh(self):
        age = time.time() - self.observed_at
        if age < -5 or age > self.max_age_seconds:
            raise EvidenceChanged("future_timestamp" if age < -5 else "expired")

    def apply(self, guard):
        """Atomically refresh a fixed evidence schema; old proposals become stale.

        The source version is exposed as workflow_source_version for destination
        compare-and-set bindings. Never use model/retrieval text as trusted facts.
        """
        self.require_fresh()
        facts = self.facts
        with guard.store._transaction(write=True):
            guard.store._assert_mutable()
            existing = guard.store.facts(guard.session_id)
            previous = existing.get("workflow_evidence_keys")
            keys = sorted(facts)
            if previous is not None and not strict_equal(previous["value"], keys):
                raise EvidenceChanged("schema_changed")
            facts.update(workflow_source_version=self.version, workflow_evidence_keys=keys)
            for key, value in facts.items():
                if key not in existing or not strict_equal(existing[key]["value"], value):
                    guard.remember(key, value, source="workflow_evidence")
            return guard.store.get_session(guard.session_id)["revision"]


class EvidenceSource:
    """A synchronous trusted reader returning version, facts and observed_at.

    Fresh reads can still return an incorrect or cached source. The destination
    must enforce the same version precondition and idempotency key atomically;
    a read/check alone cannot prevent an external race after the check.
    """

    def __init__(self, reader, *, max_age_seconds=30, verify=None, verifier_id=None):
        _sync_callable(reader)
        _finite(max_age_seconds, "max_age_seconds")
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive.")
        if verify is not None:
            _sync_callable(verify)
            nonempty_string(verifier_id, "verifier_id")
            if len(verifier_id) > 128:
                raise ValueError("verifier_id must not exceed 128 characters.")
        elif verifier_id is not None:
            raise ValueError("verifier_id requires a verification callback.")
        self._reader, self.max_age_seconds = reader, max_age_seconds
        self._verify, self._verifier_id = verify, verifier_id

    def describe(self):
        """Stable resume configuration; callback implementations remain host-owned."""
        result = {"max_age_seconds": self.max_age_seconds}
        if self._verify is not None:
            result["verifier_id"] = self._verifier_id
        return result

    def read(self):
        value = _sync_result(self._reader())
        if type(value) is not dict or set(value) != {"version", "facts", "observed_at"}:
            raise ValueError("Evidence reader must return version, facts and observed_at only.")
        snapshot = EvidenceSnapshot(**value, max_age_seconds=self.max_age_seconds)
        if self._verify is not None:
            try:
                accepted = _sync_result(self._verify(snapshot))
            except Exception as exc:
                raise EvidenceChanged("verification_unavailable") from exc
            if accepted is not True:
                raise EvidenceChanged("verification_rejected" if accepted is False else "verification_invalid_result")
            # A slow verifier cannot turn expired source data into fresh evidence.
            snapshot.require_fresh()
        return snapshot

    def assert_current(self, snapshot):
        if not isinstance(snapshot, EvidenceSnapshot):
            raise TypeError("snapshot must be an EvidenceSnapshot.")
        snapshot.require_fresh()
        current = self.read()
        if current.fingerprint != snapshot.fingerprint:
            raise EvidenceChanged("source_changed")
        return current
