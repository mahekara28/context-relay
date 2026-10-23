"""Finite multilingual preference instructions, with exact original evidence.

This is a bounded grammar, not a translator. It never creates custom keys or
authorization facts. Unparsed clauses invalidate the entire message plan.
"""
from __future__ import annotations

import re

from .memory_values import normalize_memory_value


_FORMATS = "CSV JSON JSONL TSV XML YAML TOML Markdown HTML PDF TXT XLSX".split()
_LANGUAGES = {
    "English": "english|ingles|inglés|anglais|anglaise|angrezi|angreji|अंग्रेजी|अंग्रेज़ी|इंग्लिश",
    "Hindi": "hindi|हिंदी|हिन्दी|हिंदी भाषा",
    "Spanish": "spanish|español|espanol|espagnol|espagnole|स्पेनिश",
    "French": "french|français|francais|française|frances|francés|फ्रेंच",
    "German": "german|alemán|aleman|allemand|allemande|जर्मन",
    "Bengali": "bengali|bengalí|बंगाली|बांग्ला",
    "Marathi": "marathi|मराठी", "Tamil": "tamil|तमिल", "Telugu": "telugu|तेलुगु",
    "Arabic": "arabic|árabe|arabe|अरबी", "Chinese": "chinese|chino|chinois|चीनी",
    "Japanese": "japanese|japonés|japones|japonais|जापानी",
    "Italian": "italian|italiano|italien|इतालवी",
    "Portuguese": "portuguese|portugués|portugues|portugais|पुर्तगाली",
    "Russian": "russian|ruso|russe|रूसी", "Korean": "korean|coreano|coréen|कोरियाई",
    "Dutch": "dutch|neerlandés|néerlandais|डच", "Turkish": "turkish|turco|turc|तुर्की",
    "Urdu": "urdu|उर्दू",
}
_STYLES = {
    "short": "short|chhota|chhote|chota|chote|छोटा|छोटे|corto|corta|cortas|court|courte|courtes",
    "brief": "brief|breve|breves|bref|brève|brèves",
    "concise": "concise|conciso|concisa|concisas|concis|concises|sankshipt|संक्षिप्त",
    "detailed": "detailed|detail se|detail mein|vistaar se|vistar se|विस्तार से|विस्तृत|detallado|detallada|detalladas|détaillé|détaillée|détaillées|detaille",
}
_SUBJECTS = {
    "export_format": r"export(?:[ _-]+format)?|exports?|export\s+ka\s+format|निर्यात(?:\s+(?:का\s+)?प्रारूप)?|एक्सपोर्ट(?:\s+(?:का\s+)?(?:फॉर्मेट|फ़ॉर्मैट|फ़ॉर्मेट|प्रारूप))?|exportaciones|formato\s+de\s+exportaci[oó]n|format\s+d['’]export(?:ation)?",
    "output_format": r"output[ _-]+format|output\s+ka\s+format|आउटपुट\s+(?:का\s+)?(?:फॉर्मेट|प्रारूप)|formato\s+de\s+salida|format\s+de\s+sortie",
    "response_language": r"(?:response|reply)[ _-]+language|(?:response|reply|jawab)\s+ki\s+(?:language|bhasha)|bhasha|भाषा|(?:जवाब(?:ों)?|उत्तर|प्रतिक्रिया)\s+की\s+भाषा|idioma(?:\s+de\s+(?:(?:las|tus|mis)\s+)?respuestas)?|langue(?:\s+(?:des|de\s+(?:tes|mes|vos))\s+r[eé]ponses)?",
    "response_style": r"(?:response|reply)[ _-]+style|(?:response|reply|jawab)\s+(?:ka|ki)\s+(?:style|andaaz)|(?:जवाब|उत्तर)\s+की\s+शैली|estilo(?:\s+de\s+(?:las\s+)?respuestas)?|style\s+des\s+r[eé]ponses",
}
_REPLIES = r"answers?|replies|responses?|reply|jawab|javaab|जवाब(?:ों)?|उत्तर|respuestas|r[eé]ponses"
_MARKER = re.compile(
    r"[\u0900-\u097f]|\b(?:ab\s+se|aaj\s+se|karo|kar\s+do|rakho|badlo|badal|galat|chahiye|jawab|bhasha|nahi|mat|samjhao|samjhaiye|"
    r"cambia|cambiar|usa|utiliza|responde|resp[oó]ndeme|responder|exporta|exportar|exportaciones|formato|idioma|respuestas|español|ingl[eé]s|traduce|traduis|traduire|anuvad|"
    r"r[eé]ponds|r[eé]pondez|utilise|utilisez|modifie|exporte|langue|r[eé]ponses|d[eé]sormais|dor[eé]navant|fran[cç]ais|je\s+(?:pr[eé]f[eè]re|veux|voudrais))\b|"
    r"\b(?:a\s+partir\s+de\s+ahora|en\s+adelante|por\s+favor|s['’]il\s+(?:te|vous)\s+pla[iî]t|format\s+(?:d['’]export(?:ation)?|de\s+sortie))\b",
    re.I,
)
# These messages discuss, quote, condition, or temporarily override instructions;
# they do not establish a standing preference, even if they contain English.
_EXCLUDED = re.compile(
    r"[`]|(?:^|[\n:;])\s*['‘\"“«]|[?¿]|"
    r"\b(?:translate|translation|traduce|traducir|traducci[oó]n|traduis|traduire|traduction|anuvad|"
    r"hypothetical|hypoth[eé]tique|hipot[eé]tico|imagine|imagina|sup[oó]n|suppose|supposons|agar|yadi|"
    r"dijo|dice|dijeron|escribi[oó]|seg[uú]n\s+(?:el|la)\s+(?:documento|informe)|a\s+dit|dit\s+que|a\s+[eé]crit|selon\s+(?:le|la)\s+(?:document|rapport|texte)|kaha|bola|"
    r"for\s+(?:now|today|this\s+(?:reply|response|task))|this\s+time|abhi\s+ke\s+liye|sirf\s+(?:aaj|is\s+baar|is\s+(?:jawab|reply|response)\s+ke\s+liye)|"
    r"solo\s+(?:hoy|esta\s+vez|para\s+esta\s+respuesta)|por\s+ahora|"
    r"pour\s+(?:l['’]instant|cette\s+r[eé]ponse)|cette\s+fois|aujourd['’]hui\s+seulement)\b|"
    r"(?:अनुवाद|अगर|यदि|काल्पनिक|उदाहरण|कहा|बोला|सिर्फ\s+आज|केवल\s+आज|इस\s+बार|अभी\s+के\s+लिए)|"
    r"(?:^|[.;\n])\s*(?:si\s|if\s|example\b|ejemplo\b|exemple\b)", re.I,
)
_PREFIX = re.compile(
    r"^(?:(?:please|actually|from\s+now\s+on|going\s+forward|ab\s+se|aaj\s+se|aage\s+se|आगे\s+से|अब\s+से|आज\s+से|ab|अब|कृपया|"
    r"a\s+partir\s+de\s+ahora|de\s+ahora\s+en\s+adelante|en\s+adelante|ahora|maintenant|por\s+favor|d[eé]sormais|dor[eé]navant|"
    r"[aà]\s+l['’]avenir|[aà]\s+partir\s+de\s+maintenant|s['’]il\s+(?:te|vous)\s+pla[iî]t)[,:\s]+)+", re.I,
)
_SUFFIX = re.compile(r"[,\s]+(?:please|por\s+favor|ab\s+se|from\s+now\s+on|en\s+adelante|de\s+ahora\s+en\s+adelante|a\s+partir\s+de\s+ahora|[aà]\s+partir\s+de\s+maintenant|d[eé]sormais)$", re.I)
_SPLIT = re.compile(r"[.;!।]\s*|\n+|\s+(?:and|y|et)\s+(?=(?:use|change|set|switch|reply|responde|resp[oó]ndeme|cambia|usa|utiliza|r[eé]ponds|utilise|modifie)\b)|(?:(?<=karo)|(?<=karna)|(?<=rakho)|(?<=do)|(?<=करो)|(?<=करना)|(?<=रखो)|(?<=दो)),?\s+(?:aur|और)\s+(?=(?:export|output|response|reply|jawab|जवाब|उत्तर|भाषा|निर्यात|आउटपुट|एक्सपोर्ट|हिंदी|अंग्रेजी|Hindi|English|use|reply|respond|set)\b)", re.I)
_COURTESY = re.compile(r"thanks|thank\s+you|please|gracias|merci|dhanyavad|धन्यवाद|ठीक\s+है|ok(?:ay)?", re.I)
_NO_CHANGE = re.compile(r"(?:(?:no\s+(?:cambies|modifiques|actualices|establezcas)\b|(?:ne\s+)?(?:change|modifie|actualise|définis)\s+pas\b)[^,;]+|[^,;]+(?:mat|मत)\s+(?:badlo|badalna|karo|karna|बदलो|बदलना|करो|करना|करें))", re.I)
_INTENT = re.compile(
    r"^(?:(?:no\s+|ne\s+|n['’])?(?:change|set|switch|use|keep|reply|respond|answer|cambia|cambies|actualiza|usa|uses|utiliza|"
    r"responde|contesta|establece|quiero|prefiero|utilise|utilisez|modifie|mets|r[eé]ponds|r[eé]pondez|"
    r"je\s+(?:veux|pr[eé]f[eè]re)|exporta|exporte)\b)|"
    r"\b(?:karo|rakho|badlo|chahiye|badal\s+do|ne\s+me\s+convient\s+plus|ya\s+no\s+(?:quiero|sirve)|"
    r"nahi\s+(?:chahiye|sahi)|sahi\s+nahi|galat|ya\s+no\s+me\s+sirve|ya\s+no\s+quiero|deben\s+cambiar|"
    r"doit\s+changer|doivent\s+changer|je\s+ne\s+veux\s+plus|wrong|outdated)\b|(?:करो|रखो|बदलो|चाहिए|बदलन[ाी]|गलत|सही\s+नहीं)", re.I,
)


def _rx(pattern):
    return re.compile(pattern, re.I)


# A pattern either names its subject or binds a narrowly specified built-in key.
# Broad captures are accepted only after full subject and finite-value checks.
_SUBJECT_WORDS = r"the|my|our|el|la|las|los|le|les|ma|mon|mes|tes|vos|mis|tus|mi|todas|toutes|mere|mera|meri|मेरे|मेरा|मेरी|future|futuras|futures|prochaines"
_SUBJECT_PATTERN = r"(?P<subject>(?:(?:" + _SUBJECT_WORDS + r")\s+)*(?:" + "|".join(_SUBJECTS.values()) + "|" + _REPLIES + r")(?:\s+(?:futures|futuras))?)"
_RULES = tuple((_rx(pattern.replace("(?P<subject>.+?)", _SUBJECT_PATTERN).replace("(?P<subject>.+)", _SUBJECT_PATTERN)), key) for pattern, key in (
    (r"(?P<value>.+?)\s+(?:samjhao|samjhaiye)", "response_style"),
    (rf"aage\s+ke\s+(?:{_REPLIES})\s+(?P<value>.+?)\s+(?:hi\s+)?rehne\s+chahiye", "response_style"),
    (r"(?P<subject>.+?)\s+(?P<old>.+?)\s+(?:ke\s+bajay|ki\s+jagah)\s+(?P<value>.+?)\s+(?:mein\s+)?(?:chahiye|hona\s+chahiye)", None),
    (r"(?P<subject>.+?)\s+(?P<old>.+?)\s+(?:की\s+जगह|के\s+बजाय)\s+(?P<value>.+?)\s+(?:में\s+)?(?:होने\s+चाहिए|होना\s+चाहिए|चाहिए)", None),
    (rf"(?:mujhe\s+)?(?P<value>.+?)\s+(?:mein|me)\s+(?:{_REPLIES})\s+(?:do|dena|dijiye|diya\s+karo)", "response_language"),
    (rf"(?:मुझे\s+)?(?P<value>.+?)\s+में\s+(?:{_REPLIES})\s+(?:दो|देना|दीजिए|दें|दिया\s+करें)", "response_language"),
    (rf"(?P<value>.+?)\s+(?:{_REPLIES})\s+(?:dena|do|देना|दो|दिया\s+करें)", "response_style"),
    (r"(?:para|pour)\s+(?P<subject>.+?)[,\s]+(?:prefiero|je\s+pr[eé]f[eè]re)\s+(?P<value>.+)", None),
    (r"quiero\s+que\s+(?P<value>.+?)\s+sea\s+(?P<subject>.+)", None),
    (r"(?:resp[oó]ndeme|cont[eé]stame)\s+en\s+(?P<value>.+)", "response_language"),
    (r"(?:r[eé]ponds-moi|r[eé]pondez-moi)\s+en\s+(?P<value>.+)", "response_language"),
    (r"(?P<subject>.+?)\s+(?:ko\s+)?(?P<old>.+?)\s+se\s+(?P<value>.+?)\s+(?:mein\s+)?(?:badlo|badal\s+do|karo|kar\s+do)", None),
    (r"(?P<subject>.+?)\s+(?:को\s+)?(?P<old>.+?)\s+से\s+(?P<value>.+?)\s+(?:में\s+)?(?:बदलो|बदल\s+दो|करो|कर\s+दो|करें)", None),
    (r"(?P<subject>.+?)\s+(?:ko\s+)?(?P<value>.+?)\s+(?:mein\s+)?(?:rakho|karo|kar\s+do|set\s+karo)", None),
    (r"(?P<subject>.+?)\s+(?:को\s+)?(?P<value>.+?)\s+(?:में\s+)?(?:रखो|करो|कर\s+दो|करें|रखें|सेट\s+करो)", None),
    (rf"(?:{_REPLIES})\s+(?P<value>.+?)\s+(?:mein|me)\s+(?:do|dena|dijiye)", "response_language"),
    (rf"(?P<value>.+?)\s+(?:mein|me)\s+(?:{_REPLIES})\s+(?:do|dena|dijiye)", "response_language"),
    (rf"(?P<old>.+?)\s+nahi[,\s]+(?P<value>.+?)\s+(?:mein|me)\s+(?:{_REPLIES})\s+(?:do|dena|dijiye)", "response_language"),
    (rf"(?:{_REPLIES})\s+(?P<value>.+?)\s+में\s+(?:दो|दीजिए|दें|देना)", "response_language"),
    (rf"(?P<value>.+?)\s+में\s+(?:{_REPLIES})\s+(?:दो|दीजिए|दें|देना)", "response_language"),
    (rf"(?P<old>.+?)\s+नहीं[,\s]+(?P<value>.+?)\s+में\s+(?:{_REPLIES})\s+(?:दो|दीजिए|दें|देना)", "response_language"),
    (rf"(?:{_REPLIES})\s+(?P<value>.+?)\s+(?:rakho|do|रखो|दो|दीजिए|दें)", "response_style"),
    (r"(?P<old>.+?)\s+(?:ki\s+jagah|के\s+बजाय|की\s+जगह)\s+(?P<value>.+?)\s+(?:mein\s+|में\s+)?(?:export|निर्यात|एक्सपोर्ट)\s+(?:karo|करो|करें)", "export_format"),
    (r"(?P<subject>.+?)\s+(?:ke\s+liye|के\s+लिए)\s+(?P<old>.+?)\s+(?:nahi|नहीं)[,\s]+(?P<value>.+?)\s+(?:use\s+karo|rakho|इस्तेमाल\s+करो|रखो)", None),
    (r"(?:cambia|actualiza|modifica)\s+(?P<subject>.+?)\s+de\s+(?P<old>.+?)\s+a\s+(?P<value>.+)", None),
    (r"(?:cambia|actualiza|establece|pon)\s+(?P<subject>.+?)\s+(?:a|en)\s+(?P<value>.+)", None),
    (r"(?:usa|utiliza|prefiero)\s+(?P<value>.+?)\s+para\s+(?P<subject>.+?)(?:\s+en\s+lugar\s+de\s+(?P<old>.+))?", None),
    (r"para\s+(?P<subject>.+?)[,\s]+(?:usa|utiliza)\s+(?P<value>.+?)(?:\s+en\s+lugar\s+de\s+(?P<old>.+))?", None),
    (r"(?:responde|contesta)\s+en\s+(?P<value>.+?)(?:\s+en\s+lugar\s+de\s+(?P<old>.+))?", "response_language"),
    (r"(?:responde|contesta)\s+(?:de\s+forma\s+)?(?P<value>.+)", "response_style"),
    (r"(?:exporta|exportar)\s+(?:en|como)\s+(?P<value>.+?)(?:\s+en\s+lugar\s+de\s+(?P<old>.+))?", "export_format"),
    (r"(?:change|modifie|actualise)\s+(?P<subject>.+?)\s+de\s+(?P<old>.+?)\s+[aà]\s+(?P<value>.+)", None),
    (r"(?:change|modifie|d[eé]finis|mets)\s+(?P<subject>.+?)\s+(?:en|[aà])\s+(?P<value>.+)", None),
    (r"(?:utilise|utilisez|emploie|je\s+pr[eé]f[eè]re)\s+(?P<value>.+?)\s+pour\s+(?P<subject>.+?)(?:\s+au\s+lieu\s+de\s+(?P<old>.+))?", None),
    (r"pour\s+(?P<subject>.+?)[,\s]+(?:utilise|utilisez)\s+(?P<value>.+?)(?:\s+au\s+lieu\s+de\s+(?P<old>.+))?", None),
    (r"(?:r[eé]ponds|r[eé]pondez)\s+en\s+(?P<value>.+?)(?:\s+au\s+lieu\s+de\s+(?P<old>.+))?", "response_language"),
    (r"(?:r[eé]ponds|r[eé]pondez)\s+(?:de\s+fa[cç]on\s+)?(?P<value>.+)", "response_style"),
    (r"exporte\s+en\s+(?P<value>.+?)(?:\s+au\s+lieu\s+de\s+(?P<old>.+))?", "export_format"),
    # English clauses may accompany another supported language in one message.
    (r"(?:change|switch|set)\s+(?P<subject>.+?)\s+from\s+(?P<old>.+?)\s+to\s+(?P<value>.+)", None),
    (r"(?:change|switch|set)\s+(?P<subject>.+?)\s+to\s+(?P<value>.+)", None),
    (r"(?:use|keep)\s+(?P<value>.+?)\s+for\s+(?P<subject>.+)", None),
    (r"(?:reply|respond|answer)\s+in\s+(?P<value>.+)", "response_language"),
))


def _canonical(key, value):
    if value is None:
        return None
    value = value.strip()
    if len(value) > 1 and (value[0], value[-1]) in {('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("«", "»")}:
        value = value[1:-1].strip()
    value = re.sub(r"^(?:el\s+|la\s+|le\s+|l['’])", "", value, flags=re.I)
    if key in {"export_format", "output_format"}:
        return next((v for v in _FORMATS if value.casefold() == v.casefold()), None)
    if key == "response_style":
        value = re.sub(r"^(?:(?:des|de|las)\s+)?(?:explications?|explicaciones)\s+", "", value, flags=re.I)
    table = _LANGUAGES if key == "response_language" else _STYLES
    return next((v for v, pattern in table.items() if re.fullmatch(pattern, value, re.I)), None)


def _subject(raw, value):
    raw = re.sub(r"^(?:(?:" + _SUBJECT_WORDS + r")\s+)+", "", raw.strip(), flags=re.I)
    raw = re.sub(r"\s+(?:futures|futuras)$", "", raw, flags=re.I)
    keys = {key for key, pattern in _SUBJECTS.items() if re.fullmatch(pattern, raw, re.I)}
    if re.fullmatch(_REPLIES, raw, re.I):
        keys.update(key for key in ("response_language", "response_style") if _canonical(key, value))
    return next(iter(keys)) if len(keys) == 1 else None


def _targets(text):
    keys = {key for key, pattern in _SUBJECTS.items() if re.search(r"(?<!\w)(?:" + pattern + r")(?!\w)", text, re.I)}
    named = bool(keys)
    if not keys.intersection({"response_language", "response_style"}) and (re.search(r"(?<!\w)(?:" + _REPLIES + r")(?!\w)", text, re.I) or re.search(r"\b(?:responde|contesta|r[eé]ponds|r[eé]pondez)\b", text, re.I)):
        cues = {key for key, table in (("response_language", _LANGUAGES), ("response_style", _STYLES))
                if any(re.search(r"(?<!\w)(?:" + value + r")(?!\w)", text, re.I) for value in table.values())}
        keys.update(cues or ("response_language", "response_style"))
    if re.search(r"\b(?:exporta|exportar|exporte)\b", text, re.I):
        keys.add("export_format")
    return keys, named


_QUOTES = re.compile(r'''"([^"\n]+)"|“([^”\n]+)”|«([^»\n]+)»|(?<!\w)'([^'\n]+)'(?!\w)|‘([^’\n]+)’''')
_HEDGE = re.compile(r"\b(?:maybe|perhaps|quiz[aá]s|tal\s+vez|shayad|peut[- ]être)\b|शायद", re.I)
_HISTORY = _rx(_SUBJECT_PATTERN + r"\s+(?:[eé]taient|[eé]tait|eran|era)\s+(?:en\s+)?(?P<old>.+)")
_FRAME = _rx(r"(?:sobre|pour|para)\s+" + _SUBJECT_PATTERN + r"\s*:\s*(?P<body>.+)")
_REFERENCE_SETTER = _rx(r"(?:quiero\s+que\s+sean|je\s+voudrais\s+qu['’](?:ils|elles)\s+soient)(?:\s+en)?\s+(?P<value>.+)")
_STYLE_REJECTION = _rx(r"(?P<value>.+?)\s+(?:" + _REPLIES + r")\s+(?:nahi\s+chahiye|नहीं\s+चाहिए)")
_COMBINED = tuple(_rx(pattern) for pattern in (
    rf"(?:mujhe\s+)?(?P<language>.+?)\s+mein\s+(?P<style>.+?)\s+(?:{_REPLIES})\s+(?:dena|do|dijiye)",
    rf"(?:मुझे\s+)?(?P<language>.+?)\s+में\s+(?P<style>.+?)\s+(?:{_REPLIES})\s+(?:देना|दो|दीजिए|दें)",
    r"(?:dame|quiero)\s+respuestas\s+(?P<style>.+?)\s+en\s+(?P<language>.+)",
    r"(?:r[eé]ponds(?:-moi)?|r[eé]pondez(?:-moi)?)\s+en\s+(?P<language>.+?)\s+avec\s+(?P<style>.+)",
))


def _unsafe_quotes(text):
    for match in _QUOTES.finditer(text):
        quoted = next(group for group in match.groups() if group is not None)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+-]{0,63}", quoted) and not any(_canonical(key, quoted) for key in _SUBJECTS):
            return True
    return False


def plan_multilingual(text, entries):
    """Return a complete plan, or None outside this bounded language detector.

    ``ignored`` prevents a caller's English fallback from promoting embedded
    instructions in foreign quotations, reported speech or temporary requests.
    Unknown instructions only dispute explicitly affected *existing* settings.
    """
    if not _MARKER.search(text):
        return None
    if _EXCLUDED.search(text) or _unsafe_quotes(text):
        return {"candidates": [], "uncertain": [], "ignored": True}
    candidates, affected, unresolved, incomplete = {}, set(), set(), False
    scope = None
    start = 0
    for boundary in [*_SPLIT.finditer(text), None]:
        end = boundary.start() if boundary else len(text)
        original = text[start:end]
        left, right = start + len(original) - len(original.lstrip()), end - len(original) + len(original.rstrip())
        clause = _SUFFIX.sub("", _PREFIX.sub("", original.strip())).strip()
        start = boundary.end() if boundary else len(text)
        if not clause or _COURTESY.fullmatch(clause) or _NO_CHANGE.fullmatch(clause):
            continue
        target, named = _targets(clause)
        found, intent = False, bool(_INTENT.search(_HEDGE.sub("", clause).strip(" ,:")))
        # Settings are short; long payloads are never parsed by the broad clause
        # captures. The original source stays intact for clarification/review.
        if len(clause) > 512:
            if intent:
                affected.update(target)
            incomplete = True
            continue
        frame = _FRAME.fullmatch(clause)
        if frame:
            key = _subject(frame["subject"], "")
            if key:
                scope = key, None, left
                clause, target, named = frame["body"], {key}, True
        historical = _HISTORY.fullmatch(clause)
        if historical:
            key = _subject(historical["subject"], historical["old"])
            if key and (old := _canonical(key, historical["old"])):
                scope = key, old, left
                continue
        rejection = _STYLE_REJECTION.fullmatch(clause)
        if rejection and _canonical("response_style", rejection["value"]):
            unresolved.add("response_style")
            affected.add("response_style")
            scope = "response_style", None, left
            continue
        parsed = []
        for pattern in _COMBINED:
            match = pattern.fullmatch(clause)
            if match:
                target, intent = {"response_language", "response_style"}, True
                language, style = _canonical("response_language", match["language"]), _canonical("response_style", match["style"])
                if language and style:
                    parsed = [("response_language", language, None), ("response_style", style, None)]
                break
        reference = _REFERENCE_SETTER.fullmatch(clause)
        if reference and scope:
            key, old, evidence_start = scope
            value = _canonical(key, reference["value"])
            target, intent = {key}, True
            if value:
                parsed, left = [(key, value, old)], evidence_start
        for pattern, fixed_key in _RULES:
            if parsed:
                break
            match = pattern.fullmatch(clause)
            if match is None:
                continue
            data = match.groupdict()
            key = fixed_key or _subject(data["subject"], data["value"])
            if key is None:
                continue
            intent = True
            if not named and (not target or key in target):
                target = {key}
            value, old = _canonical(key, data["value"]), _canonical(key, data.get("old"))
            if value is None or data.get("old") is not None and old is None:
                continue
            parsed = [(key, value, old)]
            break
        if _HEDGE.search(clause):
            parsed = []
        if parsed and scope and scope[0] not in {item[0] for item in parsed}:
            # A different intervening setting breaks the old pronoun binding.
            # Retain its identity only for clarification if a later clause fails.
            affected.add(scope[0])
            scope = None
        for key, value, old in parsed:
            if scope and scope[0] == key:
                left = min(left, scope[2])
            candidate = {"key": key, "value": normalize_memory_value(key, value), "old": old,
                         "explicit": True, "start": left, "end": right, "span": (left, right)}
            previous = candidates.get(key)
            if previous and (previous["value"], previous["old"]) != (value, old):
                incomplete = True
            else:
                candidates[key] = candidate
                unresolved.discard(key)
            found = True
        if intent:
            affected.update(target)
        if not found:
            incomplete = True
    if incomplete or unresolved:
        return {"candidates": [], "uncertain": sorted((affected | unresolved | set(candidates)).intersection(entries))}
    return {"candidates": list(candidates.values()), "uncertain": []}
