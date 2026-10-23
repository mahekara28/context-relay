"""Finite spelling equivalence for user preferences, never authorization facts.

Unknown keys/values and non-string JSON retain strict identity. This is not
fuzzy matching, translation, content validation or global case folding.
"""
from __future__ import annotations

_FORMATS = "CSV JSON JSONL TSV XML YAML TOML Markdown HTML PDF TXT XLSX".split()
_LANGUAGES = ("Arabic Bengali Chinese Dutch English French German Hindi Italian "
              "Japanese Korean Marathi Portuguese Russian Spanish Tamil Telugu Turkish Urdu").split()
_VALUES = {
    "export_format": {value.casefold(): value for value in _FORMATS},
    "output_format": {value.casefold(): value for value in _FORMATS},
    "response_language": {value.casefold(): value for value in _LANGUAGES},
    "response_style": {value: value for value in ("short", "brief", "concise", "detailed")},
}


def normalize_memory_value(key: str, value):
    """Canonicalize case/outer whitespace only for a recognized preference enum."""
    if type(value) is not str:
        return value
    return _VALUES.get(key, {}).get(value.strip().casefold(), value)
