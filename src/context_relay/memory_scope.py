"""Small syntax helpers for explicit preference scope; never infer task lifetime."""
from __future__ import annotations

import re


_SCALAR = r"[^\W\d_][\w\u0900-\u097f.+-]{0,63}"
_QUOTED = re.compile(
    rf'"\s*{_SCALAR}\s*"|“\s*{_SCALAR}\s*”|«\s*{_SCALAR}\s*»|'
    rf"(?<!\w)'\s*{_SCALAR}\s*'(?!\w)|‘\s*{_SCALAR}\s*’")
_CONTRACTION = re.compile(
    r"\b(?:I'd|I've|I'll|I'm|that's|that'd|we've|we'll|let's|don't|doesn't|isn't|"
    r"wasn't|haven't|hasn't|hadn't|can't|won't)\b", re.I)
_TURN = re.compile(
    r"(?:(?:only\s+)?for\s+(?:this|the\s+next)\s+(?:(?:single|one)\s+)?"
    r"(?:reply|response|answer|export|download|request|turn|message)|"
    r"(?:only|just)\s+this\s+(?:reply|response|answer|export|download|request|turn|message)|"
    r"(?:only\s+)?this\s+time)", re.I)
_FOREIGN_TURN = re.compile(
    r"(?:(?:sirf\s+)?is\s+(?:jawab|reply|response)\s+ke\s+liye|sirf\s+is\s+baar|"
    r"solo\s+(?:esta\s+vez|para\s+esta\s+respuesta)|"
    r"pour\s+cette\s+r[eé]ponse|cette\s+fois|(?:सिर्फ\s+|केवल\s+)?इस\s+(?:जवाब|उत्तर|प्रतिक्रिया)\s+के\s+लिए|इस\s+बार)", re.I)
_FOREIGN_LOCAL = re.compile(
    r"(?:abhi\s+ke\s+liye|sirf\s+aaj|solo\s+hoy|por\s+ahora|"
    r"pour\s+l['’]instant|aujourd['’]hui\s+seulement|सिर्फ\s+आज|केवल\s+आज|अभी\s+के\s+लिए|"
    r"(?:sirf\s+)?is\s+(?:report|task|kaam|session|run|file)\s+ke\s+liye|"
    r"(?:सिर्फ\s+|केवल\s+)?इस\s+(?:रिपोर्ट|काम|कार्य|सत्र|फ़ाइल|फाइल)\s+के\s+लिए)", re.I)
_CALENDAR_SCOPE = re.compile(r"^today(?=\s|,)", re.I)
_CONTINUATION = re.compile(r"(?:please\s+)?(?:continue|go\s+ahead|proceed|carry\s+on)"
                           r"(?:\s+[^.!?;\n]{1,160})?[.!]?|(?:please\s+)?(?:do\s+it|yes)(?:\s+please)?[.!]?|"
                           r"(?:aage\s+badho|jaari\s+rakho|जारी\s+रखो|आगे\s+बढ़ो)[.!।]?", re.I)
_REPORTED_QUOTE = re.compile(
    r"^(?:(?:the|this|my|our)\s+[\w -]{1,80}\s+(?:says|said|reads|contains|states|wrote))\s*:?[\s]*$", re.I)


def quoted_spans(text: str):
    """Balanced string spans, treating apostrophes within words as punctuation."""
    pairs = {'"': '"', "'": "'", '“': '”', '‘': '’', '«': '»', '`': '`'}
    opened, closing, spans = None, None, []
    for index, char in enumerate(text):
        if opened is not None:
            if char == closing:
                spans.append((opened, index + 1))
                opened, closing = None, None
        elif char in pairs:
            if char in "'‘" and index and text[index - 1].isalnum():
                continue
            opened, closing = index, pairs[char]
    return None if opened is not None else spans


def quoted_literal(text: str):
    """Offer one quoted scalar as a literal value to the complete setter grammar."""
    spans = quoted_spans(text)
    if spans is None or len(spans) != 1:
        return None
    start, end = spans[0]
    if text[start] == '`':
        return None
    value = text[start + 1:end - 1].strip()
    if not value or len(value) > 256 or any(char in value for char in "\r\n"):
        return None
    return text[:start] + "v" + " " * (end - start - 1) + text[end:], value


def reported_quote(text: str) -> bool:
    spans = quoted_spans(text)
    return bool(spans and len(spans) == 1 and _REPORTED_QUOTE.fullmatch(text[:spans[0][0]])
                and not text[spans[0][1]:].strip(" .;!"))


def is_continuation(text: str) -> bool:
    """A bounded ambiguous continuation; never infer that a tool completed."""
    if re.search(r"\b(?:another|next|new)\s+(?:export|reply|response|request|message|record|task|run)\b", text, re.I):
        return False
    return bool(_CONTINUATION.fullmatch(text.strip()))


def scalar_quotes(text: str) -> str | None:
    """Mask scalar delimiters without changing offsets; reject quoted sentences.

    This only makes an atom eligible for the normal complete instruction grammar.
    It does not extract instructions from quoted material or code.
    """
    characters = list(text)
    for match in _QUOTED.finditer(text):
        characters[match.start()] = characters[match.end() - 1] = " "
    result = "".join(characters)
    check = _CONTRACTION.sub("", result.replace("’", "'"))
    if any(char in check for char in '\"\'`“”‘«»') or re.search(r"(?:^|[\r\n])[^\S\r\n]*>", check):
        return None
    return result


def scope_marker(text: str, local_pattern):
    """Find a known lifetime marker, including unsupported task lifetimes."""
    return (local_pattern.search(text) or _FOREIGN_TURN.search(text)
            or _FOREIGN_LOCAL.search(text) or _CALENDAR_SCOPE.search(text))


def partition_scopes(text: str, statements, local_pattern, valid_default):
    """Return a same-length durable remainder and explicitly scoped clauses.

    Scope markers must be at a complete clause boundary. Unknown lifetimes are
    separated too, but marked unsupported for the caller's clarification path.
    """
    scoped, characters = [], list(text)
    clauses = []
    for start, end, quote in statements(text, split_clauses=False):
        # An explicit second command may carry its own lifetime. Do not split
        # "export as JSON, but only for this task": that qualifier scopes the
        # first command. The unconsumed remainder is still parsed as a whole.
        split = re.search(r",\s*but\s+(?=(?:use|export|reply|respond|answer|keep|set|change)\b)", quote, re.I)
        if (split and scope_marker(quote[split.end():], local_pattern)
                and valid_default(quote[:split.start()])):
            clauses.append((start + split.end(), end, quote[split.end():]))
        else:
            clauses.append((start, end, quote))
    for start, end, quote in clauses:
        match = scope_marker(quote, local_pattern)
        if match is None:
            continue
        marker = match.group()
        before, after = quote[:match.start()].strip(" ,"), quote[match.end():].strip(" ,.;!।")
        after = re.sub(r"^only(?:\s*,\s*|\s+)", "", after, flags=re.I)
        if before.casefold() == "only":
            before = ""
        # Do not strip a temporal phrase embedded in an explanation or condition.
        if before and after not in ("", "only"):
            continue
        body = after if not before else re.sub(r"(?:,?\s*(?:but\s+)?(?:only|just))$", "", before, flags=re.I).strip()
        supported = bool(_TURN.fullmatch(marker) or _FOREIGN_TURN.fullmatch(marker))
        hint = "export_format" if re.search(r"\b(?:export|download)\b", marker, re.I) else None
        scoped.append({"start": start, "end": end, "body": body,
                       "supported": supported, "hint": hint})
        characters[start:end] = " " * (end - start)
    return "".join(characters), scoped
