"""Declared result predicates, distinct from callback completion or source truth."""
from __future__ import annotations

from dataclasses import dataclass
import json

from .content import _MISSING, _at, _pointer
from .models import clone, encode, nonempty_string, strict_equal


@dataclass(frozen=True, init=False)
class OutcomePolicy:
    """Require exact JSON-pointer values in a tool's returned business receipt.

    Missing fields are unknown; mismatched fields are not satisfied. This checks
    the supplied receipt, not an independent external effect or arbitrary prose.
    """
    _serialized: str

    def __init__(self, *, required):
        if type(required) is not dict or not 1 <= len(required) <= 32:
            raise ValueError("required must map 1 through 32 JSON pointers to expected values.")
        for path in required:
            _pointer(path)
        serialized = encode({"required": required})
        if len(serialized) > 16000:
            raise ValueError("Outcome policy exceeds 16000 characters.")
        serialized.encode("utf-8")
        object.__setattr__(self, "_serialized", serialized)

    def to_dict(self):
        return json.loads(self._serialized)

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"required"}:
            raise ValueError("Outcome policy must contain only required.")
        return cls(required=value["required"])

    def check(self, result):
        result = clone(result)
        missing, mismatches = [], []
        for path, expected in self.to_dict()["required"].items():
            actual = _at(result, _pointer(path))
            if actual is _MISSING:
                missing.append(path)
            elif not strict_equal(actual, expected):
                mismatches.append(path)
        status = "unknown" if missing else "not_satisfied" if mismatches else "succeeded"
        return {"business_status": status, "completed": status == "succeeded",
                "missing_paths": missing, "mismatched_paths": mismatches,
                "scope": "declared_tool_receipt", "external_truth_verified": False}


class OutcomeVerifier:
    """Optional host readback after an action, separate from the tool's receipt.

    The callback receives a defensive JSON request containing request_id, tool,
    checked arguments (None if lost during a crash), and the supplied result.
    Only exactly True accepts it. It must read the destination without causing
    another effect. Its identity and independence remain host responsibilities.
    """
    def __init__(self, verify, *, verifier_id):
        # Deferred import avoids middleware -> outcomes -> middleware at import.
        from .middleware import _sync_callable
        _sync_callable(verify)
        nonempty_string(verifier_id, "verifier_id")
        if len(verifier_id) > 128:
            raise ValueError("verifier_id must not exceed 128 characters.")
        self._verify, self.verifier_id = verify, verifier_id

    def check(self, *, request_id, tool, arguments, result):
        from .middleware import _sync_result
        payload = clone({"request_id": request_id, "tool": tool, "arguments": arguments, "result": result})
        if len(encode(payload)) > 256000:
            return self._report("unavailable", "readback_input_too_large")
        try:
            accepted = _sync_result(self._verify(payload))
        except Exception:
            return self._report("unavailable", "readback_failed")
        return self._report("accepted" if accepted is True else "rejected" if accepted is False else "unavailable",
                            None if type(accepted) is bool else "invalid_verifier_result")

    def _report(self, status, reason):
        return {"status": status, "reason": reason, "verifier_id": self.verifier_id,
                "scope": "host_destination_readback", "external_truth_verified": False}
