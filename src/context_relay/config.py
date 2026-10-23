"""Small, data-only configuration for connecting Relay to an existing agent."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

from .memory import MemoryPolicy
from .output import OutputPolicy


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate configuration key: {key}.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Configuration must not contain {value}.")


@dataclass(frozen=True, slots=True)
class RelayConfig:
    """Immutable settings; loading them never opens a database or connects a model.

    ``max_chars`` is a serialized character budget, not a model token limit.
    Token counters, model callables, credentials, and sessions belong to the host
    application and are deliberately absent from configuration files.
    """

    max_chars: int = 16000
    recent_turns: int = 2
    retrieve_turns: int = 2
    pinned: tuple[int, ...] = ()
    db_path: str = ":memory:"
    name: str = "Connected agent"
    protocol: str = "plain"
    memory: MemoryPolicy | None = None
    output: OutputPolicy | None = None
    busy_timeout_ms: int = 10000

    def __post_init__(self) -> None:
        if type(self.busy_timeout_ms) is not int or not 0 <= self.busy_timeout_ms <= 60000:
            raise ValueError("busy_timeout_ms must be an integer from 0 through 60000.")
        for field, minimum in (("max_chars", 0), ("recent_turns", 1), ("retrieve_turns", 0)):
            value = getattr(self, field)
            if type(value) is not int or value < minimum:
                raise ValueError(f"{field} must be an integer of at least {minimum}.")
        if type(self.pinned) not in (list, tuple):
            raise ValueError("pinned must be a list or tuple of nonnegative integer indices.")
        if any(type(index) is not int or index < 0 for index in self.pinned):
            raise ValueError("pinned must contain only nonnegative integer indices.")
        object.__setattr__(self, "pinned", tuple(self.pinned))
        if type(self.db_path) is not str or not self.db_path.strip() or "\x00" in self.db_path:
            raise ValueError("db_path must be a nonempty string without NUL characters.")
        if type(self.name) is not str or not self.name.strip() or len(self.name) > 200:
            raise ValueError("name must be a nonempty string of at most 200 characters.")
        if type(self.protocol) is not str or self.protocol not in {"plain", "ollama", "openai_chat"}:
            raise ValueError("protocol must be 'plain', 'ollama' or 'openai_chat'.")
        if type(self.memory) is dict:
            try:
                object.__setattr__(self, "memory", MemoryPolicy(**self.memory))
            except TypeError as exc:
                raise ValueError("memory contains unknown or invalid MemoryPolicy fields.") from exc
        if self.memory is not None and not isinstance(self.memory, MemoryPolicy):
            raise ValueError("memory must be a MemoryPolicy, a settings object, or None.")
        if type(self.output) is dict:
            try:
                object.__setattr__(self, "output", OutputPolicy(**self.output))
            except TypeError as exc:
                raise ValueError("output contains unknown or invalid OutputPolicy fields.") from exc
        if self.output is not None:
            if not isinstance(self.output, OutputPolicy):
                raise ValueError("output must be an OutputPolicy, settings object, or None.")
            if self.output.decoder is not None:
                raise ValueError("Configuration is data-only; pass a decoder using connect(output=...) instead.")

    def to_dict(self) -> dict:
        """Return a fresh JSON-compatible copy of these settings."""
        return {
            "max_chars": self.max_chars,
            "recent_turns": self.recent_turns,
            "retrieve_turns": self.retrieve_turns,
            "pinned": list(self.pinned),
            "db_path": self.db_path,
            "busy_timeout_ms": self.busy_timeout_ms,
            "name": self.name,
            "protocol": self.protocol,
            "memory": self.memory.to_dict() if self.memory is not None else None,
            "output": self.output.to_dict() if self.output is not None else None,
        }

    @classmethod
    def from_file(cls, path: str | Path) -> RelayConfig:
        """Read strict UTF-8 JSON and resolve relative database paths beside it.

        No environment variables or home-directory shorthand are expanded.
        Unknown fields and duplicate JSON keys fail instead of being ignored.
        """
        config_path = Path(path).resolve()
        data = json.loads(
            config_path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
        if type(data) is not dict:
            raise ValueError("Configuration must be a JSON object.")
        unknown = set(data) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown configuration fields: {', '.join(sorted(unknown))}.")
        config = cls(**data)
        if config.db_path != ":memory:":
            database_path = Path(config.db_path)
            if not database_path.is_absolute():
                data["db_path"] = str((config_path.parent / database_path).resolve())
                config = cls(**data)
        return config
