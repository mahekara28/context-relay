"""Bounded, message-local correction plans; no model or cross-turn reference guesses.

Every clause must describe a setting, its previous/current value, or a linked
clarification. Nothing is committed here. The caller applies the complete plan
through the normal review, old-value, provenance and budget checks.
"""

import re

from .memory_values import normalize_memory_value


_ATOM = r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}(?:\s+(?:responses?|replies|answers?|ones?))?"
_MODIFIER = r"(?:saved|recorded|remembered|default|standing|ongoing|usual|earlier|old|previous|current|preferred|existing|permanent|future|later)"
_VERB = r"(?:change|switch|set|update|correct|revise|fix|replace)"
_SELECT = r"(?:save|remember|use|choose|keep|make|go\s+with|I\s+(?:now\s+)?(?:want|need|prefer)|I(?:'d|\s+would)\s+rather\s+(?:have|use)|I(?:'ve|\s+have)\s+settled\s+on|back\s+to)"
_BAD = r"(?:wrong|outdated|unsuitable|out\s+of\s+date|no\s+longer\s+(?:(?:right|correct|suitable|appropriate)(?:\s+for\s+me)?|(?:the\s+one|what)\s+I\s+(?:want|need|prefer)))(?:\s+now)?"
_GOOD = r"(?:fine|correct|right|unchanged)"
_REPLIES = r"(?:(?:all|future|later|your|our|my)\s+)*(?:responses?|replies|answers?|reply)"
_DESIRE = r"(?:I\s+(?:want|need|prefer)|I(?:'d|\s+would)\s+(?:like|prefer))"
# Start only at a separator run, avoiding quadratic retries on long whitespace.
_DURABLE = re.compile(r"(?:(?<!\s)(?:,\s+|\s+(?:,\s+)?)(?:from\s+now\s+on|going\s+forward|permanently|by\s+default|again|as\s+(?:(?:a|the|my|our)\s+)?(?:standing|permanent|ongoing)\s+preferences?|for\s+(?:future|my\s+next)\s+(?:conversations|sessions)))$", re.I)
_SPLIT = re.compile(r"[.!?;](?=\s|$)|\n|,\s*(?:and|while|but)\s+|(?<!\s)\s+and\s+(?=(?:make|set|change|update|correct|switch|remember|save)\b)", re.I)


def _match(pattern, text):
    return re.fullmatch(pattern, text, re.I)


class _Unparsed(Exception):
    pass


class _Plan:
    def __init__(self, entries, subjects, values, extract, request_body):
        self.entries, self.subjects, self.values = entries, subjects, values
        self.extract, self.request_body = extract, request_body
        self.scope, self.domain, self.updates, self.unknown, self.old = set(), set(), {}, set(), {}
        self.span, self.domain_span, self.required, self.evidence = (0, 0), None, [], {}
        self.explicit, self.aliases, self.intent = {}, {}, False
        self.rejection_seen = False

    def record(self, keys):
        """Retain exact clause ranges plus any same-message antecedents used."""
        for key in keys:
            spans = [self.span, *self.required]
            if key in self.evidence:
                spans.append(self.evidence[key])
            self.evidence[key] = min(s[0] for s in spans), max(s[1] for s in spans)

    def value(self, key, text):
        text = text.strip()
        if key in {"response_style", "response_language"}:
            text = re.sub(r"(?<!\s)\s+(?:responses?|replies|answers?|ones?)$", "", text, flags=re.I)
        if key in self.values:
            if not _match(self.values[key], text):
                raise _Unparsed
        elif (not _match(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}", text)
              or text.casefold() in {"it", "that", "this", "one", "either", "other", "something", "whatever", "unknown", "undecided"}):
            raise _Unparsed
        return normalize_memory_value(key, text)

    @staticmethod
    def clean_subject(text):
        text = re.sub(r"[_-]+", " ", text.strip())
        text = re.sub(r"(?<!\s)\s+(?:you\s+(?:have\s+(?:saved|recorded)|remember)\s+for\s+me)$", "", text, flags=re.I)
        for _ in range(2):
            text = re.sub(r"^(?:(?:the|my|our|your|all|every)\s+|" + _MODIFIER + r"\s+(?!for\b))+(?!$)", "", text, flags=re.I)
            text = re.sub(r"^(?:preferences?|default)\s+(?:for|about)\s+", "", text, flags=re.I)
        text = re.sub(r"(?<!\s)\s+(?:preferences?|choices?|settings?|entries|entry)$", "", text, flags=re.I)
        return text.strip()

    def subject(self, text, value=None):
        """A full named subject, or a uniquely compatible message-local referent."""
        # A modifier may be part of a real custom key (saved_delivery_window).
        # Match that exact identity before treating words as preference wrappers.
        literal = re.sub(r"[_-]+", " ", text.strip())
        literal = re.sub(r"^(?:(?:the|my|our|your)\s+)+", "", literal, flags=re.I)
        exact = {key for key in self.entries if literal.casefold() == key.replace("_", " ").casefold()}
        if exact:
            if value is not None:
                for key in exact:
                    self.value(key, value)
            return exact
        text = _DURABLE.sub("", text).strip()
        name = self.clean_subject(text)
        relative = _match(rf"(?P<subject>\S.*?)(?<!\s)\s+{_DESIRE}\s+(?:you\s+)?to\s+(?:use|save|remember)", name)
        if relative:
            name = relative["subject"]
        keys = {key for key, pattern in self.subjects.items() if _match(pattern, name)}
        keys.update(key for key in self.entries if name.casefold() == key.replace("_", " ").casefold())
        if _match(r"language\s+(?:of\s+|you\s+use\s+in\s+)(?:your\s+)?(?:replies|responses|answers)(?:\s+to\s+me)?", name):
            keys.add("response_language")
        if _match(rf"language\s+(?:{_DESIRE}\s+(?:you\s+)?to\s+use\s+(?:for|in)\s+{_REPLIES}|you\s+(?:(?:normally|usually)\s+)?(?:reply|respond|answer)\s+in)", name):
            keys.add("response_language")
        if _match(r"how\s+much\s+detail\s+you\s+(?:give|provide|include)|(?:amount|level)\s+of\s+detail(?:\s+you\s+(?:give|provide|include))?", name):
            keys.add("response_style")
        if _match(r"(?:your\s+)?(?:replies|responses|answers|reply)(?:\s+to\s+me)?", name):
            keys.update(("response_language", "response_style"))
        if _match(r"export(?:\s+(?:format|choice))?", name):
            keys.add("export_format")
        if _match(r"output|results|data", name):
            keys.update(key for key in ("export_format", "output_format") if key in self.entries)
            if not keys:
                keys.add("export_format")
        referenced = False
        if not keys:
            if _match(r"it|that|this|them|(?:that|this)\s+one|one\s+of\s+(?:them|those)|(?:that\s+)?(?:change|replacement|preferences?)|(?:old|new|existing|saved|current)\s+(?:one|value|entry|setting|choice)|default|values?|entries|entry|settings?|choices?", name):
                keys = set(self.scope)
                referenced = True
            elif _match(r"language|style|format|window|language\s+preference", name):
                tail = name.split()[0]
                keys = {key for key in self.scope | self.domain if key.endswith("_" + tail)}
                referenced = True
        if value is not None:
            compatible = set()
            for key in keys:
                try:
                    self.value(key, value)
                    compatible.add(key)
                except _Unparsed:
                    pass
            keys = compatible
        if not keys:
            raise _Unparsed
        if referenced:
            self.required.extend(self.evidence[key] for key in keys if key in self.evidence)
            if self.domain_span is not None:
                self.required.append(self.domain_span)
        return keys

    def embedded_subject(self, text):
        """Find an explicitly written value inside a subject, never from memory."""
        try:
            return self.subject(text), None
        except _Unparsed:
            name = self.clean_subject(text)
            match = _match(r"(?P<value>[A-Za-z0-9][A-Za-z0-9_.+-]{0,63})\s+(?P<subject>.+)", name)
            if not match:
                raise
            return self.subject(match["subject"], match["value"]), match["value"]

    def historical(self, subject):
        literal = re.sub(r"[_-]+", " ", subject.strip())
        literal = re.sub(r"^(?:(?:the|my|our|your)\s+)+", "", literal, flags=re.I)
        if any(literal.casefold() == key.replace("_", " ").casefold() for key in self.entries):
            return False
        return bool(re.match(r"(?:old|earlier|previous)\s+", literal, re.I))

    def previous(self, keys, value):
        if len(keys) != 1:
            raise _Unparsed
        key = next(iter(keys))
        value = self.value(key, value)
        if key in self.old and self.old[key] != value:
            raise _Unparsed
        self.old[key] = value
        self.scope = set(keys)
        self.record(keys)

    def set(self, subject, value, old=None, *, explicit=True):
        keys = self.subject(subject, value)
        if self.historical(subject):
            self.previous(keys, value)
            return
        if len(keys) != 1:
            self.invalidate(keys)
            return
        key = next(iter(keys))
        value = self.value(key, value)
        if key in self.updates and self.updates[key] != value:
            raise _Unparsed
        self.updates[key] = value
        self.explicit[key] = self.explicit.get(key, False) or explicit or self.intent
        self.unknown.discard(key)
        self.scope = {key}
        self.record({key})
        if old is not None:
            self.previous({key}, old)

    def invalidate(self, keys):
        self.scope = set(keys)
        self.unknown.update(keys)
        self.record(keys)

    def reject(self, subject, old=None):
        """Reject only the named setting/value, without guessing a replacement.

        An explicit old value grounds a later same-message replacement. On its
        own it disputes only that value, so rejecting JSON cannot suspend an
        unrelated active CSV choice. Multiple matching subjects stay ambiguous.
        """
        keys, embedded = self.subjects_joined(subject)
        self.rejection_seen = True
        old = embedded if old is None else old
        if old is not None:
            keys = self.subject(subject, old) if embedded is None else keys
            matching = set()
            for key in keys:
                self.previous({key}, old)
                current = self.updates.get(key, self.entries.get(key, {}).get("value"))
                if current == self.value(key, old):
                    matching.add(key)
            self.invalidate(matching)
            self.scope = set(keys)
            return
        self.invalidate(keys)

    def dissatisfaction(self, text):
        """Compose a bounded rejection with normal setters in the same message.

        Descriptions select no value: ``CSV is too awkward for exports`` only
        rejects CSV. A subsequent explicit choice supplies any replacement.
        """
        description = r"[A-Za-z]+(?:[ -][A-Za-z]+){0,5}"
        patterns = (
            rf"(?P<old>{_ATOM})\s+is\s+(?:too|overly)\s+(?P<description>{description})\s+(?:for|as)\s+(?P<subject>.+)",
            rf"(?P<subject>\S.*?)(?<!\s)\s+(?:is|are)\s+(?:too|overly)\s+(?P<description>{description})",
            rf"(?P<old>{_ATOM})\s+no\s+longer\s+works\s+(?:for|as)\s+(?P<subject>.+)",
            r"(?P<subject>\S.*?)(?<!\s)\s+no\s+longer\s+(?:works?(?:\s+for\s+me)?|suits?\s+me|fits?\s+my\s+needs)",
            rf"(?:I\s+(?:no\s+longer\s+(?:want|prefer)|don't\s+(?:want|prefer))|(?:do\s+not|don't)\s+use|stop\s+using)\s+(?P<old>{_ATOM})\s+(?:as|for|in)\s+(?P<subject>\S.*?)(?:\s+(?:anymore|any\s+longer))?",
            rf"(?P<subject>\S.*?)(?<!\s)\s+(?:is\s+not|isn't|should\s+not\s+be)\s+(?P<old>{_ATOM})(?:\s+(?:anymore|any\s+longer))?",
        )
        for pattern in patterns:
            match = _match(pattern, text)
            if not match:
                continue
            fields = match.groupdict()
            detail = fields.get("description")
            if detail and re.search(r"\b(?:if|unless|when|maybe|perhaps|not|never|and|or|but|would|could|said|says)\b", detail, re.I):
                raise _Unparsed
            subject, old = fields["subject"], fields.get("old")
            if old and _match(r"(?:my\s+)?default", subject) and re.search(r"\s(?:responses?|replies|answers?)$", old, re.I):
                subject = "response style"
            try:
                keys, embedded = self.subjects_joined(subject)
            except _Unparsed:
                continue
            if (not old and embedded is None and detail
                    and _match(r"verbose|wordy|lengthy|long|short|detailed|brief|terse", detail)
                    and keys == {"response_language", "response_style"}):
                subject = "response style"
            self.reject(subject, old)
            return True
        return False

    def reject_known_value(self, value):
        """A rejected literal can suspend matching finite preferences only.

        It supplies no new value or unnamed custom-key authority. Equal saved
        values remain ambiguous; do not select a field by conversational recency.
        """
        keys = set()
        for key in self.entries.keys() & self.values.keys():
            try:
                old = self.value(key, value)
            except _Unparsed:
                continue
            if self.entries[key]["value"] == old:
                keys.add(key)
                self.previous({key}, value)
        if not keys:
            raise _Unparsed
        self.rejection_seen = True
        self.invalidate({key for key in keys if self.updates.get(key, self.entries[key]["value"]) == self.value(key, value)})
        self.scope = keys

    def desire(self, text):
        """Recognize explicit desired values and bounded qualitative requests."""
        phrase = rf"(?P<style>{self.values['response_style']})\s+{_REPLIES}\s+in\s+(?P<language>{self.values['response_language']})"
        for pattern in (rf"{_SELECT}\s+{phrase}", rf"{phrase}\s+(?:is|are)\s+what\s+{_DESIRE}"):
            match = _match(pattern, text)
            if match:
                self.set("response style", match["style"])
                self.set("response language", match["language"])
                return True
        match = _match(rf"(?P<new>{_ATOM})\s+(?:would\s+suit|suits?)\s+(?P<subject>\S.*?)(?<!\s)\s+better\s+than\s+(?P<old>{_ATOM})", text)
        if match:
            # A personal comparative states the user's preference. A general
            # product comparison does not instruct us to replace their setting.
            if not re.match(r"(?:my|our)\s+", match["subject"], re.I):
                raise _Unparsed
            self.set(match["subject"], match["new"], match["old"])
            return True
        match = _match(rf"{_SELECT}\s+(?P<subject>\S.*?)(?<!\s)\s+(?:in|as|to\s+be)\s+(?P<new>{_ATOM})", text)
        if not match:
            match = _match(rf"(?:make|keep)\s+(?P<subject>it|them|this|that)\s+(?P<new>{_ATOM})", text)
        if match:
            self.set(match["subject"], match["new"])
            return True
        match = _match(r"(?P<subject>\S.*?)(?<!\s)\s+could\s+use\s+(?:more|less)\s+(?:detail|brevity)", text)
        if match:
            keys = self.subject(match["subject"])
            if keys not in ({"response_style"}, {"response_language", "response_style"}):
                raise _Unparsed
            self.reject("response style")
            return True
        return False

    def reaffirm(self, keys, value=None):
        self.scope = set(keys)
        self.record(keys)
        if value is not None:
            for key in keys:
                if key not in self.entries or self.entries[key]["value"] != self.value(key, value):
                    self.unknown.add(key)

    def contrast(self, text):
        match = _match(rf"(?P<body>\S.*?)(?<!\s)(?:,\s+|\s+(?:,\s+)?)(?:instead\s+of|rather\s+than|in\s+place\s+of|not|replacing)\s+(?P<old>{_ATOM})", text)
        return (match["body"], match["old"]) if match else (text, None)

    def assignment(self, subject, text, old=None, *, explicit=True):
        text, contrast = self.contrast(text)
        old = contrast if contrast is not None else old
        for pattern in (rf"(?P<new>{_ATOM})\s+(?:replaces|should\s+replace)\s+(?P<old>{_ATOM})",
                        rf"(?P<old>{_ATOM})\s+should\s+(?:now\s+)?be\s+(?P<new>{_ATOM})"):
            match = _match(pattern, text)
            if match:
                self.set(subject, match["new"], match["old"])
                return
        try:
            self.set(subject, text, old, explicit=explicit)
        except _Unparsed:
            keys = self.subject(subject)
            # Unknown descriptions/options are evidence of missing information,
            # not candidate literal values or authority to consult external data.
            if not _match(r"(?:the|a|an|one|either|something|whatever|whichever|short|brief|concise|detailed)\b.+", text):
                raise
            self.invalidate(keys)

    def clause(self, raw):
        text, explicit = self.request_body(raw.strip().replace("\u2019", "'"))
        text = re.sub(r"^for\s+(?:all\s+)?(?:future|later|my\s+next)\s+(?:conversations|sessions),\s*", "", text, flags=re.I)
        text = re.sub(rf"^I\s+(?:want|need)\s+to\s+(?={_VERB}\b)", "", text, flags=re.I)
        literal = self.extract(text)
        if literal and _DURABLE.search(text):
            try:
                targets = self.subject((literal["key"] or "").replace("_", " "))
            except _Unparsed:
                targets = set()
            if targets and not targets.intersection(self.values):
                # A custom literal can end in ordinary words such as "Again".
                # The planner must not additionally strip them as metadata.
                # Keep the named setting disputed until clarified.
                self.invalidate(targets)
                return
        for _ in range(4):
            stripped = _DURABLE.sub("", text).strip()
            if stripped == text:
                break
            text = stripped
        if _match(r"thanks|thank\s+you|ok(?:ay)?|please", text):
            return
        legacy = self.extract(text)
        if (legacy and legacy["key"] not in set(self.values) | set(self.entries)
                and (legacy["explicit"] or not _match(_BAD + "|" + _GOOD, legacy["value"]))):
            try:
                keys = self.subject((legacy["key"] or "").replace("_", " "))
            except _Unparsed:
                keys = set()
            if keys and not keys.intersection(self.values):
                # A valid literal new custom key must not be folded into an
                # existing custom key by treating its prefix as a qualifier.
                raise _Unparsed
        # A durable update header supplies intent, not a value or an authority.
        frame = re.match(r"^(?:(?:permanent|standing|ongoing)\s+)?preference\s+(?:updates?|corrections?)\s*:\s*|^correction\s+for\s+my\s+next\s+sessions\s*:\s*|^(?:update|correct|change)\s+(?:(?:two|both|the|my|our)\s+)*(?:standing\s+|saved\s+)?preferences\s*:\s*", text, re.I)
        if frame:
            self.intent = True
            self.clause(text[frame.end():])
            return
        frame = _match(r"(?P<subject>\S.*?(?<!\s)\s+preferences)\s+need\s+updating\s*:\s*(?P<body>.+)", text)
        if frame:
            self.scope = self.subject(frame["subject"])
            self.domain = set(self.scope)
            self.domain_span = self.span
            self.clause(frame["body"])
            return
        frame = _match(r"(?:update|correct)\s+what\s+you\s+remember\s+about\s+(?P<subject>.+)", text)
        if frame:
            self.scope = self.subject(frame["subject"])
            self.domain = set(self.scope)
            self.domain_span = self.span
            return
        frame = _match(r"(?:for\s+(?P<for>\S.*?),|about\s+(?P<about>\S.*?):)\s*(?P<body>.+)", text)
        if frame:
            self.scope = self.subject(frame["for"] or frame["about"])
            self.domain = set(self.scope)
            self.domain_span = self.span
            self.clause(frame["body"])
            return
        if self.scope and self.deferred(text):
            self.invalidate(self.scope)
            return
        if self.desire(text):
            return
        pair = _match(rf"(?P<rejected>.+?),\s+(?P<replacement>{_SELECT}\s+.+)", text)
        if pair and self.dissatisfaction(pair["rejected"]):
            self.clause(pair["replacement"])
            return
        if self.dissatisfaction(text):
            return
        # Concrete transitions preserve the old/new direction and old evidence.
        patterns = (
            rf"(?:{_VERB}\s+)?(?P<subject>\S.*?)(?<!\s)\s+(?:has\s+changed\s+)?from\s+(?P<old>{_ATOM})\s+to\s+(?P<new>{_ATOM})",
            rf"(?:I\s+(?:now\s+)?want\s+)?(?P<new>{_ATOM})\s+(?:to\s+replace|replaces)\s+(?P<old>{_ATOM})\s+(?:as|for)\s+(?P<subject>.+)",
            rf"(?:{_SELECT}\s+)?(?P<new>{_ATOM}),\s*(?:rather\s+than|instead\s+of)\s+(?P<old>{_ATOM}),\s*as\s+(?P<subject>.+)",
        )
        for pattern in patterns:
            match = _match(pattern, text)
            if match:
                self.set(match["subject"], match["new"], match["old"])
                return
        body, old = self.contrast(text)
        body = _DURABLE.sub("", body).strip()
        if self.scope and _match(r"(?:(?:remember|save)\s+)?(?:it|that|the\s+change|(?:that\s+)?language\s+preference)(?:\s+as\s+my\s+ongoing\s+preference)?", body):
            if old is not None:
                self.previous(self.scope, old)
            return
        match = _match(rf"replace\s+(?P<subject>\S.*?)(?<!\s)\s+with\s+(?P<new>.+)", body)
        if match:
            keys, embedded = self.embedded_subject(match["subject"])
            self.scope = keys
            self.assignment("that", match["new"], embedded or old)
            return
        match = _match(rf"{_VERB}\s+(?P<subject>\S.*?):\s*(?P<new>.+)", body)
        if not match:
            match = _match(rf"{_VERB}\s+(?P<subject>\S.*?)(?<!\s)\s+(?:back\s+)?to\s+(?P<new>.+)", body)
        if match:
            self.assignment(match["subject"], match["new"], old)
            return
        match = _match(r"(?P<subject>\S.*?)(?<!\s)\s+(?P<mode>is\s+now|should\s+(?:now\s+)?(?:be|read)|should\s+change\s+to|is)\s+(?P<new>.+)", body)
        if match and not _match(_BAD + "|" + _GOOD, match["new"]):
            try:
                self.subject(match["subject"])
            except _Unparsed:
                pass
            else:
                if self.historical(match["subject"]):
                    self.previous(self.subject(match["subject"], match["new"]), match["new"])
                else:
                    self.assignment(match["subject"], match["new"], old,
                                    explicit=explicit or match["mode"].casefold() != "is")
                return
        match = _match(rf"(?:{_SELECT}\s+)?(?P<new>{_ATOM})\s+(?:as|for)\s+(?P<subject>\S.*?)(?:\s+instead)?", body)
        if match:
            subject = match["subject"]
            if _match(r"(?:my\s+)?(?:usual\s+)?style|(?:my\s+)?default", subject) and re.search(r"\s(?:responses?|replies|answers?)$", match["new"], re.I):
                subject = "response style"
            self.set(subject, match["new"], old)
            return
        match = _match(rf"(?:{_SELECT}\s+)?(?P<new>{_ATOM})\s+is\s+(?P<subject>.+)", body)
        if match:
            try:
                self.subject(match["subject"], match["new"])
            except _Unparsed:
                pass
            else:
                self.set(match["subject"], match["new"], old)
                return
        match = _match(rf"make\s+(?P<new>{_ATOM})\s+(?P<subject>.+)", body)
        if match:
            try:
                self.subject(match["subject"], match["new"])
            except _Unparsed:
                pass
            else:
                self.set(match["subject"], match["new"], old)
                return
        match = _match(rf"{_SELECT}\s+(?P<new>{_ATOM})(?:\s+instead)?", body)
        if match and self.scope:
            self.set("that", match["new"], old)
            return
        match = _match(r"I\s+(?:now\s+)?want\s+(?P<subject>them|it|that)\s+in\s+(?P<new>.+)", body)
        if match:
            self.assignment(match["subject"], match["new"], old)
            return
        # Old-value assertions and linked status statements are never new keys.
        match = _match(rf"(?P<subject>\S.*?)(?<!\s)\s+was\s+(?P<old>{_ATOM})", text)
        if match:
            self.previous(self.subject(match["subject"], match["old"]), match["old"])
            return
        match = _match(rf"(?P<old>{_ATOM})\s+no\s+longer\s+(?:works\s+for\s+me|suits\s+me)", text)
        if match:
            self.reject_known_value(match["old"])
            return
        match = _match(r"(?P<subject>\S.*?)(?<!\s)\s+(?:doesn't|don't|does\s+not|do\s+not)\s+either", text)
        if match and self.rejection_seen:
            self.reject(match["subject"])
            return
        match = _match(rf"you\s+have\s+(?P<old>{_ATOM})\s+recorded\s+as\s+(?P<subject>\S.*?)(?<!\s)\s+and\s+(?P<other>{_ATOM})\s+as\s+(?P<second>.+)", text)
        if match:
            combined = set()
            for subject, value in ((match["subject"], match["old"]), (match["second"], match["other"])):
                keys = self.subject(subject, value)
                self.previous(keys, value)
                combined.update(keys)
            self.scope = combined
            return
        match = _match(rf"you\s+have\s+(?P<old>{_ATOM})\s+recorded\s+as\s+(?P<subject>.+)|(?P<was>{_ATOM})\s+was\s+(?P<previous>.+)", text)
        if match:
            self.previous(self.subject(match["subject"] or match["previous"], match["old"] or match["was"]), match["old"] or match["was"])
            return
        match = _match(rf"(?:that's\s+a\s+change\s+from\s+(?P<from>{_ATOM})|(?P<old>{_ATOM})\s+is\s+my\s+old\s+preference)", text)
        if match:
            value = match["from"] or match["old"]
            self.previous(self.subject("that", value), value)
            return
        match = _match(r"(?P<subject>\S.*?)(?<!\s)\s+(?:(?:is|are)\s+(?:both\s+)?(?P<bad>" + _BAD + r")|(?:both\s+)?needs?\s+(?:to\s+(?:change|be\s+corrected)|changing|correcting|revising|replacing))", text)
        if match:
            keys, embedded = self.subjects_joined(match["subject"])
            if embedded is not None:
                self.previous(keys, embedded)
            if embedded is None or any(key not in self.updates or self.updates[key] == self.value(key, embedded) for key in keys):
                self.invalidate(keys)
            return
        match = _match(r"(?P<subject>\S.*?)(?<!\s)\s+(?:is|remains?|stays?)\s+(?:still\s+)?(?P<good>" + _GOOD + r"|" + _ATOM + ")", text)
        if match:
            keys, embedded = self.embedded_subject(match["subject"])
            self.reaffirm(keys, embedded if _match(_GOOD, match["good"]) else match["good"])
            return
        match = _match(rf"I\s+no\s+longer\s+(?:want|prefer)\s+(?P<old>{_ATOM})\s+as\s+(?P<subject>.+)", text)
        if match:
            subject = match["subject"]
            if _match(r"my\s+default", subject) and re.search(r"\sresponses$", match["old"], re.I):
                subject = "response style"
            keys = self.subject(subject, match["old"])
            self.invalidate(keys)
            self.previous(keys, match["old"])
            return
        match = _match(rf"(?P<old>{_ATOM})\s+(?:no\s+longer\s+suits\s+me|is\s+no\s+longer\s+(?P<subject>.+))", text)
        if match:
            keys = self.subject(match["subject"] or "that", match["old"])
            self.previous(keys, match["old"])
            if not keys.issubset(self.updates):
                self.invalidate(keys)
            return
        match = _match(rf"(?P<subject>\S.*)(?<!\s)\s+should\s+no\s+longer\s+be\s+(?P<old>{_ATOM})", text)
        if match:
            keys = self.subject(match["subject"], match["old"])
            self.invalidate(keys)
            self.previous(keys, match["old"])
            return
        match = _match(rf"{_VERB}\s+(?P<subject>.+)", text)
        if match:
            keys, _ = self.subjects_joined(match["subject"])
            self.invalidate(keys)
            return
        # Reuse the established exact grammar only for recognized setting keys.
        candidate = self.extract(body)
        if candidate and not candidate.get("contextual") and not candidate.get("unfamiliar") and candidate["key"] in set(self.values) | set(self.entries):
            self.set(candidate["key"].replace("_", " "), candidate["value"], candidate["old"] or old,
                     explicit=candidate["explicit"])
            if candidate.get("aliases"):
                self.aliases[candidate["key"]] = candidate["aliases"]
                self.scope = set(candidate["aliases"]).intersection(self.entries) or {candidate["key"]}
                self.record(self.scope)
            return
        raise _Unparsed

    def subjects_joined(self, text):
        keys, value = set(), None
        text = re.sub(r"^both\s+", "", text, flags=re.I)
        for part in re.split(r"(?<!\s)\s+and\s+", text, flags=re.I):
            found, embedded = self.embedded_subject(part)
            keys.update(found)
            if embedded is not None:
                if value is not None:
                    raise _Unparsed
                value = embedded
        return keys, value

    def deferred(self, text):
        # Only setting-linked statements of missing information are accepted.
        # They supply no replacement, even when another document is mentioned.
        return bool(_match(
            r"(?:I\s+(?:haven't|have\s+not)\s+(?:supplied|specified|named|described|chosen|selected|picked|decided|settled)\b.+|"
            r"I\s+(?:still\s+)?need\s+to\s+(?:tell|provide)\b.+|"
            r"I(?:'ll|\s+will)\s+(?:confirm|specify|provide|explain|clarify)\b.+\b(?:later|soon)|"
            r"I'm\s+weighing\b.+\band\s+haven't\s+picked\b.+|"
            r"ask\s+me\s+(?:for|before)\b.+|"
            r"(?:the|my)\s+(?:new|replacement)\s+(?:window|language|format|value)\s+is\s+still\s+(?:undecided|to\s+be\s+decided)|"
            r"I\s+can't\s+specify\s+the\s+new\s+(?:format|value|language|style)\s+until\b.+|"
            r"(?:make|replace)\s+it\s+(?:match|with)\s+.+\b(?:haven't\s+shared|have\s+not\s+described)\b.+)", text))


def plan_corrections(text, entries, *, subjects, values, extract, request_body, polite_question):
    """Return a complete typed plan or None; incomplete plans never escape."""
    plan = _Plan(entries, subjects, values, extract, request_body)
    try:
        start = 0
        for boundary in [*_SPLIT.finditer(text), None]:
            end = boundary.start() if boundary else len(text)
            delimiter = boundary.group() if boundary else ""
            quote_end = end + 1 if delimiter in {".", "!", "?", ";"} else end
            clause = text[start:end].strip()
            if clause:
                if delimiter == "?" and not polite_question(clause):
                    raise _Unparsed
                if delimiter == "!" and not _match(r"thanks|thank\s+you|ok(?:ay)?|please", clause):
                    raise _Unparsed
                left = start + len(text[start:end]) - len(text[start:end].lstrip())
                plan.span, plan.required = (left, quote_end), []
                plan.clause(clause)
            start = boundary.end() if boundary else len(text)
    except _Unparsed:
        return None
    affected = set(plan.updates) | plan.unknown
    if plan.unknown:
        return {"candidates": [], "uncertain": sorted(affected.intersection(entries))}
    return {"candidates": [{"key": key, "value": value, "old": plan.old.get(key), "explicit": plan.explicit[key],
                            "span": plan.evidence[key], **({"aliases": plan.aliases[key]} if key in plan.aliases else {})}
                           for key, value in plan.updates.items()], "uncertain": []}
