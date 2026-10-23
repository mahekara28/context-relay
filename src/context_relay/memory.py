"""Bounded local recall of explicit user statements, never policy authority.

No LLM, embedding service or predefined field schema is needed by default.
Recognition is intentionally narrow: complete statements and explicit setters.
An optional interpreter may propose additional recall for explicit review.
The append-only transcript cursor is durable, including dismissed or forgotten
statements, so replaying a conversation never silently brings them back.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .evidence import EvidenceArchive
from .interpretation import MemoryInterpretationError, MemoryInterpreter
from .memory_values import normalize_memory_value
from .memory_plan import plan_corrections
from .memory_languages import plan_multilingual
from .memory_scope import is_continuation, partition_scopes, quoted_literal, quoted_spans, reported_quote, scalar_quotes, scope_marker
from .models import ConflictError, clone, encode, nonempty_string, validate_revision
from .store import RelayStore


class MemoryHistoryError(ValueError):
    """The original conversation is not an extension of the consumed prefix."""


class MemoryBudgetExceeded(ValueError):
    """A memory or extraction cap would be exceeded; no batch was committed."""


class MemoryConflictError(ValueError):
    """A reviewed or undoable change no longer matches its captured state."""


@dataclass(frozen=True, slots=True)
class MemoryPolicy:
    """Explicit opt-in settings for schema-free user-stated recall.

    ``max_entries`` and ``max_chars`` count accepted and pending memory together.
    The character budget counts compact JSON (including provenance), not tokens.
    Raw source messages remain in the local database, outside the model snapshot.
    Input caps abort an entire batch rather than silently skipping its tail.
    """

    mode: str = "auto"
    max_entries: int = 64
    max_chars: int = 4000
    max_message_chars: int = 16000
    max_new_messages: int = 256
    max_history_chars: int = 2000000
    pause_on_uncertainty: bool = True
    assurance_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.mode) is not str or self.mode not in {"auto", "review"}:
            raise ValueError("Memory mode must be 'auto' or 'review'.")
        if type(self.pause_on_uncertainty) is not bool:
            raise ValueError("pause_on_uncertainty must be a boolean.")
        fields = self.assurance_fields
        if (type(fields) not in (tuple, list) or len(fields) > 64
                or any(type(key) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key) for key in fields)
                or len(set(fields)) != len(fields)):
            raise ValueError("assurance_fields needs at most 64 unique memory keys.")
        object.__setattr__(self, "assurance_fields", tuple(sorted(fields)))
        limits = {
            "max_entries": 10000, "max_chars": 2000000,
            "max_message_chars": 2000000, "max_new_messages": 100000,
            "max_history_chars": 20000000,
        }
        for name, ceiling in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"{name} must be an integer from 1 to {ceiling}.")

    def to_dict(self) -> dict:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "assurance_fields"}
        if self.assurance_fields:
            result["assurance_fields"] = list(self.assurance_fields)
        return result


_CAUTION = re.compile(
    r"\b(?:if|unless|hypothetical|hypothetically|imagine|suppose|supposing|example|"
    r"maybe|perhaps|possibly|might|could|would|should|not|no|never|don't|doesn't|"
    r"isn't|wasn't|can't|cannot|won't|said|says|wrote|quoted|according)\b",
    re.IGNORECASE,
)
_COMPOUND = re.compile(r"\b(?:and|or|but|because|although|while|except|until)\b", re.IGNORECASE)
_SECRET = re.compile(r"\b(?:password|passphrase|secret|credential|credentials|private[ _-]+key|api[ _-]+key|access[ _-]+token)\b", re.IGNORECASE)
_FRAMING = re.compile(r"(?:^|[\r\n])[^\S\r\n]*(?:translate|summari[sz]e|rewrite|analy[sz]e|classify|quote|draft|write|fictional|sample|for\s+testing|here\s+is|the\s+(?:document|following)|(?:the|this)\s+(?:example|sample|quotation|test\s+fixture)\s+(?:is|says|contains|reads))\b", re.IGNORECASE)
_SUBJECT = r"[A-Za-z][A-Za-z0-9 _-]{0,79}"
# Values are stripped at extraction; do not let separator runs become values.
_VALUE = r"[^\s;!?][^\n\r;!?]{0,255}?"
_COURTESY_WORDS = r"thanks|thank\s+you|ok(?:ay)?"
_COURTESY = re.compile(rf"(?:{_COURTESY_WORDS}|please)[.!]?", re.IGNORECASE)
# Complete acknowledgments only: never remove an arbitrary explanation, condition,
# reported statement or trailing request. These clauses carry no setting value.
_ACKNOWLEDGMENT = re.compile(
    r"(?:thanks|thank\s+you)(?:\s+for\s+(?:helping|checking|clarifying|your\s+help|the\s+help)|"
    r",?\s+(?:this|that|it)\s+(?:helps|will\s+help|is\s+helpful))[.!]?", re.IGNORECASE)
_REPORTED_SUFFIX = re.compile(
    r"(?:these|those|the\s+(?:above|preceding)(?:\s+(?:statements|instructions|settings))?)\s+"
    r"(?:are|were)\s+(?:(?:just|only)\s+)?(?:examples?|samples?|sample\s+settings|illustrative\s+values)[.!]?",
    re.IGNORECASE)
_MATERIAL_PREFIX = re.compile(
    r"\b(?:demo|examples?|samples?|illustrative|documentation|document|fixtures?|quotations?|"
    r"transcripts?|snippets?|copied|pasted|following|above|below|claims?|said|says|sent)\b", re.IGNORECASE)
_PREFIX = re.compile(rf"^(?:(?:{_COURTESY_WORDS})[, ]+)?(?:(actually)[, ]+)?(?:please\s+)?(?:remember\s+(?:that\s+)?)?", re.IGNORECASE)
_STYLE = r"short|brief|concise|detailed"
_STYLE_PHRASE = rf"{_STYLE}|to[ -]the[ -]point"
_REPLIES = r"(?:(?:all\s+)?(?:future|later)\s+)?(?:answers?|responses?|replies|reply)"
_LANGUAGE = r"Arabic|Bengali|Chinese|Dutch|English|French|German|Hindi|Italian|Japanese|Korean|Marathi|Portuguese|Russian|Spanish|Tamil|Telugu|Turkish|Urdu"
_FORMAT = r"CSV|JSON|JSONL|TSV|XML|YAML|TOML|Markdown|HTML|PDF|TXT|XLSX"
_FINITE_SETTINGS = {"export_format": _FORMAT, "output_format": _FORMAT,
                    "response_language": _LANGUAGE, "response_style": _STYLE}
_SETTING_SUBJECTS = {
    "export_format": r"export[ _-]+(?:format|preference)|exports?",
    "output_format": r"output[ _-]+format",
    "response_language": r"(?:response|reply)[ _-]+language",
    "response_style": r"(?:response|reply)[ _-]+style",
}
_OUTPUT_ALIAS = r"output|results|data"
_INTERPRETABLE = re.compile(r"(?:I\s+(?:want|need|like|prefer)|make|keep|use|switch|change|set|update|reply|respond|answer|export|give|send|return|be|replace|choose|leave|move|shift|adjust|have|go\s+with|stick\s+to)\b", re.IGNORECASE)
_FUTURE = r"(?:from\s+(?:now|here)\s+on|from\s+this\s+point\s+(?:on|onward|onwards)|as\s+of\s+now|going\s+forward|in\s+(?:the\s+)?future|permanently)"
_RECONSIDERED = r"I(?:'ve|\s+have)?\s+(?:reconsidered|rethought)"
_POLITE_REQUEST = re.compile(r"^(?:can|could|would|will)\s+(?:you|we)\s+(?:please\s+)?", re.IGNORECASE)
_CORRECTION_PREFIX = re.compile(r"^(?:on\s+second\s+thought|I(?:'ve|\s+have)?\s+changed\s+my\s+mind|scratch\s+that)[,:\s\u2014-]+", re.IGNORECASE)
_LOCAL_SCOPE = re.compile(r"\b(?:for\s+(?:now|today|(?:this|the\s+next)\s+(?:(?:single|one)\s+)?(?:reply|response|answer|export|request|task|session|download|run|turn|message|occasion|report|document|file|job|workflow))|(?:only|just)\s+(?:this|today)|this\s+time|today\s+only)\b", re.IGNORECASE)
_INTERPRETER_FRAME = re.compile(
    r"\b(?:when|whenever|once|after|until|during|temporar(?:y|ily)|assuming|provided|reports?|reported|states?|claims?|"
    r"told|asked|requested|example|sample|translate|summari[sz]e|rewrite|quoted|quotation|"
    r"agar|yadi|kaha|bola|shayad|anuvad|traduce|traduis|dijo|dice|selon|según)\b|"
    r"(?:^|[,;])\s*before\b|(?:अगर|यदि|कहा|बोला|शायद|अनुवाद|उदाहरण)", re.I)
_UNSPECIFIED = re.compile(r"\b(?:whatever|something|anything|whichever)\b|^(?:the|that|some)\s+(?:other|different)\b", re.IGNORECASE)
_STYLE_WORDS = {"briefly": "brief", "concisely": "concise", "succinctly": "concise",
                "in detail": "detailed", "to the point": "concise"}


def _setting_target(subject: str) -> str:
    return rf"(?:(?:the|your|our|my)\s+)?(?:(?:all|every)\s+)?(?:(?:default|future|later|current|preferred)\s+)?(?:{subject})(?:\s+preference)?"


def _setting_patterns(subject: str, values: str):
    """Shared full-clause grammar; values remain bounded per named setting."""
    target = _setting_target(subject)
    value = rf"(?P<value>{values})"
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
        rf"(?:(?:I\s+(?:want|need|prefer)|have)\s+)?{target}\s*(?:\s(?:in|as|should\s+(?:be|become)|needs?\s+to\s+(?:be|become)|must\s+(?:be|become))\s+|[=:]\s*){value}",
        rf"(?:(?:use|keep|choose|go\s+with|stick\s+to)\s+)?{value}\s+(?:for|as)\s+{target}",
        rf"{target}\s+(?:should|must|need\s+to)\s+(?:come|be\s+provided)\s+(?:in|as)\s+{value}",
        rf"(?:keep|leave)\s+{target}\s+(?:in|as)\s+{value}",
        rf"make\s+{value}\s+(?:(?:the|our)\s+)?default\s+(?:for|as)\s+{target}",
        rf"for\s+{target},?\s+(?:(?:let's|let\s+us)\s+)?(?:(?:use|choose|go\s+with)\s+{value}|make\s+(?P<default>{values})\s+(?:(?:the|our)\s+)?default)",
        rf"make\s+{value}\s+(?:(?:the|our|my)\s+)?default\s+(?:{subject})",
        rf"make\s+{target}\s+{value}",
        rf"(?:switch|change|move|set|update)\s+{target}\s*(?:(?:over\s+)?to\s+|:\s*){value}",
        rf"(?:switch|change|move)\s+{target}\s+from\s+(?P<old>{values})\s+to\s+{value}",
        rf"(?:my|our)\s+preference\s+(?:for|about)\s+{target}\s+is\s+{value}",
        rf"{value}\s+is\s+{target}\s+I\s+(?:want|need|prefer)",
        rf"for\s+{target}(?:\s+{_FUTURE})?,?\s+{value}\s+is\s+what\s+I\s+(?:want|need|prefer)",
        rf"{_RECONSIDERED}\s+{target}\s*[;.]\s*(?:please\s+)?(?:use|choose|go\s+with)\s+{value}",
        rf"replace\s+(?P<old>{values})\s+with\s+{value}\s+(?:for|as)\s+{target}",
        rf"(?:instead\s+of|rather\s+than)\s+(?P<old>{values})\s+for\s+{target},?\s+(?:please\s+)?(?:use|choose|go\s+with)\s+{value}",
        rf"(?:use|choose|keep)\s+{value}\s+(?:for|as)\s+{target},?\s+(?:not|instead\s+of|rather\s+than)\s+(?P<old>{values})",
        rf"(?:use|choose|keep)\s+{value}\s+(?:instead\s+of|rather\s+than)\s+(?P<old>{values})\s+(?:for|as)\s+{target}",
    ))


_SETTING_PATTERNS = (
    (_setting_patterns(_SETTING_SUBJECTS["export_format"], _FORMAT), "export_format"),
    (_setting_patterns(_SETTING_SUBJECTS["output_format"], _FORMAT), "output_format"),
    (_setting_patterns(r"(?P<format_subject>output|results|data)", _FORMAT), "export_format"),
    (_setting_patterns(rf"{_SETTING_SUBJECTS['response_language']}|{_REPLIES}", _LANGUAGE), "response_language"),
    (_setting_patterns(rf"{_SETTING_SUBJECTS['response_style']}|{_REPLIES}", _STYLE_PHRASE), "response_style"),
)


def _named_setter(prefix: str, values: str, suffix: str = "") -> re.Pattern:
    return re.compile(rf"{prefix}(?P<value>{values}){suffix}(?:\s+instead\s+of\s+(?P<old>{values}){suffix})?", re.IGNORECASE)


_NAMED_SETTERS = (
    (_named_setter(r"(?:I\s+prefer|use)\s+", _STYLE, rf"\s+(?:{_REPLIES})\s+for\s+(?:the\s+)?response\s+style"), "response_style"),
    (_named_setter(r"(?:I\s+(?:want|need|prefer)|(?:give|send)\s+me|use|choose)\s+(?:a\s+)?", _STYLE_PHRASE, rf"\s+(?:{_REPLIES})"), "response_style"),
    (_named_setter(rf"(?:keep|make)\s+(?:(?:the|your)\s+)?(?:{_REPLIES})\s+", _STYLE_PHRASE), "response_style"),
    (_named_setter(rf"I\s+(?:want|need|prefer)\s+(?:the\s+)?(?:{_REPLIES})\s+to\s+be\s+", _STYLE_PHRASE), "response_style"),
    (_named_setter(r"be\s+", _STYLE_PHRASE, rf"\s+in\s+(?:(?:the|your)\s+)?(?:{_REPLIES})"), "response_style"),
    (_named_setter(r"(?:answer|reply|respond)\s+", r"briefly|concisely|succinctly|in\s+detail"), "response_style"),
    (_named_setter(r"(?:(?:answer|reply|respond)(?:\s+(?:to\s+)?me)?|keep\s+(?:answering|replying|responding))\s+in\s+", _LANGUAGE), "response_language"),
    (_named_setter(rf"I\s+(?:want|need|prefer)\s+(?:the\s+)?(?:{_REPLIES})\s+(?:to\s+be\s+)?in\s+", _LANGUAGE), "response_language"),
    (_named_setter(r"use\s+", _LANGUAGE, rf"\s+for\s+(?:(?:the|your)\s+)?(?:{_REPLIES})"), "response_language"),
    (_named_setter(r"switch\s+(?:the\s+)?response\s+language\s+to\s+", _LANGUAGE), "response_language"),
    (_named_setter(r"export(?:\s+(?:the\s+)?(?:results|data))?\s+(?:(?:as|in)\s+)?", _FORMAT), "export_format"),
    (_named_setter(r"(?:I\s+(?:want|need|prefer)|(?:give|send|return)(?:\s+me)?)\s+(?:the\s+)?(?P<format_subject>output|exports?|results|data)\s+(?:in|as)\s+", _FORMAT), "export_format"),
    (_named_setter(r"switch\s+(?:the\s+)?(?:export\s+format|exports?)\s+to\s+", _FORMAT), "export_format"),
)
_CONTEXTUAL_PREFIX = r"(?:(?:use|switch\s+to|(?:switch|change|set)\s+(?:it|them|this|that)\s+to)\s+)?"
_CONTEXTUAL = (
    (re.compile(rf"(?:(?:make|keep)\s+(?:it|them)\s+)?(?P<value>{_STYLE}|(?:a\s+bit\s+)?shorter|longer|more\s+detailed|less\s+verbose)", re.IGNORECASE), ("response_style",)),
    (re.compile(rf"{_CONTEXTUAL_PREFIX}(?P<value>{_LANGUAGE})(?:\s+instead)?", re.IGNORECASE), ("response_language",)),
    (re.compile(rf"{_CONTEXTUAL_PREFIX}(?P<value>{_FORMAT})(?:\s+instead)?", re.IGNORECASE), ("export_format", "output_format")),
)
_DIRECT_PATTERNS = (
    (re.compile(rf"use\s+(?P<value>{_VALUE})\s+for\s+(?P<subject>{_SUBJECT})", re.IGNORECASE), True),
    (re.compile(rf"(?:set|change)\s+(?P<subject>{_SUBJECT})\s+to\s+(?P<value>{_VALUE})", re.IGNORECASE), True),
    (re.compile(rf"(?P<subject>(?:the|my|our)\s+{_SUBJECT})\s+is\s+(?P<value>{_VALUE})", re.IGNORECASE), False),
    (re.compile(rf"I\s+prefer\s+(?P<value>{_VALUE})\s+for\s+(?P<subject>{_SUBJECT})", re.IGNORECASE), True),
    (re.compile(rf"leave\s+(?P<subject>{_SUBJECT})\s+as\s+(?P<value>{_VALUE})", re.IGNORECASE), True),
)


def _digest(value: object) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def _initial() -> dict:
    return {"schema": 1, "protocol": None, "processed_count": 0,
            "prefix_hash": _digest([]), "entries": {}, "pending": {}}


def _subject_key(subject: str) -> str:
    for key, pattern in _SETTING_SUBJECTS.items():
        if re.fullmatch(_setting_target(pattern), subject.strip(), re.IGNORECASE):
            return key
    if re.fullmatch(_setting_target(_OUTPUT_ALIAS), subject.strip(), re.IGNORECASE):
        return "export_format"
    subject = re.sub(r"^the\s+", "", subject.strip(), flags=re.IGNORECASE)
    key = re.sub(r"[\s_-]+", "_", subject.casefold())
    owned = re.sub(r"^(?:my|our|your)_", "", key)
    return owned if owned in _FINITE_SETTINGS else key


def _request_body(text: str) -> tuple[str, bool]:
    """Remove only fixed request wrappers; original quotes/offsets stay intact."""
    text = text.replace("\u2019", "'")
    explicit = bool(_CORRECTION_PREFIX.match(text))
    text = _CORRECTION_PREFIX.sub("", text)
    text = re.sub(rf"^{_FUTURE},?\s+", "", text, flags=re.IGNORECASE)
    prefix = _PREFIX.match(text)
    assert prefix is not None
    explicit = explicit or bool(prefix.group(1))
    text = text[prefix.end():]
    text = _POLITE_REQUEST.sub("", text)
    text = re.sub(r"^(?:I'd|I\s+would)\s+like\s+", "I want ", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:I'd|I\s+would)\s+prefer\s+", "I prefer ", text, flags=re.IGNORECASE)
    text = re.sub(r"^let(?:'s|\s+us)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r",\s*please$", "", text, flags=re.IGNORECASE)
    text = re.sub(rf"(?<!\s)(?:,\s+|\s+(?:,\s+)?){_FUTURE}$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^I\s+(?:want|need)\s+you\s+to\s+", "", text, flags=re.IGNORECASE)
    return text, explicit


def _polite_question(text: str) -> bool:
    text = _CORRECTION_PREFIX.sub("", text)
    text = re.sub(rf"^{_FUTURE},?\s+", "", text, flags=re.IGNORECASE)
    return bool(_POLITE_REQUEST.match(_PREFIX.sub("", text, count=1)))


def _extract(statement: str) -> dict | None:
    """Only complete single-clause patterns; no free semantic inference."""
    literal = quoted_literal(statement)
    if literal is not None:
        masked, value = literal
        candidate = _extract(masked)
        if candidate is not None and candidate["value"] == "v" and not candidate.get("contextual"):
            key = candidate["key"]
            unfamiliar = key in _FINITE_SETTINGS and not re.fullmatch(_FINITE_SETTINGS[key], value, re.I)
            if not unfamiliar or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+-]{0,63}", value):
                return candidate | {"value": value, "unfamiliar": unfamiliar}
    text = statement.strip().rstrip(".;").strip()
    if text.endswith("?"):
        if not _polite_question(text):
            return None
        text = text[:-1].strip()
    if not text or _LOCAL_SCOPE.search(text):
        return None
    text, explicit = _request_body(text)
    # "should be" is accepted only by the complete named-setting grammar.
    # Other caution words are checked before any generic setter can run.
    if not _COMPOUND.search(text):
        for patterns, key in _SETTING_PATTERNS:
            for pattern in patterns:
                match = pattern.fullmatch(text)
                if match:
                    fields = match.groupdict()
                    candidate = {"key": key, "value": fields.get("value") or fields["default"],
                                 "old": fields.get("old"), "explicit": True}
                    if key == "response_style":
                        for name in ("value", "old"):
                            if candidate[name] is not None:
                                canonical = " ".join(candidate[name].casefold().replace("-", " ").split())
                                candidate[name] = _STYLE_WORDS.get(canonical, candidate[name])
                    if fields.get("format_subject"):
                        candidate["aliases"] = ["export_format", "output_format"]
                    return candidate
    if _CAUTION.search(text) or _COMPOUND.search(text):
        return None
    for pattern, key in _NAMED_SETTERS:
        match = pattern.fullmatch(text)
        if match:
            candidate = {"key": key, "value": match["value"],
                         "old": match["old"], "explicit": True}
            if key == "response_style":
                for field in ("value", "old"):
                    value = candidate[field]
                    if value is not None:
                        canonical = " ".join(value.casefold().replace("-", " ").split())
                        candidate[field] = _STYLE_WORDS.get(canonical, value)
            if match.groupdict().get("format_subject", "").casefold() in {"output", "results", "data"}:
                # Existing explicit custom fields retain their names. "Output"
                # may refer to either; never overwrite both or choose by recency.
                candidate["aliases"] = ["export_format", "output_format"]
            return candidate
    for pattern, targets in _CONTEXTUAL:
        match = pattern.fullmatch(text)
        if match:
            return {"key": targets[0], "value": match["value"], "old": None,
                    "explicit": False, "contextual": True, "context_targets": list(targets)}
    match = re.fullmatch(rf"use\s+(?P<value>{_VALUE})\s+instead\s+of\s+(?P<old>{_VALUE})", text, re.IGNORECASE)
    if match:
        return {"key": None, "value": match["value"].strip(),
                "old": match["old"].strip(), "explicit": True}
    for pattern, is_explicit in _DIRECT_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            subject, value = match["subject"].strip(), match["value"].strip()
            if not value or _SECRET.search(subject) or _UNSPECIFIED.search(value):
                return None
            if re.fullmatch(rf"(?:(?:the|your)\s+)?(?:{_REPLIES})|it|them|(?:this|that|these|those)(?:\s+ones?)?", subject, re.IGNORECASE):
                # A pronoun or unspecified reply is not a named setting. Only
                # the state-aware contextual path can suggest such a target.
                return None
            # A verb hidden in the subject makes this reported/compound prose,
            # not a direct field statement (e.g. "the report states ...").
            if re.search(r"\b(?:is|are|was|were|says|said|states|claims|thinks|told|from)\b", subject, re.IGNORECASE):
                return None
            if not is_explicit and (
                re.search(r"\b(?:for|about|regarding|which|that)\b", subject, re.IGNORECASE)
                or re.fullmatch(r"(?:the|my|our)\s+(?:following|example|sample|(?:test\s+)?fixture|quote|quotation|excerpt|snippet|sentence|statement|paragraph|transcript|document|instruction|request)", subject, re.IGNORECASE)
                or re.fullmatch(r"(?:the|my|our)\s+(?:existing|saved|recorded|current|old)\s+(?:entry|value|setting|preference)", subject, re.IGNORECASE)
            ):
                # Descriptions of preferences or quoted material are not the
                # literal name of a setting. Explicit custom setters still work.
                return None
            key = _subject_key(subject)
            unfamiliar = key in _FINITE_SETTINGS and not re.fullmatch(_FINITE_SETTINGS[key], value, re.IGNORECASE)
            if unfamiliar and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+-]{0,63}", value):
                return None
            candidate = {"key": key, "value": value, "old": None,
                         "explicit": is_explicit or explicit, "unfamiliar": unfamiliar}
            if re.fullmatch(_setting_target(_OUTPUT_ALIAS), subject, re.IGNORECASE):
                candidate["aliases"] = ["export_format", "output_format"]
            return candidate
    return None


def _statements(text: str, *, split_clauses: bool = True):
    """Yield exact spans; avoid periods within domains, versions and numbers."""
    start = 0
    quoted = quoted_spans(text) or []
    boundary_pattern = r"[.!?;।](?=\s|$)|\n" + (r"|(?<!\s)(?:,\s+|\s+(?:,\s+)?)and\s+" if split_clauses else "")
    for boundary in re.finditer(boundary_pattern, text, re.IGNORECASE):
        if any(left <= boundary.start() < right for left, right in quoted):
            continue
        # Conjunctions delimit complete clauses but are not part of either quote.
        end = boundary.start() if "and" in boundary.group().casefold() else boundary.end()
        left, right = start, end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right:
            yield left, right, text[left:right]
        start = boundary.end()
    if start < len(text):
        left, right = start, len(text)
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right:
            yield left, right, text[left:right]


def _quoted_instructions(text: str) -> str | None:
    """Keep literal setters, omit a bounded reported quote, never parse its text."""
    spans = quoted_spans(text)
    if spans is None:
        return None
    if not spans:
        return scalar_quotes(text)
    characters = list(text)
    for start, end, clause in _statements(text):
        clean = scalar_quotes(clause)
        if reported_quote(clause):
            characters[start:end] = " " * (end - start)
        elif clean is not None:
            characters[start:end] = clean
        elif quoted_literal(clause) is None or _extract(clause) is None:
            return None
    return "".join(characters)


def _without_acknowledgments(text: str) -> str:
    """Mask bounded acknowledgments without changing source evidence offsets.

    Run only after the whole-message quote/framing checks. This affects the
    deterministic grammar, not eligibility for the optional interpreter.
    """
    characters = list(text)
    for start, end, quote in _statements(text, split_clauses=False):
        if _ACKNOWLEDGMENT.fullmatch(quote):
            characters[start:end] = " " * (end - start)
    return "".join(characters)


def _candidate_targets(candidates, entries) -> set[str]:
    """Existing targets implicated by an incomplete message, never new facts."""
    targets = set()
    for _, _, _, candidate in candidates:
        if candidate.get("aliases"):
            targets.update(key for key in candidate["aliases"] if key in entries)
        elif candidate["key"] is not None:
            if candidate["key"] in entries:
                targets.add(candidate["key"])
        elif candidate.get("old") is not None:
            targets.update(key for key, entry in entries.items()
                           if entry["value"].casefold() == candidate["old"].casefold())
    return targets


def _can_interpret(text: str, *, review_unmatched: bool = False) -> bool:
    """Only direct user requests can reach the optional interpretation callback."""
    if _SECRET.search(text) or scope_marker(text, _LOCAL_SCOPE) or _FRAMING.search(text):
        return False
    normalized = scalar_quotes(text)
    if normalized is None:
        return False
    eligible = _without_acknowledgments(normalized) if review_unmatched else normalized
    statements = [quote for _, _, quote in _statements(eligible, split_clauses=False)
                  if not _COURTESY.fullmatch(quote)]
    for quote in statements:
        raw = quote.rstrip(".;").strip()
        if raw.endswith("?"):
            if not _polite_question(raw):
                return False
            raw = raw[:-1].strip()
        body, _ = _request_body(raw)
        if (_CAUTION.search(body) or _INTERPRETER_FRAME.search(body) or ":" in body
                or not review_unmatched and not _INTERPRETABLE.match(body)):
            return False
    return bool(statements)


def _conflicting_targets(candidates, entries) -> set[str]:
    """A message cannot silently assign different values to the same target."""
    choices = {}
    for _, _, _, candidate in candidates:
        key = candidate["key"]
        targets = candidate.get("aliases", [key])
        if candidate.get("aliases"):
            targets = [target for target in targets if target in entries] or [key]
        if key is None:
            targets = [target for target, entry in entries.items()
                       if entry["value"].casefold() == candidate["old"].casefold()]
        for target in targets:
            choices.setdefault(target, set()).add(normalize_memory_value(target, candidate["value"]))
    return {key for key, values in choices.items() if len(values) > 1}


def _scope_default(text: str) -> bool:
    """A contrast may follow a complete preference, never discard its framing."""
    text = re.sub(r"\s+(?:normally|by\s+default)$", "", text.strip(), flags=re.I)
    text = re.sub(r"^(?:keep|continue)\s+using\s+", "use ", text, flags=re.I)
    candidate = _extract(text)
    return bool(candidate and candidate["key"] in _FINITE_SETTINGS
                and not candidate.get("unfamiliar"))


def _request_targets(body: str, patterns: dict[str, str]) -> set[str]:
    """Resolve grammatical subjects, never settings mentioned inside a value.

    Prefer the longest existing subject: ``timeout for exports`` is not an
    instruction to change ``export_format``. Equal aliases remain ambiguous.
    """
    targets, allow_bare = set(), True
    for clause in re.split(r"(?<!\s)(?:,\s+|\s+(?:,\s+)?)and\s+", body, flags=re.IGNORECASE):
        clause, _ = _request_body(clause.strip())
        value_first = re.fullmatch(r"(?:use|choose|keep|go\s+with|stick\s+to|I\s+prefer)\s+\S.*?(?<!\s)\s+for\s+(.+)", clause, re.IGNORECASE)
        if value_first:
            subject = value_first[1]
        else:
            subject = re.sub(r"^(?:(?:I\s+(?:want|need|prefer))|make|keep|switch|change|set|replace|choose|leave|move|shift|adjust|update|have|(?:give|send|return)(?:\s+me)?)\s+", "", clause, flags=re.IGNORECASE)
        if not allow_bare and subject == clause:
            continue
        subject = re.sub(r"^both\s+", "", subject, flags=re.IGNORECASE)
        matches = [(key, re.match(rf"{_setting_target(pattern)}(?=$|[\s,:])", subject, re.IGNORECASE))
                   for key, pattern in patterns.items()]
        longest = max((match.end() for _, match in matches if match), default=0)
        tail = subject[longest:].strip()
        allow_bare = bool(longest) and not tail
        # A known setting cannot merely be a prefix of an unknown compound
        # subject, e.g. "export format documentation" or "response style guide".
        if not tail or re.match(r"(?:[,:]|(?:to|from|with|as|in|by|into|more|less|much|slightly|easier|harder|shorter|longer|better|clearer|simpler|(?:machine|human)[ -]readable|a\s+(?:little|bit))\b)", tail, re.IGNORECASE):
            targets.update(key for key, match in matches if match and match.end() == longest)
    return targets


def _uncertain_targets(text: str, state: dict) -> list[str]:
    """Keep known targets disputed when a direct change supplies no clear value."""
    if _SECRET.search(text) or _LOCAL_SCOPE.search(text):
        return []
    aliases = dict(_SETTING_SUBJECTS)
    for key in ("export_format", "output_format"):
        aliases[key] += "|" + _OUTPUT_ALIAS
    aliases["response_language"] += rf"|language\s+(?:of|for)\s+(?:(?:the|your)\s+)?(?:{_REPLIES})|(?:{_REPLIES})\s+(?:to\s+be\s+)?(?:mostly\s+|mainly\s+)?in\b|(?:reply|respond|answer)(?:ing)?\s+(?:mostly\s+|mainly\s+)?in\b"
    aliases["response_style"] += rf"|(?:response|reply|answer)[ _-]+(?:length|tone)|(?:{_REPLIES})\s+(?:to\s+be\s+)?(?:shorter|longer|terse|verbose|brief|concise|detailed)"
    patterns = {key: aliases.get(key, r"[ _-]+".join(re.escape(part) for part in key.split("_")))
                for key in state["entries"]}
    targets, prior_change = set(), set()
    rejection = r"(?:no\s+longer\s+(?:works(?:\s+for\s+me)?|suits\s+me)|is\s+no\s+longer\s+(?:suitable|correct|appropriate)(?:\s+for\s+me)?)"
    deferral = r"I(?:'ll|\s+will)\s+(?:explain|specify|clarify|provide|confirm)\s+(?:(?:the|my|our|a)\s+)?(?:replacement|new\s+(?:value|setting|preference)|choice)\s+(?:shortly|later|soon)"
    for _, _, quote in _statements(text, split_clauses=False):
        if _COURTESY.fullmatch(quote):
            continue
        body, _ = _request_body(quote.rstrip(".;").strip())
        if _can_interpret(quote):
            named = _request_targets(body.rstrip("?"), patterns)
            targets.update(named)
            prior_change = named if re.match(r"(?:switch|change|set|replace|move|shift|adjust|update)\b", body, re.IGNORECASE) else set()
            continue
        declared, changed = set(), set()
        for key, pattern in patterns.items():
            target = _setting_target(pattern)
            match = re.fullmatch(rf"{target}\s+(?:is|should\s+be|needs?\s+to\s+be|must\s+be|(?:should|must|need\s+to)\s+(?:come|be\s+provided)\s+(?:in|as))\s+(?P<value>{_VALUE})", body, re.IGNORECASE)
            if match and not _CAUTION.search(match["value"]):
                declared.add(key)
            if re.fullmatch(rf"(?:{target}\s+(?:has\s+changed|{rejection})|{_RECONSIDERED}\s+{target})", body, re.IGNORECASE):
                changed.add(key)
        if declared or changed:
            targets.update(declared | changed)
            prior_change = changed
        elif prior_change and re.fullmatch(rf"(?:{deferral}|(?:the|my|our)\s+(?:old|current|previous)\s+(?:one|value|setting|choice)\s+{rejection})", body, re.IGNORECASE):
            # Negation is scoped to rejecting an old setting, never stripped
            # globally from a command. A missing replacement stays unknown.
            continue
        else:
            return []
    return sorted(targets)


def _contextual_target(candidate: dict, state: dict, history: list[dict], index: int) -> str | None:
    """A narrow candidate for review, never unrestricted pronoun resolution."""
    previous = next((i for i in range(index - 1, -1, -1) if history[i]["role"] == "user"), None)
    if previous is None:
        return None
    message = history[previous]
    statements = [quote for _, _, quote in _statements(message["content"]) if not _COURTESY.fullmatch(quote)]
    if len(statements) != 1:
        return None
    antecedent = _extract(statements[0])
    if not antecedent or antecedent.get("contextual"):
        return None
    keys = [key for key in antecedent.get("aliases", [antecedent["key"]]) if key in state["entries"]]
    if len(keys) != 1 or keys[0] not in candidate["context_targets"]:
        return None
    key = keys[0]
    entry = state["entries"][key]
    if (entry["source"].get("session_id") is not None
            or entry["value"].casefold() != antecedent["value"].casefold()
            or any(key in proposal["targets"] for proposal in state["pending"].values())):
        return None
    return key


def _compact_entry(entry: dict) -> dict:
    return {"key": entry["key"], "value": entry["value"],
            "source": {key: value for key, value in entry["source"].items() if key != "text"}}


def _snapshot(state: dict) -> dict:
    disputed = {key for proposal in state["pending"].values() for key in proposal["targets"]}
    entries = []
    for key in sorted(state["entries"]):
        entry = _compact_entry(state["entries"][key])
        entry["disputed"] = key in disputed
        entries.append(entry)
    return {"kind": "user_stated_memory", "current_authority": False,
            "trusted_instruction": False,
            "entries": entries, "pending_count": len(state["pending"]),
            "unresolved": [_compact_pending(proposal) for proposal in state["pending"].values()]}


def _effective_snapshot(state: dict, session_id: str | None = None) -> dict:
    """Overlay preference-only current-turn values; standing memory stays intact."""
    result = _snapshot(state)
    entries = {entry["key"]: entry for entry in result["entries"]}
    for proposal in state["pending"].values():
        key = proposal["key"]
        if key not in entries and key in _FINITE_SETTINGS and proposal["reason"] == "unparsed_correction":
            entries[key] = {"key": key, "value": None, "disputed": True,
                            "source": _compact_pending(proposal)["source"]}
    scope = state.get("turn_scope") or state.get("previous_turn_scope")
    if scope is None:
        result["entries"] = [entries[key] for key in sorted(entries)]
        return _assured_snapshot(state, result, session_id)
    previous = "turn_scope" not in state
    scoped = scope["entries"]
    for key, record in scoped.items():
        entry = _compact_entry(record) | {"scope": "turn", "disputed": record["disputed"] or previous}
        if previous:
            entry["value"] = None
        # A current exception cannot silently resolve an unrelated durable dispute.
        entry["disputed"] = entry["disputed"] or entries.get(key, {}).get("disputed", False)
        entries[key] = entry
        if record["disputed"] or previous:
            result["unresolved"].append({"key": key, "value": None, "targets": [key],
                "reason": "unparsed_turn_correction", "scope": "turn", "source": entry["source"]})
    result["entries"] = [entries[key] for key in sorted(entries)]
    result["pending_count"] = len(result["unresolved"])
    result["turn_scope"] = {key: scope[key] for key in ("index", "message_sha256")}
    if previous:
        result["turn_scope"]["awaiting_followup"] = True
    return _assured_snapshot(state, result, session_id)


def _review_values(state):
    """Underlying suggestions, without treating unreviewed values as approved."""
    raw = _effective_snapshot({key: value for key, value in state.items() if key != "assurance"})
    entries = {entry["key"]: entry for entry in raw["entries"]}
    return [{"key": key, "value": entries.get(key, {}).get("value"),
             "scope": entries.get(key, {}).get("scope", "durable"),
             "disputed": entries.get(key, {}).get("disputed", False)}
            for key in state["assurance"]["fields"]]


def _assured_snapshot(state, snapshot, session_id):
    assurance = state.get("assurance")
    if assurance is None:
        return snapshot
    source = assurance["user"]
    approved = assurance["approval"]
    ready = (source is not None and approved is not None and source["session_id"] == session_id
             and approved == _digest({"source": source, "values": _review_values(state)}))
    snapshot["assurance"] = {"fields": list(assurance["fields"]), "reviewed": ready}
    if ready:
        return snapshot
    entries = {entry["key"]: entry for entry in snapshot["entries"]}
    evidence = ({key: value for key, value in source.items() if key != "text"} if source is not None
                else {"method": "structured_review_required", "quote": ""})
    for key in assurance["fields"]:
        entries[key] = {"key": key, "value": None, "disputed": True, "source": evidence}
        snapshot["unresolved"].append({"key": key, "value": None, "targets": [key],
            "reason": "confirmation_required", "source": evidence})
    snapshot["entries"] = [entries[key] for key in sorted(entries)]
    snapshot["pending_count"] = len(snapshot["unresolved"])
    return snapshot


def _compact_pending(proposal: dict) -> dict:
    return {key: value for key, value in proposal.items() if key not in {"basis", "source", "clarifies"}} | {
        "source": {key: value for key, value in proposal["source"].items() if key != "text"}
    }


def _map_changes(before: dict, after: dict) -> list[dict]:
    return [{"key": key, "before": before.get(key), "after": after.get(key)}
            for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)]


class AutomaticMemory:
    """Per-conversation, persistent recall with explicit and auditable boundaries.

    Feed the same full ORIGINAL conversation prefix plus appended messages every
    time. Starting a branch, replacing a system prompt, pruning or editing history
    requires a new session. Memory is not a fact verifier or permission system.
    """

    def __init__(self, store: RelayStore, session_id: str, *, policy: MemoryPolicy | None = None,
                 interpreter: MemoryInterpreter | None = None):
        if not isinstance(store, RelayStore):
            raise TypeError("store must be a RelayStore.")
        if policy is not None and not isinstance(policy, MemoryPolicy):
            raise TypeError("policy must be a MemoryPolicy.")
        if interpreter is not None and not isinstance(interpreter, MemoryInterpreter):
            raise TypeError("interpreter must be a MemoryInterpreter.")
        store.get_session(session_id)
        self.store = store
        self.session_id = session_id
        self.policy = policy if policy is not None else MemoryPolicy()
        self.interpreter = interpreter
        if self.policy.assurance_fields:
            with self.store._transaction(write=True):
                self.store._assert_mutable()
                state = self._load()
                before = clone(state)
                assurance = state.setdefault("assurance", {"fields": [], "user": None, "approval": None})
                fields = sorted(set(assurance["fields"]) | set(self.policy.assurance_fields))
                MemoryPolicy(assurance_fields=fields)
                if fields != assurance["fields"]:
                    assurance.update(fields=fields, approval=None)
                state["schema"] = 4
                self._save(before, state, "configure_assurance")

    def _load(self) -> dict:
        state = self.store._memory_state(self.session_id)
        if state is None:
            return _initial()
        if type(state) is not dict or type(state.get("schema")) is not int or state["schema"] not in (1, 2, 3, 4):
            raise ValueError("Unsupported automatic memory schema.")
        if type(state.get("entries")) is not dict or type(state.get("pending")) is not dict:
            raise ValueError("Invalid automatic memory maps.")
        for proposal in state["pending"].values():
            if type(proposal) is not dict or "value" not in proposal or "reason" not in proposal:
                raise ValueError("Invalid pending memory record.")
            if proposal["value"] is None or proposal["reason"] == "unparsed_correction":
                key = proposal.get("key")
                if (state["schema"] not in (2, 3, 4) or proposal["reason"] != "unparsed_correction"
                        or proposal["value"] is not None or type(key) is not str
                        or (key not in state["entries"] and (state["schema"] < 3 or key not in _FINITE_SETTINGS))
                        or proposal.get("targets") != [key]
                        or type(proposal.get("basis")) is not dict or key not in proposal["basis"]
                        or type(proposal.get("source")) is not dict
                        or proposal["source"].get("method") != "uncertain"):
                    raise ValueError("Invalid clarification-only memory record.")
        if state.get("turn_scope") is not None and state.get("previous_turn_scope") is not None:
            raise ValueError("Conflicting current and previous turn memory scopes.")
        for field in ("turn_scope", "previous_turn_scope"):
            scope = state.get(field)
            if scope is None:
                continue
            if (state["schema"] < 3 or type(scope) is not dict
                    or set(scope) != {"index", "message_sha256", "entries"}
                    or type(scope["index"]) is not int or scope["index"] < 0
                    or type(scope["message_sha256"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", scope["message_sha256"])
                    or type(scope["entries"]) is not dict):
                raise ValueError("Invalid current-turn memory scope.")
            for key, entry in scope["entries"].items():
                if (key not in _FINITE_SETTINGS or type(entry) is not dict
                        or entry.get("key") != key or type(entry.get("disputed")) is not bool
                        or type(entry.get("source")) is not dict
                        or entry.get("value") is not None and type(entry["value"]) is not str
                        or entry.get("value") is None and not entry["disputed"]):
                    raise ValueError("Invalid current-turn preference record.")
        assurance = state.get("assurance")
        if state["schema"] == 4 and assurance is None:
            raise ValueError("Instruction assurance state is missing.")
        if assurance is not None:
            if (state["schema"] != 4 or type(assurance) is not dict
                    or set(assurance) != {"fields", "user", "approval"}
                    or type(assurance["fields"]) is not list or not assurance["fields"]
                    or list(MemoryPolicy(assurance_fields=assurance["fields"]).assurance_fields) != assurance["fields"]
                    or assurance["approval"] is not None and (type(assurance["approval"]) is not str
                        or not re.fullmatch(r"[0-9a-f]{64}", assurance["approval"]))):
                raise ValueError("Invalid instruction assurance state.")
            source = assurance["user"]
            if source is not None and (type(source) is not dict
                    or source.get("role") != "user" or type(source.get("index")) is not int or source["index"] < 0
                    or type(source.get("text")) is not str or source.get("quote") != source["text"]
                    or source.get("start") != 0 or source.get("end") != len(source["text"])
                    or type(source.get("session_id")) is not str
                    or type(source.get("message_sha256")) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", source["message_sha256"])):
                raise ValueError("Invalid instruction assurance source.")
        return state

    def _revision(self, expected_revision: int | None) -> int:
        session = self.store._session(self.session_id)
        if expected_revision is not None:
            validate_revision(expected_revision)
            if expected_revision != session["revision"]:
                raise ConflictError(expected_revision, session["revision"])
        return session["revision"]

    def _usage(self, state: dict) -> tuple[int, int]:
        scope = state.get("turn_scope") or state.get("previous_turn_scope") or {}
        scope_count = len(scope.get("entries", {}))
        size = len(encode(_snapshot(state)))
        if scope_count:
            size += len(encode({**scope, "entries": {key: _compact_entry(record) | {"disputed": record["disputed"]}
                                                       for key, record in scope["entries"].items()}}))
        assurance = state.get("assurance")
        if assurance is not None:
            compact = clone(assurance)
            if compact["user"] is not None:
                compact["user"].pop("text", None)
            size += len(encode(compact))
            scope_count += len(assurance["fields"])
        return len(state["entries"]) + len(state["pending"]) + scope_count, size

    def _check_budget(self, state: dict) -> None:
        count, size = self._usage(state)
        if count > self.policy.max_entries:
            raise MemoryBudgetExceeded(f"Memory needs {count} active/pending entries; limit is {self.policy.max_entries}. No change was saved.")
        if size > self.policy.max_chars:
            raise MemoryBudgetExceeded(f"Memory needs {size} serialized characters; limit is {self.policy.max_chars}. No change was saved.")

    def _save(self, before: dict, state: dict, operation: str, *, detail: dict | None = None) -> dict | None:
        try:
            self._check_budget(state)
        except MemoryBudgetExceeded:
            # A restarted app may deliberately lower its limits. Permit explicit
            # cleanup one item at a time, but no reads into model context until
            # the whole remaining state fits the new caps.
            old_count, old_size = self._usage(before)
            new_count, new_size = self._usage(state)
            if not (operation in {"forget", "dismiss"} and new_count < old_count and new_size <= old_size):
                raise
        changes = _map_changes(before["entries"], state["entries"])
        proposals = _map_changes(before["pending"], state["pending"])
        scope_changed = any(before.get(key) != state.get(key) for key in ("turn_scope", "previous_turn_scope"))
        assurance_changed = before.get("assurance") != state.get("assurance")
        event = None
        if changes or proposals or scope_changed or assurance_changed:
            event = self.store._append_event(self.session_id, "memory", {
                "operation": operation, "changes": changes, "proposals": proposals,
                "current_authority": False, **(detail or {}),
                **({"turn_scope_changed": True} if scope_changed else {}),
                **({"assurance_changed": True} if assurance_changed else {}),
            })
        self.store._save_memory_state(self.session_id, state)
        return event

    def process(self, messages: list[dict], *, protocol: str = "plain", expected_revision: int | None = None,
                cancellation_event=None) -> dict:
        """Consume newly appended originals atomically.

        Default recognition makes no model call. An optional interpreter runs
        outside SQLite and only proposes reviewable recall. Failure, cancellation
        or concurrent changes leave this batch unconsumed for explicit retry.
        """
        # Encode before making extra copies, to enforce a total input cap.
        if len(encode(messages)) > self.policy.max_history_chars:
            raise MemoryBudgetExceeded("Original history exceeds max_history_chars. No change was saved.")
        archive = EvidenceArchive(messages, scope_id=self.session_id, protocol=protocol)
        history = archive.messages
        with self.store._transaction(write=self.interpreter is None):
            self.store._assert_mutable()
            if self.interpreter is not None and self.store._depth > 1:
                raise MemoryInterpretationError("Interpretation cannot run inside an existing store transaction.")
            revision = self._revision(expected_revision)
            state = self._load()
            before = clone(state)
            count = state["processed_count"]
            if state["protocol"] not in (None, protocol):
                raise MemoryHistoryError("The conversation protocol changed. Start a separate memory session.")
            if len(history) < count or _digest(history[:count]) != state["prefix_hash"]:
                raise MemoryHistoryError("Memory needs the unchanged original conversation prefix. Pruned, edited or branched histories require a new session.")
            if len(history) - count > self.policy.max_new_messages:
                raise MemoryBudgetExceeded("New original messages exceed max_new_messages. No change was saved.")
            if self.interpreter is None:
                report = self._consume(state, archive, history, revision, cancellation_event)
                return self._commit_process(before, state, history, protocol, report, cancellation_event)
        # Callbacks may perform slow model I/O. Never keep a SQLite transaction
        # or the store's lock open across them, and never retry them implicitly.
        report = self._consume(state, archive, history, revision, cancellation_event)
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            self._revision(revision)
            if self._load() != before:
                raise MemoryConflictError("Memory history changed during interpretation. Retry against the current history.")
            return self._commit_process(before, state, history, protocol, report, cancellation_event)

    @staticmethod
    def _check_cancelled(cancellation_event) -> None:
        if cancellation_event is not None and cancellation_event.is_set():
            raise MemoryInterpretationError("Memory interpretation was cancelled. No change was saved.")

    def _consume(self, state: dict, archive: EvidenceArchive, history: list[dict], revision: int, cancellation_event) -> dict:
        processed, ignored, created = [], [], []
        current_user = next((i for i in range(len(history) - 1, -1, -1) if history[i]["role"] == "user"), None)
        assurance = state.get("assurance")
        if assurance is not None and current_user is not None:
            message = history[current_user]
            old = assurance["user"]
            if old is None or (old["session_id"], old["index"], old["message_sha256"]) != (
                    self.session_id, current_user, _digest(message)):
                text = message["content"]
                assurance.update(approval=None, user={"session_id": self.session_id,
                    "history_id": archive.history_id, "index": current_user, "role": "user",
                    "message_sha256": _digest(message), "text": text, "quote": text,
                    "start": 0, "end": len(text), "method": "structured_review_required"})
        for index in range(state["processed_count"], len(history)):
            message = history[index]
            if message["role"] != "user":
                ignored.append({"index": index, "reason": "not_user"})
                continue
            text = original_text = message["content"]
            if len(text) > self.policy.max_message_chars:
                raise MemoryBudgetExceeded(f"User message {index} exceeds max_message_chars. No change was saved.")
            # Advance through every new user message, including an imported
            # backlog. Scope and persistent disputes must not depend on how the
            # caller partitions the same append-only history into batches.
            previous = state.pop("previous_turn_scope", None)
            if state.get("turn_scope") is not None:
                previous = state.pop("turn_scope")
            if previous is not None and is_continuation(text):
                source = {"history_id": archive.history_id, "index": index, "role": "user",
                    "text": text, "quote": text, "start": 0, "end": len(text),
                    "message_sha256": _digest(message), "scope": "turn",
                    "method": "continuation_needs_scope"}
                state["turn_scope"] = {"index": index, "message_sha256": _digest(message), "entries": {
                    key: {"key": key, "value": None, "disputed": True, "source": source,
                          "sequence": revision + 1} for key in previous["entries"]}}
            processed.append(index)
            extracted, uncertain = [], []
            unsupported = False
            normalized = _quoted_instructions(text)
            if normalized is not None and not _FRAMING.search(text):
                remainder, scoped = partition_scopes(normalized, _statements, _LOCAL_SCOPE, _scope_default)
                if scoped:
                    self._consume_scopes(state, scoped, archive, message, index, revision, created)
                    text = remainder
                    if not text.strip():
                        continue
            # Foreign framing and elisions must be checked before the English
            # quote guard. A recognized non-instruction never falls through.
            plan = plan_multilingual(text, state["entries"])
            if plan is not None and plan.get("ignored"):
                ignored.append({"index": index, "reason": "non_instruction_context"})
                continue
            if plan is None:
                # Whole-message caution blocks framed examples even when followed
                # by otherwise declarative sentences on later lines.
                normalized = _quoted_instructions(text)
                if normalized is None:
                    ignored.append({"index": index, "reason": "quoted_or_code"})
                    continue
                text = normalized
                if _FRAMING.search(text):
                    ignored.append({"index": index, "reason": "negation_hypothetical_or_reported"})
                    continue
                grammar_text = _without_acknowledgments(text)
                statements = [(start, end, quote) for start, end, quote in _statements(grammar_text)
                              if not _COURTESY.fullmatch(quote)]
                if len(statements) == 2 and re.match(_RECONSIDERED, _request_body(statements[0][2])[0], re.IGNORECASE):
                    # A named reconsideration can ground a following value-only
                    # imperative within this message, never across prior turns.
                    start, end = statements[0][0], statements[-1][1]
                    statements = [(start, end, text[start:end])]
                for start, end, quote in statements:
                    candidate = None if quote.endswith("!") else _extract(quote)
                    if candidate and candidate.get("contextual"):
                        target = _contextual_target(candidate, state, history, index) if len(statements) == 1 else None
                        if target is None:
                            candidate = None
                        else:
                            candidate["key"] = target
                    if candidate is None:
                        unsupported = True
                    else:
                        extracted.append((start, end, quote, candidate))
                if unsupported or not extracted or any(candidate["key"] not in _FINITE_SETTINGS for _, _, _, candidate in extracted):
                    # State-aware subject resolution also prevents generic capture
                    # from inventing fields for saved/standing preference aliases.
                    plan = None if _LOCAL_SCOPE.search(text) or _SECRET.search(text) else plan_corrections(
                        grammar_text, state["entries"], subjects=_SETTING_SUBJECTS, values=_FINITE_SETTINGS,
                        extract=_extract, request_body=_request_body, polite_question=_polite_question)
            if plan is not None:
                extracted = []
                for planned in plan["candidates"]:
                    candidate = dict(planned)
                    start, end = candidate.pop("span")
                    candidate.pop("start", None)
                    candidate.pop("end", None)
                    extracted.append((start, end, text[start:end], candidate))
                unsupported = bool(plan["uncertain"])
            conflicts = _conflicting_targets(extracted, state["entries"])
            unsupported = unsupported or bool(conflicts)
            if unsupported or not extracted:
                reason = "mixed_or_unsupported_context" if extracted else "unsupported_statement"
                uncertain = plan["uncertain"] if plan is not None else _uncertain_targets(grammar_text, state)
                uncertain = sorted(set(uncertain) | conflicts)
                if plan is None and unsupported:
                    # An unhandled qualifier can cancel or alter an otherwise
                    # complete instruction. Do not apply a partial update, but
                    # also do not let a stale target remain usable by repair.
                    # Reported material cannot ground a correction merely by
                    # containing a setter. Ordinary unsupported commentary is
                    # different: retain uncertainty regardless of clause order.
                    first_candidate = min((item[0] for item in extracted), default=0)
                    framed = any(start < first_candidate and _MATERIAL_PREFIX.search(quote)
                                 for start, _, quote in statements)
                    if not framed and not any(_REPORTED_SUFFIX.fullmatch(quote) for _, _, quote in statements):
                        uncertain = sorted(set(uncertain) | _candidate_targets(extracted, state["entries"]))
                proposals = []
                if self.interpreter is not None and _can_interpret(
                        original_text, review_unmatched=self.interpreter.review_unmatched):
                    self._check_cancelled(cancellation_event)
                    disputed = {target for pending in state["pending"].values() for target in pending["targets"]}
                    known = [{"key": key, "value": state["entries"][key]["value"], "disputed": key in disputed}
                             for key in sorted(state["entries"])]
                    proposals = self.interpreter.suggest(text=original_text, index=index, known_memory=known)
                    reason = "interpreter_no_proposal"
                extracted = [(p["start"], p["end"], p["quote"], {
                    "key": p["key"], "value": p["value"], "old": None,
                    "explicit": False, "interpreted": True,
                }) for p in proposals]
                if not extracted and not uncertain:
                    ignored.append({"index": index, "reason": reason})
                    continue
            source_base = {"history_id": archive.history_id, "index": index, "role": "user",
                           "text": original_text, "message_sha256": _digest(message)}
            first_created = len(created)
            for start, end, quote, candidate in extracted:
                source = source_base | {"quote": original_text[start:end], "start": start, "end": end}
                if candidate.get("interpreted"):
                    source["method"] = "interpreter"
                elif candidate.get("contextual"):
                    source["method"] = "contextual"
                self._apply_candidate(state, candidate, source, revision + 1, created)
            if uncertain:
                # A no-op or deduplicated model suggestion cannot account for
                # a fresh unclear change. Only a newly retained replacement
                # proposal can cover this source; old confirmation stays scoped.
                proposals = [state["pending"][pid] for pid in created[first_created:]]
                covered = {p["key"] for p in proposals
                           if p["value"] is not None and p["value"] != state["entries"].get(p["key"], {}).get("value")}
                source = source_base | {"quote": original_text, "start": 0, "end": len(original_text), "method": "uncertain"}
                for key in uncertain:
                    if key not in covered:
                        if key not in state["entries"]:
                            state["schema"] = max(state["schema"], 3)
                        self._apply_candidate(state, {"key": key, "value": None, "old": state["entries"].get(key, {}).get("value"),
                                                      "explicit": False, "uncertain": True}, source, revision + 1, created)
        return {"processed_indices": processed, "ignored": ignored, "pending_ids": created}

    def _consume_scopes(self, state, clauses, archive, message, index, revision, created):
        """Only finite user preferences can override the current user turn."""
        source_base = {"history_id": archive.history_id, "index": index, "role": "user",
                       "text": message["content"], "message_sha256": _digest(message)}
        available = dict(state["entries"])
        for key in _FINITE_SETTINGS:
            available.setdefault(key, {"key": key, "value": ""})
        for clause in clauses:
            body = clause["body"]
            plan = plan_multilingual(body, available)
            if plan is not None and plan.get("ignored"):
                continue
            candidates, uncertain = [], []
            if plan is not None:
                candidates, uncertain = plan["candidates"], plan["uncertain"]
            else:
                candidate = _extract(body)
                if candidate is not None and candidate.get("contextual"):
                    targets = candidate["context_targets"]
                    hint = clause["hint"]
                    selected = ([hint] if hint in targets else targets if len(targets) == 1 else
                                [key for key in targets if key in state["entries"]])
                    if len(selected) == 1:
                        candidate = candidate | {"key": selected[0], "explicit": True, "contextual": False}
                    else:
                        candidate, uncertain = None, selected or targets
                if candidate is not None:
                    candidates = [candidate]
                else:
                    planned = plan_corrections(body, available, subjects=_SETTING_SUBJECTS,
                        values=_FINITE_SETTINGS, extract=_extract, request_body=_request_body, polite_question=_polite_question)
                    if planned is not None:
                        candidates, uncertain = planned["candidates"], planned["uncertain"]
                    else:
                        uncertain = _uncertain_targets(body, {"entries": available})
                    if not candidates and not uncertain and clause["hint"] and _can_interpret(body):
                        uncertain = [clause["hint"]]
            values = {}
            for candidate in candidates:
                key = candidate["key"]
                if key not in _FINITE_SETTINGS:
                    continue
                aliases = [key for key in candidate.get("aliases", [key]) if key in state["entries"]]
                if len(aliases) > 1:
                    uncertain.extend(aliases)
                    continue
                key = aliases[0] if aliases else key
                value = normalize_memory_value(key, candidate["value"])
                old = normalize_memory_value(key, candidate.get("old"))
                if (candidate.get("unfamiliar") or not re.fullmatch(_FINITE_SETTINGS[key], value, re.I)
                        or old is not None and state["entries"].get(key, {}).get("value") != old):
                    uncertain.append(key)
                else:
                    values[key] = value
            targets = (set(values) | set(uncertain)).intersection(_FINITE_SETTINGS)
            if not targets:
                continue
            start, end = clause["start"], clause["end"]
            source = source_base | {"start": start, "end": end, "quote": message["content"][start:end],
                                    "scope": "turn" if clause["supported"] else "unsupported_lifetime"}
            if not clause["supported"]:
                # No task/session lifecycle is inferred from an arbitrary phrase.
                for key in sorted(targets):
                    if key not in state["entries"]:
                        state["schema"] = max(state["schema"], 3)
                    self._apply_candidate(state, {"key": key, "value": None,
                        "old": state["entries"].get(key, {}).get("value"), "explicit": False, "uncertain": True},
                        source | {"method": "uncertain"}, revision + 1, created)
                continue
            state["schema"] = max(state["schema"], 3)
            scope = state.setdefault("turn_scope", {"index": index, "message_sha256": _digest(message), "entries": {}})
            for key in sorted(targets):
                disputed = key in uncertain or self.policy.mode == "review"
                value = values.get(key)
                previous = scope["entries"].get(key)
                if previous is not None and (previous["value"] != value or previous["disputed"]):
                    disputed, value = True, None
                scope["entries"][key] = {"key": key, "value": value, "disputed": disputed,
                    "source": source, "sequence": revision + 1}

    def _commit_process(self, before: dict, state: dict, history: list[dict], protocol: str,
                        report: dict, cancellation_event) -> dict:
        self._check_cancelled(cancellation_event)
        state["protocol"] = protocol
        state["processed_count"] = len(history)
        state["prefix_hash"] = _digest(history)
        event = self._save(before, state, "process")
        changes = _map_changes(before["entries"], state["entries"])
        return clone({"status": "unchanged" if state["processed_count"] == before["processed_count"] else "processed",
                      **report,
                      "changes": [{"key": c["key"], "operation": "added" if c["before"] is None else "updated"} for c in changes],
                      "event_id": None if event is None else event["id"],
                      "revision": self.store._session(self.session_id)["revision"],
                      "memory_entries": len(state["entries"]), "pending_entries": len(state["pending"])})

    def _apply_candidate(self, state: dict, candidate: dict, source: dict, sequence: int, created: list[str]) -> None:
        entries = state["entries"]
        key = candidate["key"]
        reason = ("unparsed_correction" if candidate.get("uncertain") else
                  "interpreted_needs_review" if candidate.get("interpreted") else
                  "unrecognized_value_needs_review" if candidate.get("unfamiliar") else
                  "contextual_needs_review" if candidate.get("contextual") else None)
        if candidate.get("aliases"):
            targets = [alias for alias in candidate["aliases"] if alias in entries]
            if len(targets) > 1:
                key, reason = None, "ambiguous_target"
            else:
                key = targets[0] if targets else key
                targets = [key]
        elif key is None:
            targets = [k for k, entry in entries.items() if entry["value"].casefold() == candidate["old"].casefold()]
            if len(targets) == 1:
                key = targets[0]
            else:
                reason = "ambiguous_target" if targets else "no_known_target"
        else:
            targets = [key]
        # Canonicalize only the helper's finite preference vocabulary, after a
        # target is selected. Unknown/custom values keep their exact spelling.
        candidate = dict(candidate)
        if key is not None:
            for field in ("value", "old"):
                candidate[field] = normalize_memory_value(key, candidate[field])
        existing = entries.get(key)
        current_scope_disputes = {pid for pid, pending in state["pending"].items()
            if pending["source"].get("scope") == "unsupported_lifetime"
            and pending["source"]["index"] == source["index"]
            and pending["source"]["message_sha256"] == source["message_sha256"]}
        clarifies = [pid for pid, pending in state["pending"].items()
                     if not candidate.get("uncertain") and pending["reason"] == "unparsed_correction"
                     and pid not in current_scope_disputes and set(targets).intersection(pending["targets"])]
        if reason is None and candidate["old"] is not None and candidate["key"] is not None:
            if existing is None or existing["value"].casefold() != candidate["old"].casefold():
                reason = "old_value_mismatch"
        if reason is None and candidate["explicit"] and self.policy.mode == "auto":
            # A later statement naming the same subject explicitly resolves its
            # older direct proposal. An unsupported lifetime in this very same
            # message remains a question even beside a clear durable default.
            state["pending"] = {pid: p for pid, p in state["pending"].items()
                                if not (p["key"] == key or p["targets"] == [key])
                                or pid in current_scope_disputes}
        if existing is not None and existing["value"] == candidate["value"] and not clarifies:
            return
        if reason is None and existing is not None and not candidate["explicit"]:
            reason = "conflicting_declaration"
        if reason is None and self.policy.mode == "review":
            reason = "review_mode"
        if reason is not None:
            # Identical unresolved statements do not fill the pending queue.
            for pending in state["pending"].values():
                if (not candidate.get("uncertain") and pending.get("clarifies", []) == clarifies
                        and (pending["key"], pending["value"], pending["old"], pending["targets"]) == (key, candidate["value"], candidate["old"], targets)):
                    return
            proposal_id = _digest({"session": self.session_id, "source": source, "candidate": candidate})
            proposal = {"id": proposal_id, "key": key, "value": candidate["value"],
                        "old": candidate["old"], "targets": targets, "reason": reason,
                        "source": source, "basis": {target: entries.get(target) for target in targets},
                        "sequence": sequence}
            if clarifies:
                proposal["clarifies"] = clarifies
            if candidate.get("uncertain"):
                # Old Relay versions reject schema 2 instead of confirming a
                # null placeholder. Once migrated, the session never downgrades.
                state["schema"] = max(state["schema"], 2)
            state["pending"][proposal_id] = clone(proposal)
            created.append(proposal_id)
            return
        entries[key] = {"key": key, "value": candidate["value"], "source": source, "sequence": sequence}

    def _review_plan(self, state):
        assurance = state.get("assurance")
        if assurance is None:
            raise MemoryConflictError("No instruction assurance fields are configured.")
        view = _effective_snapshot(state, self.session_id)
        plan = {"schema": 1, "session_id": self.session_id, "revision": self._revision(None),
                "processed_count": state["processed_count"], "prefix_hash": state["prefix_hash"],
                "source": clone(assurance["user"]), "fields": _review_values(state),
                "requires_review": not view["assurance"]["reviewed"]}
        return plan | {"fingerprint": _digest(plan)}

    def review_plan(self) -> dict:
        """Inspect suggestions for explicit host review; no inferred approval.

        The complete plan binds confirmation to this session, revision and
        original history. All protected fields require explicit value/scope
        choices, including fields the host decides have not changed.
        """
        with self.store._transaction():
            return clone(self._review_plan(self._load()))

    def resolve_review(self, plan: dict, *, decisions: dict) -> dict:
        """Apply host-confirmed preferences against an unchanged review plan.

        Decisions contain exactly every protected key, each with ``value`` and
        ``scope`` (``durable`` or ``turn``). This API does not authenticate the
        reviewer or grant fact/tool authority. Do not auto-approve model output.
        """
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            state = self._load()
            before = clone(state)
            expected = self._review_plan(state)
            if type(plan) is not dict or _digest(plan) != _digest(expected):
                raise MemoryConflictError("The review plan changed; inspect the current source and fields again.")
            source = state["assurance"]["user"]
            if source is None or source["session_id"] != self.session_id:
                raise MemoryConflictError("Process the current session's original user history before review.")
            keys = state["assurance"]["fields"]
            if type(decisions) is not dict or set(decisions) != set(keys):
                raise ValueError("Review decisions must include exactly every protected memory key.")
            decisions = clone(decisions)
            for key, choice in decisions.items():
                if (type(choice) is not dict or set(choice) != {"value", "scope"}
                        or choice["scope"] not in ("turn", "durable")
                        or type(choice["value"]) is not str or not choice["value"].strip()
                        or len(choice["value"]) > 256):
                    raise ValueError("Each review choice needs a nonempty bounded string value and explicit scope.")
                choice["value"] = normalize_memory_value(key, choice["value"])
                if key in _FINITE_SETTINGS and not re.fullmatch(_FINITE_SETTINGS[key], choice["value"], re.I):
                    raise ValueError("The reviewed value is not supported for this finite preference.")
                if choice["scope"] == "turn" and key not in _FINITE_SETTINGS:
                    raise ValueError("Turn review supports finite preferences only; custom keys need durable scope.")
            durable = {key for key, choice in decisions.items() if choice["scope"] == "durable"}
            for proposal in state["pending"].values():
                targets = set(proposal["targets"])
                if targets.intersection(keys) and not targets <= durable:
                    raise MemoryConflictError("Resolve the durable dispute with explicit durable choices before a turn-only review.")
            state["pending"] = {pid: proposal for pid, proposal in state["pending"].items()
                                if not set(proposal["targets"]).intersection(durable)}
            sequence = self._revision(None) + 1
            for key, choice in decisions.items():
                for name in ("turn_scope", "previous_turn_scope"):
                    if name in state:
                        state[name]["entries"].pop(key, None)
                        if not state[name]["entries"]:
                            del state[name]
                evidence = source | {"method": "structured_review", "scope": choice["scope"]}
                entry = {"key": key, "value": choice["value"], "source": evidence, "sequence": sequence}
                if choice["scope"] == "durable":
                    state["entries"][key] = entry
                else:
                    scope = state.setdefault("turn_scope", {"index": source["index"],
                        "message_sha256": source["message_sha256"], "entries": {}})
                    scope["entries"][key] = entry | {"disputed": False}
            state["assurance"]["approval"] = _digest({"source": source, "values": _review_values(state)})
            event = self._save(before, state, "resolve_review", detail={"review_fingerprint": plan["fingerprint"],
                                                                       "decisions": decisions})
            return clone(event)

    def snapshot(self, *, for_context: bool = False) -> dict:
        """Return compact recall and disputes, separate from authoritative facts.

        Inspection remains available after lowering caps. ``for_context=True``
        enforces the current policy before any of this data reaches a model.
        """
        if type(for_context) is not bool:
            raise ValueError("for_context must be a boolean.")
        with self.store._transaction():
            state = self._load()
            if for_context:
                self._check_budget(state)
            return clone(_snapshot(state))

    def effective_snapshot(self, *, for_context: bool = False) -> dict:
        """Current-turn preferences over standing recall, never fact authority.

        The next user message expires explicit one-turn overrides. Durable
        inspection/confirmation APIs deliberately continue to show standing data.
        """
        if type(for_context) is not bool:
            raise ValueError("for_context must be a boolean.")
        with self.store._transaction():
            state = self._load()
            if for_context:
                self._check_budget(state)
            return clone(_effective_snapshot(state, self.session_id))

    def context_snapshot(self) -> dict:
        """Model-facing recall with exact quotes; full audit hashes stay local.

        Every accepted value and unresolved conflict is retained. Source indices
        refer to this session, or source.session_id for an archived segment. This is
        a lossless decision projection, not a summary or relevance filter.
        """
        snapshot = self.effective_snapshot(for_context=True)
        for records in (snapshot["entries"], snapshot["unresolved"]):
            for record in records:
                record["source"] = {key: record["source"][key] for key in ("index", "role", "quote", "session_id", "method")
                                    if key in record["source"]}
                record.pop("id", None)
                record.pop("sequence", None)
        return snapshot

    def entries(self) -> list[dict]:
        """Inspect full local accepted records, even after policy limits shrink.

        This is an operator view, not model-ready context.
        Full source text is retained here and in events; return values are copies.
        """
        with self.store._transaction():
            state = self._load()
            return clone([state["entries"][key] for key in sorted(state["entries"])])

    def pending(self) -> list[dict]:
        """Inspect pending proposals with full source text and captured basis."""
        with self.store._transaction():
            return clone(list(self._load()["pending"].values()))

    def confirm(self, proposal_id: str, *, key: str | None = None, expected_revision: int | None = None) -> dict:
        """Accept recall, never an authoritative fact or tool permission.

        An ambiguous old-value replacement requires ``key`` from its captured
        target list. A replacement with no known target cannot be confirmed;
        dismiss it and append a statement naming its subject instead.
        Clarification-only records cannot be confirmed. A concrete reviewed
        value resolves only the earlier uncertainty records captured with it.
        """
        nonempty_string(proposal_id, "proposal_id")
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            revision = self._revision(expected_revision)
            state = self._load()
            before = clone(state)
            proposal = state["pending"].get(proposal_id)
            if proposal is None:
                raise KeyError(f"Unknown pending memory proposal: {proposal_id}")
            if proposal["value"] is None or proposal["reason"] == "unparsed_correction":
                raise MemoryConflictError("This correction needs clarification, not confirmation. Append a clear named instruction or dismiss it.")
            selected = key if key is not None else proposal["key"]
            if selected is None or selected not in proposal["targets"]:
                raise MemoryConflictError("Select a captured target key; an unbound correction needs a new statement naming its subject.")
            if state["entries"].get(selected) != proposal["basis"].get(selected):
                raise MemoryConflictError("Memory changed since this proposal. Dismiss it and review a fresh statement.")
            for earlier_id in proposal.get("clarifies", []):
                earlier = state["pending"].get(earlier_id)
                if (earlier is not None and earlier["reason"] == "unparsed_correction"
                        and selected in earlier["targets"]
                        and earlier["basis"].get(selected) == proposal["basis"].get(selected)):
                    del state["pending"][earlier_id]
            state["entries"][selected] = {"key": selected, "value": normalize_memory_value(selected, proposal["value"]),
                                          "source": proposal["source"], "sequence": revision + 1}
            del state["pending"][proposal_id]
            event = self._save(before, state, "confirm", detail={"proposal_id": proposal_id})
            assert event is not None
            return clone(event)

    def dismiss(self, proposal_id: str, *, expected_revision: int | None = None) -> dict:
        nonempty_string(proposal_id, "proposal_id")
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            self._revision(expected_revision)
            state = self._load()
            before = clone(state)
            if proposal_id not in state["pending"]:
                raise KeyError(f"Unknown pending memory proposal: {proposal_id}")
            del state["pending"][proposal_id]
            event = self._save(before, state, "dismiss", detail={"proposal_id": proposal_id})
            assert event is not None
            return clone(event)

    def forget(self, key: str, *, expected_revision: int | None = None) -> dict:
        """Remove standing and scoped recall; provenance remains in audit history.

        Old transcript replay does not re-extract it. A genuinely new user
        statement can add it again. This is not secure deletion of personal data.
        """
        nonempty_string(key, "key")
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            self._revision(expected_revision)
            state = self._load()
            before = clone(state)
            scope_fields = ("turn_scope", "previous_turn_scope")
            if key not in state["entries"] and not any(key in state.get(field, {}).get("entries", {}) for field in scope_fields):
                raise KeyError(f"Unknown memory key: {key}")
            state["entries"].pop(key, None)
            for field in scope_fields:
                if field in state:
                    state[field]["entries"].pop(key, None)
                    if not state[field]["entries"]:
                        state.pop(field)
            # Outstanding proposals for a forgotten key must not resurrect it.
            state["pending"] = {pid: p for pid, p in state["pending"].items() if key not in p["targets"]}
            event = self._save(before, state, "forget", detail={"key": key})
            assert event is not None
            return clone(event)

    def undo(self, event_id: str, *, expected_revision: int | None = None) -> dict:
        """Reverse one memory change only if all affected items are unchanged.

        The consumed original prefix is NEVER rewound. Undo is another durable
        event, not erasure; unrelated fact/policy changes do not prevent it.
        """
        nonempty_string(event_id, "event_id")
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            revision = self._revision(expected_revision)
            state = self._load()
            before = clone(state)
            event = next((event for event in self.store.events(self.session_id)
                          if event["id"] == event_id and event["kind"] == "memory"), None)
            if event is None:
                raise KeyError(f"Unknown memory event: {event_id}")
            if event.get("turn_scope_changed") or event.get("assurance_changed"):
                raise MemoryConflictError("A turn-scoped memory event cannot be undone into a different request. Append a fresh instruction.")
            for table, field in (("entries", "changes"), ("pending", "proposals")):
                for change in event[field]:
                    key = change["key"]
                    if state[table].get(key) != change["after"]:
                        raise MemoryConflictError("An affected memory item changed after this event; undo would overwrite later work.")
                    if change["before"] is None:
                        state[table].pop(key, None)
                    else:
                        restored = clone(change["before"])
                        restored["sequence"] = revision + 1
                        state[table][key] = restored
            # If a confirmation is undone, its restored proposal must refer to
            # the restored value's new event sequence, not its pre-undo sequence.
            # Rebind only entries explicitly restored by this same reversal.
            restored_keys = {change["key"] for change in event["changes"] if change["before"] is not None}
            for change in event["proposals"]:
                if change["before"] is None:
                    continue
                proposal = state["pending"][change["key"]]
                for key in restored_keys & proposal["basis"].keys():
                    expected = proposal["basis"][key]
                    actual = state["entries"].get(key)
                    if expected is not None and actual is not None and {
                        k: v for k, v in expected.items() if k != "sequence"
                    } == {k: v for k, v in actual.items() if k != "sequence"}:
                        proposal["basis"][key] = clone(actual)
            result = self._save(before, state, "undo", detail={"undone_event_id": event_id})
            if result is None:
                raise MemoryConflictError("This event has no remaining reversible memory changes.")
            return clone(result)
