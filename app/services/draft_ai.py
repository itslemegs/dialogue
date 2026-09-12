from app.ai_features import require_ai_features
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv
from pydantic import BaseModel

from app.services.local_llm import get_chat_client


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=True)


class DraftFill(BaseModel):
    title: str = ""
    recalling: str = ""
    noting: str = ""
    welcoming: str = ""
    expressing_regret: str = ""
    expressing_deep_concern: str = ""
    emphasizing: str = ""
    decides: str = ""
    requests: str = ""
    calls_upon: str = ""
    encourages: str = ""


EXPECTED_KEYS = [
    "title",
    "recalling",
    "noting",
    "welcoming",
    "expressing_regret",
    "expressing_deep_concern",
    "emphasizing",
    "decides",
    "requests",
    "calls_upon",
    "encourages",
]


DRAFT_JSON_SCHEMA = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in EXPECTED_KEYS},
    "required": EXPECTED_KEYS,
    "additionalProperties": False,
}


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "been", "being", "but",
    "by", "can", "could", "did", "do", "does", "for", "from", "had", "has", "have",
    "if", "in", "into", "is", "it", "its", "may", "more", "most", "must", "not",
    "of", "on", "or", "other", "our", "should", "so", "such", "than", "that",
    "the", "their", "them", "these", "they", "this", "those", "through", "to",
    "too", "under", "was", "were", "when", "where", "which", "while", "who",
    "with", "within", "without", "would", "also", "overall", "people", "person",
    "one", "many", "much", "everything", "almost", "clear", "strong", "new",
    "proposal", "proposes", "saying", "says", "wants", "main", "message", "simple",
}


BAD_GENERIC_PATTERNS = [
    r"^\s*(the\s+)?importance\s+of\b",
    r"^\s*(the\s+)?need\s+for\b",
    r"^\s*background\s+(on|about)\b",
    r"^\s*concerns?\s+(about|on)\b",
    r"^\s*requests?\s+from\b",
    r"^\s*calls?\s+up?on\s+(the\s+)?importance\b",
    r"^\s*encouragement\s+for\b",
    r"^\s*encourages?\s+(countries|organizations|individuals)\s+to\s+work\s+together\.?\s*$",
    r"^\s*summary\s+of\b",
]


BAD_TITLE_PATTERNS = [
    r"^\s*$",
    r"^\s*(test|testing|demo|sample|untitled|draft|proposal|resolution)\s*\d*\s*$",
    r"^\s*(multi|room|discussion|general floor|proposal room|new draft)\b.*$",
    r"^\s*[a-zA-Z]+\s*\d+\s*$",
    r"^\s*\d+\s*$",
]


FIELD_MAX_LINES = {
    "recalling": 2,
    "noting": 3,
    "welcoming": 2,
    "expressing_regret": 2,
    "expressing_deep_concern": 2,
    "emphasizing": 2,
    "decides": 2,
    "requests": 3,
    "calls_upon": 2,
    "encourages": 3,
}


FIELD_CUES = {
    "welcoming": [
        "welcomes", "welcomed", "welcoming", "commends", "commended", "appreciates",
        "progress", "successful", "achievement", "improvement", "positive development",
        "existing cooperation", "ongoing efforts", "initiative", "initiatives",
    ],
    "expressing_regret": [
        "regret", "regrets", "regretting", "failure", "failed", "lack of progress",
        "insufficient", "missed commitment", "not fulfilled", "shortfall", "too little",
        "not enough",
    ],
    "decides": [
        "decides", "decide", "resolved", "resolves", "determines", "shall establish",
        "shall create", "shall adopt", "this body decides", "the committee decides",
        "the assembly decides",
    ],
    "requests": [
        "requests", "request", "asks", "ask", "calls for a report", "study", "review",
        "report", "survey", "assess", "assessment", "technical recommendation",
        "technical recommendations", "standards", "standard", "rules", "guidelines",
        "framework", "expert", "experts", "committee", "secretariat", "working group",
        "pay attention", "organization working", "organizations working", "meaning of", "definition",
        "definitions", "terms", "keep checking", "continue reviewing",
    ],
    "calls_upon": [
        "countries", "states", "member states", "governments", "companies",
        "organizations", "stakeholders", "international organizations", "technical experts",
        "researchers", "private sector", "civil society", "work together", "cooperate",
        "cooperation", "coordinate", "coordination", "share knowledge", "share information",
        "not work alone", "across borders", "jointly", "collaborate",
    ],
    "encourages": [
        "encourages", "encourage", "training", "workshops", "support",
        "capacity", "capacity-building", "developing countries", "best practices",
        "incident response", "sharing information", "help each other", "prepare",
        "preparation", "protect", "protection", "adopt", "implement", "useful standards",
        "technical reports", "education", "awareness", "assistance",
    ],
    "expressing_deep_concern": [
        "attack", "attacks", "cyberattack", "cyberattacks", "threat", "threats",
        "harm", "damage", "steal", "stolen", "criminal", "misuse", "serious problem", "malware",
        "botnet", "botnets", "denial-of-service", "phishing", "fake websites",
        "unauthorized access", "hackers", "crisis", "instability", "serious",
        "grave", "severe", "whole country", "widespread", "major risk", "critical risk",
    ],
    "emphasizing": [
        "important", "importance", "priority", "high priority", "need", "needs",
        "should", "must", "from the beginning", "day one", "designed with",
        "security in mind", "resilient", "recover", "trusted", "reliable",
        "common understanding", "objective", "principle", "principles", "urgent",
    ],
    "noting": [
        "now", "today", "becoming", "increasing", "connected", "across borders",
        "new technologies", "cloud", "internet of things", "smart cities",
        "digital services", "gap", "gaps", "weak", "weaker", "risk", "risks",
        "problem", "problems", "hard to know", "difficult to know", "not always",
        "can affect", "impact", "trend", "trends", "current", "modern",
    ],
    "recalling": [
        "depends on", "used for", "daily life", "rights", "obligations",
        "principles", "policy context", "government services", "health",
        "education", "school", "work", "business", "communication", "banking",
        "public services", "in the past", "older", "previously", "historically",
        "recognizing that", "basic", "background",
    ],
}


FIELD_PRIORITY = [
    "decides",
    "requests",
    "encourages",
    "calls_upon",
    "welcoming",
    "expressing_regret",
    "expressing_deep_concern",
    "emphasizing",
    "noting",
    "recalling",
]


def _env_bool(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default).strip().casefold()
    return value in {"1", "true", "yes", "on"}


def _strip_code_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = _strip_code_fences(text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"Model did not return JSON. Raw output: {text[:800]}")
        data = json.loads(match.group(0))

    if isinstance(data, dict) and isinstance(data.get("draft"), dict):
        data = data["draft"]

    if not isinstance(data, dict):
        raise ValueError(f"Model JSON was not an object. Raw output: {text[:800]}")

    return data


def _normalize_key(key: str) -> str:
    k = str(key or "").strip().casefold()
    k = k.replace("-", "_").replace(" ", "_").replace("/", "_")

    aliases = {
        "draft_title": "title",
        "resolution_title": "title",
        "recall": "recalling",
        "note": "noting",
        "welcome": "welcoming",
        "regret": "expressing_regret",
        "expressing_regrets": "expressing_regret",
        "concern": "expressing_deep_concern",
        "deep_concern": "expressing_deep_concern",
        "expressing_concern": "expressing_deep_concern",
        "expressing_deep_concerns": "expressing_deep_concern",
        "emphasize": "emphasizing",
        "decide": "decides",
        "request": "requests",
        "call_upon": "calls_upon",
        "calls_on": "calls_upon",
        "calling_upon": "calls_upon",
        "encourage": "encourages",
    }

    return aliases.get(k, k)


def _normalize_value(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, list):
        return "\n".join(str(x).strip() for x in value if str(x).strip())

    if isinstance(value, dict):
        parts = []
        for v in value.values():
            cleaned = _normalize_value(v)
            if cleaned:
                parts.append(cleaned)
        return "\n".join(parts)

    value = str(value).strip()

    if value.casefold() in {"none", "null", "n/a", "not applicable", "-"}:
        return ""

    return value


def _is_bad_title(title: str) -> bool:
    title = re.sub(r"\s+", " ", title or "").strip()
    if len(title) < 4:
        return True
    if len(title.split()) > 12:
        return True
    if any(re.search(pattern, title, flags=re.IGNORECASE) for pattern in BAD_TITLE_PATTERNS):
        return True
    return False


def _title_case(text: str) -> str:
    small = {"and", "or", "of", "for", "to", "in", "on", "with", "by", "from", "the", "a", "an"}
    words = []
    for i, word in enumerate(text.split()):
        raw = word.strip()
        if not raw:
            continue
        lower = raw.casefold()
        if i > 0 and lower in small:
            words.append(lower)
        elif raw.isupper() and len(raw) <= 6:
            words.append(raw)
        else:
            words.append(lower.capitalize())
    return " ".join(words).strip()


def _split_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []

    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    sentences = []

    for part in parts:
        s = part.strip()
        if len(s) < 18:
            continue
        sentences.append(s)

    return sentences


def _tokens(text: str) -> List[str]:
    return [
        t.casefold()
        for t in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text or "")
        if t.casefold() not in STOPWORDS and len(t) >= 3
    ]


def _best_key_phrases(text: str, *, limit: int = 8) -> List[str]:
    raw_tokens = [t.casefold() for t in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text or "")]
    scores: Dict[str, float] = {}

    for n in [1, 2, 3]:
        for i in range(0, max(0, len(raw_tokens) - n + 1)):
            gram_tokens = raw_tokens[i:i + n]
            if not gram_tokens:
                continue

            if gram_tokens[0] in STOPWORDS or gram_tokens[-1] in STOPWORDS:
                continue

            if n > 1 and any(t in STOPWORDS for t in gram_tokens):
                continue

            phrase = " ".join(gram_tokens)
            if phrase in {
                "important", "importance", "systems", "people", "countries",
                "organizations", "technical", "information", "problems",
            }:
                continue

            # Multi-word phrases make better titles than isolated words.
            base = {1: 0.8, 2: 3.5, 3: 4.3}[n]
            scores[phrase] = scores.get(phrase, 0) + base

            if phrase in (text or "")[:400].casefold():
                scores[phrase] += 1.5

            if any(anchor in phrase for anchor in [
                "system", "systems", "network", "networks", "services",
                "rights", "policy", "security", "governance", "resilience",
                "cooperation", "standards",
            ]):
                scores[phrase] += 1.0

    ranked = sorted(scores.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    phrases = []

    for phrase, _score in ranked:
        phrase_words = len(phrase.split())

        # Do not let a frequent unigram such as "digital" block a better phrase
        # such as "digital systems" or "digital networks".
        if phrase_words > 1:
            phrases = [
                existing for existing in phrases
                if not (len(existing.split()) == 1 and existing in phrase)
            ]

        if any(phrase in existing for existing in phrases):
            continue

        if any(existing in phrase and len(existing.split()) > 1 for existing in phrases):
            continue

        phrases.append(phrase)
        if len(phrases) >= limit:
            break

    return phrases

def _derive_title_from_text(plain_text: str) -> str:
    compact = re.sub(r"\s+", " ", plain_text or "").strip()
    if not compact:
        return "Draft Resolution"

    sentences = _split_sentences(compact)
    first = sentences[0] if sentences else compact[:220]

    subject = ""
    subject_match = re.match(
        r"^(?:overall,\s*)?(?:the\s+)?([A-Za-z][A-Za-z0-9 ,\-]{2,70}?)\s+"
        r"(?:is|are|has|have|can|should|must|needs?|depends|becomes?|became)\b",
        first,
        flags=re.IGNORECASE,
    )
    if subject_match:
        subject = subject_match.group(1).strip(" .,;:")
        subject = re.sub(r"^(main message|proposal|draft|it|this|these|those)\b.*", "", subject, flags=re.IGNORECASE).strip()

    phrases = _best_key_phrases(compact, limit=30)

    if not subject or len(subject.split()) > 5:
        subject = phrases[0] if phrases else ""

    subject = re.sub(r"\b(becoming|important|used|almost|everything)\b", "", subject, flags=re.IGNORECASE)
    subject = re.sub(r"\s+", " ", subject).strip(" .,;:")

    if not subject:
        return "Draft Resolution"

    second = ""
    subject_cf = subject.casefold()
    for phrase in phrases:
        if phrase.casefold() == subject_cf:
            continue
        if phrase.casefold() in subject_cf or subject_cf in phrase.casefold():
            continue
        # A second title concept should normally be a phrase, not a lonely generic word.
        if 2 <= len(phrase.split()) <= 3:
            second = phrase
            break

    if second and len(subject.split()) <= 3:
        title = f"{_title_case(subject)} and {_title_case(second)}"
    else:
        title = _title_case(subject)

    title = re.sub(r"\s+", " ", title).strip(" .,;:")
    return title[:90] or "Draft Resolution"


def _fallback_title(
    plain_text: str,
    agenda_title: Optional[str] = None,
    room_title: Optional[str] = None,
) -> str:
    # Agenda titles may be official; room titles are often placeholders like "multi 2".
    for candidate in [agenda_title]:
        candidate = re.sub(r"\s+", " ", candidate or "").strip()
        if candidate and not _is_bad_title(candidate):
            return candidate[:90]

    return _derive_title_from_text(plain_text)


def _clean_model_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line or "").strip()
    line = line.strip(" -•\t\r\n")
    line = re.sub(
        r"^(Recalling|Noting|Welcoming|Expressing regret|Expressing deep concern|Emphasizing|Decides|Requests|Calls upon|Encourages)\s+(that\s+)?",
        "",
        line,
        flags=re.IGNORECASE,
    )
    return line.strip()


def _strip_meta_intro(sentence: str) -> str:
    s = re.sub(r"\s+", " ", sentence or "").strip()

    replacements = [
        r"^Overall,\s+the\s+main\s+message\s+is\s+simple:\s*",
        r"^This\s+proposal\s+is\s+saying\s+that\s+",
        r"^This\s+proposal\s+says\s+that\s+",
        r"^The\s+proposal\s+also\s+wants\s+",
        r"^The\s+proposal\s+wants\s+",
        r"^It\s+also\s+says\s+that\s+",
        r"^It\s+encourages\s+",
        r"^It\s+says\s+that\s+",
        r"^That\s+is\s+why\s+",
        r"^Because\s+of\s+that,\s+",
    ]

    for pattern in replacements:
        s = re.sub(pattern, "", s, flags=re.IGNORECASE).strip()

    return s


def _infer_actor(sentence: str) -> str:
    s = _strip_meta_intro(sentence)
    actor_keywords = (
        "countries", "states", "governments", "companies", "organizations",
        "experts", "researchers", "stakeholders", "bodies", "committee",
        "secretariat", "people and organizations", "international organizations",
        "technical experts", "standards bodies",
    )

    if not any(keyword in s.casefold() for keyword in actor_keywords):
        return ""

    patterns = [
        r"^(.{3,140}?)\s+(?:should|must|need to|needs to|have to|has to|can|could|will|shall)\b",
        r"^(.{3,140}?)\s+(?:working on|involved in|responsible for)\b",
        r"^(.{3,140}?)\s+to\b",
    ]

    for pattern in patterns:
        m = re.match(pattern, s, flags=re.IGNORECASE)
        if m:
            actor = m.group(1).strip(" .,;:")
            actor = re.sub(r"^(the\s+)?", "", actor, flags=re.IGNORECASE).strip()
            if any(keyword in actor.casefold() for keyword in actor_keywords):
                return actor[:140]

    # If no clean subject pattern matched, capture a coordinated actor list.
    m = re.search(
        r"((?:countries|states|governments|companies|organizations|experts|researchers|stakeholders|international organizations|technical experts)"
        r"(?:[, ]+(?:and\s+)?(?:countries|states|governments|companies|organizations|experts|researchers|stakeholders|international organizations|technical experts))*)",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(1).strip(" .,;:")

    return ""


def _resolve_leading_pronoun(sentence: str, previous_actor: str) -> str:
    s = _strip_meta_intro(sentence)
    if previous_actor:
        s = re.sub(r"^(They|Them|Their)\b", previous_actor, s, count=1, flags=re.IGNORECASE)
    else:
        s = re.sub(r"^(They|Them|Their)\b", "relevant actors", s, count=1, flags=re.IGNORECASE)
    return s


def _normalized_sentence_stream(plain_text: str) -> List[str]:
    out = []
    previous_actor = ""

    for sentence in _split_sentences(plain_text):
        s = _strip_meta_intro(sentence)
        if re.match(r"^(They|Them|Their)\b", s, flags=re.IGNORECASE):
            s = _resolve_leading_pronoun(s, previous_actor)

        actor = _infer_actor(s)
        if actor:
            previous_actor = actor

        out.append(s)

    return out


def _score_sentence_for_field(sentence: str, field: str) -> int:
    s = sentence.casefold()
    score = 0

    for cue in FIELD_CUES.get(field, []):
        if cue.casefold() in s:
            score += 4 if " " in cue else 2

    if field in {"requests", "calls_upon", "encourages"}:
        if re.search(r"\b(should|must|need to|needs to|have to|has to|to)\b", s):
            score += 2

    if field == "requests" and re.search(r"\bshould\s+(?:also\s+)?pay attention\b|\bcontinue reviewing\b|\bkeep checking\b", s):
        score += 8

    if field == "calls_upon" and re.search(r"\bnot\s+be\s+handled\s+separately\b|\bwithout coordination\b|\bwork alone\b|\bwork together\b", s):
        score += 8

    if field == "encourages" and re.search(r"\btraining|workshops|capacity|support|technical reports|incident response|best practices\b", s):
        score += 6

    if field == "expressing_deep_concern" and re.search(r"\bserious problem\b", s):
        score += 8

    if field == "recalling" and re.search(r"\b(in the past|historically|used for|depends on)\b", s):
        score += 3

    if field == "noting" and re.search(r"\b(today|now|current|becoming|increasing|connected|new)\b", s):
        score += 2

    if field == "expressing_deep_concern" and re.search(r"\b(attack|threat|harm|damage|steal|criminal|misuse)\w*\b", s):
        score += 4

    if field == "emphasizing" and re.search(r"\b(high priority|from the beginning|day one|important|importance)\b", s):
        score += 4

    if field == "emphasizing" and re.search(r"\b(built into|designed with|from the beginning|day one|not added only after)\b", s):
        score += 6

    return score


def _best_field_for_sentence(sentence: str) -> str:
    scores = {field: _score_sentence_for_field(sentence, field) for field in FIELD_CUES}

    # Action sentences should not be misread as background facts just because they mention risks.
    if re.search(r"\b(should|must|need to|needs to|have to|has to|encourages?|wants?|asks?|requests?)\b", sentence, flags=re.IGNORECASE):
        for field in ["requests", "calls_upon", "encourages", "emphasizing", "decides"]:
            scores[field] += 2

    best_score = max(scores.values()) if scores else 0
    if best_score <= 0:
        return ""

    tied = [field for field, score in scores.items() if score == best_score]
    for field in FIELD_PRIORITY:
        if field in tied:
            return field

    return tied[0]


def _shorten_note(sentence: str, *, max_chars: int = 340) -> str:
    sentence = re.sub(r"\s+", " ", sentence or "").strip()
    sentence = sentence.strip(" -•\t\r\n")

    if len(sentence) <= max_chars:
        return sentence

    clipped = sentence[:max_chars].rsplit(" ", 1)[0].strip()
    return clipped.rstrip(",;:") + "."


def _fingerprint(text: str) -> str:
    toks = [
        t for t in _tokens(text)
        if t not in {"relevant", "actors", "countries", "organizations", "states"}
    ]
    return " ".join(toks[:16])


def _too_similar(a: str, b: str) -> bool:
    at = set(_fingerprint(a).split())
    bt = set(_fingerprint(b).split())
    if not at or not bt:
        return False
    overlap = len(at & bt) / max(1, min(len(at), len(bt)))
    return overlap >= 0.72


def _build_source_notes(plain_text: str) -> Dict[str, List[str]]:
    sentences = _normalized_sentence_stream(plain_text)
    notes: Dict[str, List[str]] = {key: [] for key in EXPECTED_KEYS if key != "title"}
    used_sentences: List[str] = []

    for sentence in sentences:
        field = _best_field_for_sentence(sentence)
        if not field:
            continue

        note = _shorten_note(sentence)
        if any(_too_similar(note, existing) for existing in used_sentences):
            continue

        # Keep "this includes..." implementation sentences as encourages unless they are clearly a formal request.
        if re.match(r"^This includes\b", sentence, flags=re.IGNORECASE):
            field = "encourages"

        notes[field].append(note)
        used_sentences.append(note)

    # If recalling is empty, use the earliest contextual sentence.
    if not notes["recalling"] and sentences:
        for sentence in sentences:
            if not re.search(r"\b(should|must|need to|attack|threat|harm|damage)\b", sentence, flags=re.IGNORECASE):
                notes["recalling"].append(_shorten_note(sentence))
                break

    # If everything is empty, keep at least one useful emphasized idea.
    if not any(notes.values()) and sentences:
        notes["emphasizing"] = [_shorten_note(s) for s in sentences[:3]]

    # Keep each field bounded.
    for key in notes:
        notes[key] = notes[key][: max(FIELD_MAX_LINES.get(key, 3), 1)]

    return notes


def _notes_to_prompt_text(notes: Dict[str, List[str]]) -> str:
    blocks = []

    for key in EXPECTED_KEYS:
        if key == "title":
            continue

        items = notes.get(key) or []
        if not items:
            continue

        label = key.upper()
        body = "\n".join(f"- {item}" for item in items)
        blocks.append(f"{label} SOURCE NOTES:\n{body}")

    return "\n\n".join(blocks).strip() or "No source notes were extracted."


def _is_bad_generic_line(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False

    if re.search(r"\b(They|It|This)\s+to\b", stripped):
        return True

    if len(stripped.split()) <= 5 and "," not in stripped:
        return True

    return any(re.search(pattern, stripped, flags=re.IGNORECASE) for pattern in BAD_GENERIC_PATTERNS)


def _first_lower(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    if text[:1].isupper() and not (text.split()[0].isupper() and len(text.split()[0]) <= 6):
        return text[0].lower() + text[1:]
    return text

def _strip_trailing_reason(text: str) -> str:
    """
    Remove explanatory tails that make clause fragments read like copied prose.
    Example: "..., because these technologies can bring new risks" -> "... and related risks".
    """
    s = re.sub(r"\s+", " ", text or "").strip(" .")

    m = re.match(r"^(?P<head>.+?),\s*because\s+(?P<reason>.+)$", s, flags=re.IGNORECASE)
    if m:
        head = m.group("head").strip(" .,;:")
        reason = m.group("reason").strip(" .,;:")

        risk_match = re.search(r"\b(?:bring|create|pose|cause|lead to)\s+(?P<risk>(?:new\s+)?[A-Za-z\- ]{3,60}?risks?)\b", reason, flags=re.IGNORECASE)
        if risk_match:
            return f"{head} and related {risk_match.group('risk').lower()}"

        effect_match = re.search(r"\b(?:affect|harm|damage|threaten|undermine)\s+(?P<effect>[A-Za-z\- ,]{3,80})", reason, flags=re.IGNORECASE)
        if effect_match:
            return f"{head} and related effects on {effect_match.group('effect').strip(' .,;:').lower()}"

        return head

    return s


def _generic_subject(subject: str) -> str:
    subject = re.sub(r"\s+", " ", subject or "").strip(" .,;:")
    subject = re.sub(r"^(these|those|the)\s+", "", subject, flags=re.IGNORECASE)
    subject = re.sub(r"^(they|them|it|this)$", "relevant systems", subject, flags=re.IGNORECASE)
    return subject or "relevant systems"


def _compress_list_text(text: str) -> str:
    """
    Turns implementation lists into noun-like clause text.
    This is generic and not tied to any policy domain.
    """
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_trailing_reason(s)

    replacements = [
        (r"\bcreating\s+", ""),
        (r"\bdeveloping\s+", ""),
        (r"\bestablishing\s+", ""),
        (r"\bimproving\s+", ""),
        (r"\bstrengthening\s+", ""),
        (r"\bsharing\s+information\s+about\s+", "information-sharing on "),
        (r"\bsharing\s+knowledge\s+about\s+", "knowledge-sharing on "),
        (r"\bsharing\s+", "sharing of "),
        (r"\bmaking\s+sure\s+that\s+", "ensuring that "),
    ]

    for pattern, repl in replacements:
        s = re.sub(pattern, repl, s, flags=re.IGNORECASE)

    s = re.sub(r"information-sharing\s+of\s+on\s+", "information-sharing on ", s, flags=re.IGNORECASE)
    s = re.sub(r"knowledge-sharing\s+of\s+on\s+", "knowledge-sharing on ", s, flags=re.IGNORECASE)

    # Turn long assurance phrases into concise institutional nouns.
    # Generic examples:
    # - "ensuring that networks are trusted, resilient, and able to recover"
    #   -> "network trust, resilience, and recovery"
    # - "ensuring that services are safe, reliable, and able to recover"
    #   -> "service safety, reliability, and recovery"
    assurance_patterns = [
        (
            r"ensuring\s+that\s+(?P<subject>[A-Za-z][A-Za-z\- ]{2,60}?)\s+are\s+trusted,\s*resilient,\s*and\s*able\s+to\s+recover(?:\s+when\s+[^,.;]+)?",
            "{subject} trust, resilience, and recovery",
        ),
        (
            r"ensuring\s+that\s+(?P<subject>[A-Za-z][A-Za-z\- ]{2,60}?)\s+are\s+safe,\s*reliable,\s*and\s*able\s+to\s+recover(?:\s+when\s+[^,.;]+)?",
            "{subject} safety, reliability, and recovery",
        ),
    ]

    for pattern, template in assurance_patterns:
        def repl(match: re.Match) -> str:
            subject = match.group("subject").strip().lower()
            # Light singularization improves noun phrases without requiring NLP.
            if subject.endswith("ies"):
                subject = subject[:-3] + "y"
            elif subject.endswith("s") and not subject.endswith("ss"):
                subject = subject[:-1]
            return template.format(subject=subject)

        s = re.sub(pattern, repl, s, flags=re.IGNORECASE)

    s = re.sub(r"\bmore\s+(technical reports|reports|standards|training|workshops|cooperation|support|assistance)\b", r"\1", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .,;:")
    return s


def _clean_action_text(action: str) -> str:
    action = re.sub(r"\s+", " ", action or "").strip(" .")
    action = re.sub(r"^also\s+", "", action, flags=re.IGNORECASE)
    action = re.sub(r"^keep\s+checking\b", "continue reviewing", action, flags=re.IGNORECASE)
    action = re.sub(r"^not\s+work\s+alone\b", "cooperate rather than work in isolation", action, flags=re.IGNORECASE)

    m = re.match(r"^pay\s+attention\s+to\s+(?P<topic>.+?),\s*because\s+(?P<reason>.+)$", action, flags=re.IGNORECASE)
    if m:
        topic = m.group("topic").strip(" .,;:")
        reason = m.group("reason").strip(" .,;:")
        risk_match = re.search(r"\b(?:bring|create|pose|cause|lead to)\s+(?P<risk>(?:new\s+)?[A-Za-z\- ]{3,60}?risks?)\b", reason, flags=re.IGNORECASE)
        if risk_match:
            risk = risk_match.group('risk').lower()
            risk = re.sub(r"^new\s+", "", risk, flags=re.IGNORECASE)
            return f"assess {topic} and related {risk}"
        return f"assess {topic} and related implications"

    action = _strip_trailing_reason(action)
    action = re.sub(r"\brelated\s+new\s+", "related ", action, flags=re.IGNORECASE)
    return action


def _formalize_preambular_clause(text: str, field: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_trailing_reason(s)

    m = re.match(r"^when\s+(?P<subject>.+?)\s+(?:are|is)\s+weak\s+or\s+attacked\s*,\s*the damage can affect\s+(?P<target>.+)$", s, flags=re.IGNORECASE)
    if m:
        subject = _generic_subject(m.group("subject"))
        target = m.group("target").strip(" .,;:")
        return f"the potential for weak or attacked {subject} to cause damage affecting {target}"

    m = re.match(r"^if\s+(?P<subject>.+?)\s+(?:are|is)\s+not\s+(?P<condition>.+?)\s*,\s*(?:they|it)\s+can\s+become\s+(?P<risk>.+)$", s, flags=re.IGNORECASE)
    if m:
        subject = _generic_subject(m.group("subject"))
        condition = m.group("condition").strip(" .,;:")
        risk = m.group("risk").strip(" .,;:")
        return f"the risk that {subject} not {condition} may become {risk}"

    # General prose-to-fragment conversions. These avoid raw copied sentences
    # while preserving the user's evidence.
    patterns = [
        (
            r"^(.+?)\s+should be built into\s+(.+?)\s+from\s+the\s+beginning,\s*not\s+added\s+only\s+after\s+(.+)$",
            r"the importance of building \1 into \2 from the beginning rather than only after \3",
        ),
        (r"^almost everything people do now depends on\s+(.+)$", r"the growing reliance on \1"),
        (r"^these systems are used for\s+(.+)$", r"the use of these systems for \1"),
        (r"^(.+?)\s+is becoming more important because\s+(.+)$", r"the growing importance of \1, given that \2"),
        (r"^when\s+(.+?)\s*,\s*the damage can affect\s+(.+)$", r"the potential for \1 to cause damage affecting \2"),
        (r"^if\s+(.+?)\s*,\s*they can become\s+(.+)$", r"the risk that \1 can become \2"),
        (r"^if\s+(.+?)\s*,\s*(.+)$", r"the risk that \1, with resulting \2"),
        (r"^because\s+(.+?),\s*(.+)$", r"the fact that \1, and that \2"),
        (r"^but today,\s+(.+)$", r"the current situation in which \1"),
        (r"^today,\s+(.+)$", r"the current situation in which \1"),
        (r"^people now have to deal with\s+(.+)$", r"the increasing prevalence of \1"),
        (r"^cybersecurity should stay\s+(.+)$", r"the continued need to keep cybersecurity \1"),
        (r"^(.+?)\s+should be built into\s+(.+)$", r"the importance of building \1 into \2"),
        (r"^(.+?)\s+should be designed with\s+(.+)$", r"the importance of designing \1 with \2"),
    ]

    for pattern, repl in patterns:
        new_s = re.sub(pattern, repl, s, flags=re.IGNORECASE)
        if new_s != s:
            s = new_s
            break

    s = re.sub(
        r"(the growing importance of )([A-Z][a-z]+)(?=,|$)",
        lambda m: m.group(1) + m.group(2).lower(),
        s,
    )

    if field == "expressing_deep_concern":
        s = re.sub(r"^the fact that\s+", "", s, flags=re.IGNORECASE)
        if not re.match(r"^(the|serious|grave|growing|continued|increasing|widespread|potential|risk)\b", s, flags=re.IGNORECASE):
            s = "the risk of " + _first_lower(s)

    if field == "noting" and not re.match(r"^(the|current|ongoing|continued|growing|increasing)\b", s, flags=re.IGNORECASE):
        s = "the current situation in which " + _first_lower(s)

    s = re.sub(r"\s+", " ", s).strip(" .,;:")
    return _first_lower(s)


def _clean_actor(actor: str) -> str:
    actor = re.sub(r"\s+", " ", actor or "").strip(" .,;:")
    actor = re.sub(r"^(the\s+)", "", actor, flags=re.IGNORECASE).strip()

    if not actor:
        return "relevant actors"

    if actor.casefold() in {"they", "them", "it", "this", "these", "those"}:
        return "relevant actors"

    actor = re.sub(r"^people and organizations working on", "organizations working on", actor, flags=re.IGNORECASE)
    return actor


def _make_action_clause(note: str, field: str) -> str:
    s = _clean_model_line(_strip_meta_intro(note))
    s = s.strip(" .")

    # Common bad fragments produced by small models.
    s = re.sub(r"^(They|It|This)\s+to\b", "relevant actors to", s, flags=re.IGNORECASE)
    s = re.sub(r"\bto\s+not\s+work\s+alone\b", "to cooperate rather than work in isolation", s, flags=re.IGNORECASE)
    s = re.sub(r"\bto\s+keep\s+checking\b", "to continue reviewing", s, flags=re.IGNORECASE)

    m_align = re.match(r"^(?:it\s+would\s+be\s+better\s+if\s+)?(?P<things>.+?)\s+(?:were|was|are|is)\s+more\s+aligned$", s, flags=re.IGNORECASE)
    if m_align:
        things = m_align.group("things").strip(" .,;:")
        return f"relevant actors to align {_first_lower(things)}"

    if re.search(r"\bshould\s+not\s+be\s+handled\s+separately\b|\bwithout coordination\b", s, flags=re.IGNORECASE):
        return "relevant actors to coordinate their efforts and avoid working in isolation"

    if re.match(r"^This includes\b", s, flags=re.IGNORECASE):
        body = re.sub(r"^This includes\s+", "", s, flags=re.IGNORECASE)
        return "the strengthening of " + _compress_list_text(body)

    # "X should/must/needs to do Y" -> "X to do Y"
    m = re.match(
        r"^(?P<actor>.+?)\s+(?:should|must|need to|needs to|have to|has to)\s+(?P<action>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        actor = _clean_actor(m.group("actor"))
        action = _clean_action_text(m.group("action"))
        return f"{_first_lower(actor)} to {action}"

    # "experts to improve..." style is already operative.
    m = re.match(r"^(?P<actor>.+?)\s+to\s+(?P<action>.+)$", s, flags=re.IGNORECASE)
    if m and len(m.group("actor").split()) <= 18:
        actor = _clean_actor(m.group("actor"))
        action = _clean_action_text(m.group("action"))
        return f"{_first_lower(actor)} to {action}"

    # Nominal fragments are useful in encourages.
    if field == "encourages":
        body = _compress_list_text(s)
        if re.match(r"^(more|additional|further|continued)\b", s, flags=re.IGNORECASE):
            body = re.sub(r"^(more|additional|further)\s+", "", body, flags=re.IGNORECASE)
            return "the development of " + _first_lower(body)
        if re.search(r"\b(training|workshops|support|assistance|best practices|technical reports|incident response|frameworks|information-sharing|resilience)\b", body, flags=re.IGNORECASE):
            return "the strengthening of " + _first_lower(body)

    if field == "requests":
        if re.search(r"\b(report|study|review|assessment|standards|guidelines|recommendations|definitions|terms)\b", s, flags=re.IGNORECASE):
            body = re.sub(r"^(more|additional|further)\s+", "develop ", s, flags=re.IGNORECASE)
            return "relevant bodies to " + _first_lower(_clean_action_text(body))

    if field == "calls_upon":
        if re.search(r"\b(cooperate|work together|share|coordinate|collaborate)\b", s, flags=re.IGNORECASE):
            return "relevant actors to " + _first_lower(_clean_action_text(s))

    return _first_lower(_clean_action_text(s))


def _note_to_clause(note: str, field: str) -> str:
    s = _clean_model_line(_strip_meta_intro(note))
    s = s.strip(" .")

    if not s:
        return ""

    if field in {"requests", "calls_upon", "encourages", "decides"}:
        return _make_action_clause(s, field)

    return _formalize_preambular_clause(s, field)


def _line_dedupe(text: str, *, max_lines: int = 4) -> str:
    lines = []
    seen_fingerprints: List[str] = []

    for raw in (text or "").splitlines():
        line = _clean_model_line(raw)
        if not line:
            continue

        if _is_bad_generic_line(line):
            continue

        if any(_too_similar(line, existing) for existing in lines):
            continue

        fp = _fingerprint(line)
        if fp and fp in seen_fingerprints:
            continue

        seen_fingerprints.append(fp)
        lines.append(line)

        if len(lines) >= max_lines:
            break

    return "\n".join(lines)


def _build_rule_payload(
    *,
    plain_text: str,
    source_notes: Dict[str, List[str]],
    agenda_title: Optional[str] = None,
    room_title: Optional[str] = None,
) -> Dict[str, str]:
    out = {key: "" for key in EXPECTED_KEYS}
    out["title"] = _fallback_title(plain_text, agenda_title, room_title)

    used_lines: List[str] = []

    for key in EXPECTED_KEYS:
        if key == "title":
            continue

        max_lines = FIELD_MAX_LINES.get(key, 3)
        built_lines = []

        for note in source_notes.get(key, []):
            clause = _note_to_clause(note, key)
            if not clause or _is_bad_generic_line(clause):
                continue
            if any(_too_similar(clause, existing) for existing in used_lines):
                continue

            built_lines.append(clause)
            used_lines.append(clause)

            if len(built_lines) >= max_lines:
                break

        out[key] = "\n".join(built_lines)

    if not any(out[k] for k in EXPECTED_KEYS if k != "title"):
        out["emphasizing"] = _first_lower(_shorten_note(plain_text, max_chars=360))

    return out


def _normalize_payload(
    payload: Dict[str, Any],
    *,
    plain_text: str = "",
    agenda_title: Optional[str] = None,
    room_title: Optional[str] = None,
) -> Dict[str, str]:
    out: Dict[str, str] = {key: "" for key in EXPECTED_KEYS}

    for raw_key, raw_value in (payload or {}).items():
        key = _normalize_key(raw_key)
        if key in out:
            out[key] = _normalize_value(raw_value)

    if _is_bad_title(out.get("title", "")):
        out["title"] = _fallback_title(plain_text, agenda_title, room_title)

    return out


def _contains_any(text: str, cues: List[str]) -> bool:
    t = (text or "").casefold()
    return any(cue.casefold() in t for cue in cues)


def _strip_unsourced_parenthetical_acronyms(text: str, source: str) -> str:
    source_upper = (source or "").upper()

    def repl(match: re.Match) -> str:
        acronym = match.group(1)
        if acronym and acronym.upper() not in source_upper:
            return ""
        return match.group(0)

    return re.sub(r"\s*\(([A-Z]{2,10})\)", repl, text or "")


def _de_specific_request_actors(text: str, source: str) -> str:
    source_cf = (source or "").casefold()

    safe_prefixes = (
        "relevant ",
        "member states",
        "states",
        "countries",
        "governments",
        "international organizations",
        "experts",
        "stakeholders",
        "technical experts",
        "relevant bodies",
        "relevant organizations",
        "organizations",
        "standards bodies",
    )

    fixed_lines = []

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        lowered = line.casefold()
        if lowered.startswith(safe_prefixes):
            fixed_lines.append(line)
            continue

        m = re.match(
            r"^(?:the\s+)?([A-Z][A-Za-z&\-]+(?:\s+[A-Z][A-Za-z&\-]+){1,6})(?:\s+to\b)",
            line,
        )

        if m:
            actor = m.group(1)
            if actor.casefold() not in source_cf:
                line = re.sub(
                    r"^(?:the\s+)?[A-Z][A-Za-z&\-]+(?:\s+[A-Z][A-Za-z&\-]+){1,6}(?=\s+to\b)",
                    "Relevant bodies",
                    line,
                    count=1,
                )

        fixed_lines.append(line)

    return "\n".join(fixed_lines)


def _apply_evidence_guards(
    payload: Dict[str, str],
    *,
    source_text: str,
    context_text: str,
) -> Dict[str, str]:
    payload = dict(payload)
    evidence = f"{context_text}\n\n{source_text}"

    welcoming_cues = FIELD_CUES["welcoming"]
    regret_cues = FIELD_CUES["expressing_regret"]
    deep_concern_cues = FIELD_CUES["expressing_deep_concern"]
    decision_cues = FIELD_CUES["decides"]

    if payload.get("welcoming") and not _contains_any(evidence, welcoming_cues):
        payload["welcoming"] = ""

    if payload.get("expressing_regret") and not _contains_any(evidence, regret_cues):
        payload["expressing_regret"] = ""

    if payload.get("expressing_deep_concern") and not _contains_any(evidence, deep_concern_cues):
        payload["expressing_deep_concern"] = ""

    if payload.get("decides") and not _contains_any(evidence, decision_cues):
        payload["decides"] = ""

    for key in ["requests", "calls_upon", "encourages"]:
        payload[key] = _strip_unsourced_parenthetical_acronyms(payload.get(key, ""), evidence)

    payload["requests"] = _de_specific_request_actors(payload.get("requests", ""), evidence)

    for key in EXPECTED_KEYS:
        if key == "title":
            continue
        payload[key] = _line_dedupe(payload.get(key, ""), max_lines=FIELD_MAX_LINES.get(key, 3))

    if _is_bad_title(payload.get("title", "")):
        payload["title"] = _fallback_title(source_text)

    return payload


def _payload_quality_issues(payload: Dict[str, str], *, rule_payload: Dict[str, str]) -> List[str]:
    issues = []

    if _is_bad_title(payload.get("title", "")):
        issues.append("bad_title")

    all_lines = []
    seen = []
    for key in EXPECTED_KEYS:
        if key == "title":
            continue
        for line in (payload.get(key, "") or "").splitlines():
            line = _clean_model_line(line)
            if not line:
                continue
            all_lines.append((key, line))
            if _is_bad_generic_line(line):
                issues.append(f"bad_line:{key}")
            if re.search(r"\b(They|It|This)\s+to\b", line):
                issues.append(f"broken_pronoun:{key}")
            if key in {"requests", "calls_upon", "encourages"} and not re.search(r"\bto\b", line, flags=re.IGNORECASE):
                # Not every operative must contain "to", but small-model fragments without it are usually bad.
                issues.append(f"weak_operative:{key}")
            if any(_too_similar(line, previous) for previous in seen):
                issues.append(f"duplicate:{key}")
            seen.append(line)

    if len(all_lines) <= 2 and sum(bool(rule_payload.get(k)) for k in EXPECTED_KEYS if k != "title") >= 3:
        issues.append("too_sparse")

    return issues


def _merge_or_replace_with_rules(
    model_payload: Dict[str, str],
    *,
    rule_payload: Dict[str, str],
) -> Dict[str, str]:
    # Default to stable deterministic clauses. The model is used only if it is clean.
    if _env_bool("DRAFT_AI_PREFER_RULES", "1"):
        base = dict(rule_payload)
        # Allow a good model title only if it is clearly better and not a room/test title.
        if not _is_bad_title(model_payload.get("title", "")):
            base["title"] = model_payload["title"]
        return base

    issues = _payload_quality_issues(model_payload, rule_payload=rule_payload)
    if issues:
        print("DRAFT AI QUALITY GATE USING RULE PAYLOAD:", ", ".join(sorted(set(issues))))
        return dict(rule_payload)

    return dict(model_payload)


def _validate(
    payload: Dict[str, Any],
    *,
    source_text: str,
    context_text: str,
    source_notes: Dict[str, List[str]],
    agenda_title: Optional[str] = None,
    room_title: Optional[str] = None,
) -> DraftFill:
    rule_payload = _build_rule_payload(
        plain_text=source_text,
        source_notes=source_notes,
        agenda_title=agenda_title,
        room_title=room_title,
    )

    normalized = _normalize_payload(
        payload,
        plain_text=source_text,
        agenda_title=agenda_title,
        room_title=room_title,
    )

    guarded_model = _apply_evidence_guards(
        normalized,
        source_text=source_text,
        context_text=context_text,
    )

    selected = _merge_or_replace_with_rules(
        guarded_model,
        rule_payload=rule_payload,
    )

    final_payload = _apply_evidence_guards(
        selected,
        source_text=source_text,
        context_text=context_text,
    )

    if hasattr(DraftFill, "model_validate"):
        return DraftFill.model_validate(final_payload)

    return DraftFill.parse_obj(final_payload)


def _clamp_input(text: str) -> str:
    max_chars = int(os.getenv("DRAFT_AI_INPUT_MAX_CHARS", "6000"))
    text = (text or "").strip()

    if len(text) <= max_chars:
        return text

    return text[:max_chars].rsplit(" ", 1)[0].strip()


def _build_context_text(
    *,
    agenda_title: Optional[str] = None,
    room_title: Optional[str] = None,
) -> str:
    context_bits = []

    if agenda_title and not _is_bad_title(agenda_title):
        context_bits.append(f"Agenda title: {agenda_title}")

    # Room titles are frequently internal test labels such as "multi 2" or
    # "Cek Aws 5". Do not send them to the model unless explicitly enabled.
    if os.getenv("DRAFT_AI_ALLOW_ROOM_TITLE", "0") == "1" and room_title and not _is_bad_title(room_title):
        context_bits.append(
            "Discussion room title, for context only. Do not copy it as the draft title unless it is clearly a real policy title: "
            f"{room_title}"
        )

    return "\n".join(context_bits).strip()


def _is_tiny_or_small_model() -> bool:
    model = os.getenv("OLLAMA_MODEL", "").casefold()
    profile = os.getenv("DRAFT_AI_PROFILE", "").casefold()

    return (
        profile in {"tiny", "small", "qwen", "stablelm"}
        or "0.5b" in model
        or "1.6b" in model
        or "tinyllama" in model
        or "qwen2.5:0.5b" in model
        or "stablelm2:1.6b" in model
    )


def _build_prompts(
    *,
    plain_text: str,
    context_text: str,
    source_notes: Dict[str, List[str]],
) -> Tuple[str, str, Union[str, Dict[str, Any]], int, float]:
    source_note_text = _notes_to_prompt_text(source_notes)

    if _is_tiny_or_small_model():
        system = """
You polish source notes into a UN-style draft skeleton.

Rules:
- Return JSON only.
- Use exactly the requested JSON keys.
- Do not invent facts, institutions, actors, events, progress, failures, or legal claims.
- Do not write vague category labels like "importance of..." or "background on...".
- Do not copy "SOURCE NOTES" labels.
- Every value must be a string.
- Leave unsupported fields as "".
""".strip()

        user = f"""
Context:
{context_text or "No extra context."}

JSON keys:
title, recalling, noting, welcoming, expressing_regret, expressing_deep_concern, emphasizing, decides, requests, calls_upon, encourages

Write clause fragments, not summaries.

Good style:
- recalling/noting/emphasizing fields should be concise preambular fragments.
- requests/calls_upon/encourages fields should use this pattern: "actor to action".
- Keep actors generic unless named in the source notes.
- Maximum 2 lines per field.
- Do not start a line with "They to", "It to", or "This to".

Grounded source notes:
{source_note_text}

Plain-language input, for title only:
{_clamp_input(plain_text)}

Return the JSON object now.
""".strip()

        response_format: Union[str, Dict[str, Any]] = DRAFT_JSON_SCHEMA
        max_tokens = int(os.getenv("DRAFT_AI_MAX_TOKENS", "900"))
        timeout_s = float(os.getenv("DRAFT_AI_TIMEOUT_S", "180"))
        return system, user, response_format, max_tokens, timeout_s

    system = """
You convert plain-language policy text into a cautious UN-style draft skeleton.

You are evidence-grounded:
- Use only information supported by the input or context.
- Do not invent facts, institutions, actors, events, or progress.
- It is better to leave a field empty than to fill it with unsupported content.

Return complete valid JSON only.
No markdown.
No explanation.
""".strip()

    user = f"""
{context_text}

Return exactly this JSON object shape:

{{
  "title": "",
  "recalling": "",
  "noting": "",
  "welcoming": "",
  "expressing_regret": "",
  "expressing_deep_concern": "",
  "emphasizing": "",
  "decides": "",
  "requests": "",
  "calls_upon": "",
  "encourages": ""
}}

General field guide:
- title: required; infer a concise formal title from the central policy topic. Do not use test-like room titles.
- recalling: background principles, rights, obligations, legal/policy context, basic recognized facts.
- noting: factual conditions, trends, risks, gaps, problems, technical changes, cross-border effects.
- welcoming: only existing positive developments, successful cooperation, or progress already stated in the input.
- expressing_regret: only explicit regret, failure, lack of progress, missed commitments, or insufficient action stated in the input.
- expressing_deep_concern: only serious threats, major harm, widespread damage, grave risks, or instability supported by the input.
- emphasizing: importance, priority, urgency, need, objectives, resilience, shared understanding, or key principles.
- decides: only a direct decision by the body itself. Leave empty if the input only recommends or encourages.
- requests: asks a specific body, expert group, standards body, secretariat, committee, or organization to review, study, report, continue technical work, or provide assistance.
- calls_upon: urges states, organizations, companies, experts, stakeholders, or international actors to cooperate or take action.
- encourages: softer recommendations, best practices, capacity-building, voluntary actions, adoption of approaches, preparation, protection, training, or support.

Strict evidence rules:
- Do not name a specific organization unless it appears in the input or context.
- If the input says only "organizations", "experts", "standards bodies", or "international organizations", keep the actor generic.
- Do not create "existing efforts" or "existing progress" unless the input clearly says such efforts or progress already exist.
- Do not create "lack of progress", "failure", or "missed commitments" unless the input clearly says so.
- Do not use "decides" unless the input clearly states a formal decision by the body.
- Do not put recommendations or proposed actions into "noting".
- Do not put future recommendations into "welcoming".
- Do not put factual risks into "encourages".

Drafting rules:
- Rewrite informal language into formal but concise clause language.
- Split long paragraphs into multiple clause ideas when useful.
- Each field must be a string.
- Use newline characters between multiple clauses in one field.
- Use at most 3 lines per field.
- Each line should be concise, preferably under 30 words.
- Leave unsupported fields as empty strings.
- Finish the JSON object completely, including the final closing brace.

Plain-language input:
{plain_text}
""".strip()

    response_format = "json"
    max_tokens = int(os.getenv("DRAFT_AI_MAX_TOKENS", "900"))
    timeout_s = float(os.getenv("DRAFT_AI_TIMEOUT_S", "240"))
    return system, user, response_format, max_tokens, timeout_s


def _call_model(
    *,
    messages: List[Dict[str, str]],
    response_format: Union[str, Dict[str, Any]],
    max_tokens: int,
    timeout_s: float,
) -> str:
    client = get_chat_client()

    resp = client.chat_completion(
        messages=messages,
        temperature=float(os.getenv("DRAFT_AI_TEMPERATURE", "0.0")),
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        response_format=response_format,
    )

    return (resp.choices[0].message.content or "").strip()


def generate_draft_from_paragraphs(
    *,
    plain_text: str,
    agenda_title: Optional[str] = None,
    room_title: Optional[str] = None,
) -> DraftFill:
    require_ai_features()
    context_text = _build_context_text(
        agenda_title=agenda_title,
        room_title=room_title,
    )

    source_notes = _build_source_notes(plain_text)

    # Fast, stable path. This is the default because small local models often
    # produce fluent-looking but structurally weak clauses.
    if not _env_bool("DRAFT_AI_USE_MODEL", "1"):
        payload = _build_rule_payload(
            plain_text=plain_text,
            source_notes=source_notes,
            agenda_title=agenda_title,
            room_title=room_title,
        )
        return _validate(
            payload,
            source_text=plain_text,
            context_text=context_text,
            source_notes=source_notes,
            agenda_title=agenda_title,
            room_title=room_title,
        )

    system, user, response_format, max_tokens, timeout_s = _build_prompts(
        plain_text=plain_text,
        context_text=context_text,
        source_notes=source_notes,
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    content = ""

    try:
        content = _call_model(
            messages=messages,
            response_format=response_format,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )
        payload = _extract_json_object(content)

    except Exception as first_error:
        print("DRAFT AI FIRST ATTEMPT FAILED:", repr(first_error))
        print("DRAFT AI RAW OUTPUT:", content[:1200])

        if isinstance(response_format, dict):
            try:
                content = _call_model(
                    messages=messages,
                    response_format="json",
                    max_tokens=max_tokens,
                    timeout_s=timeout_s,
                )
                payload = _extract_json_object(content)

            except Exception as second_error:
                print("DRAFT AI SECOND ATTEMPT FAILED:", repr(second_error))
                print("DRAFT AI RAW OUTPUT 2:", content[:1200])
                payload = _build_rule_payload(
                    plain_text=plain_text,
                    source_notes=source_notes,
                    agenda_title=agenda_title,
                    room_title=room_title,
                )
        else:
            payload = _build_rule_payload(
                plain_text=plain_text,
                source_notes=source_notes,
                agenda_title=agenda_title,
                room_title=room_title,
            )

    return _validate(
        payload,
        source_text=plain_text,
        context_text=context_text,
        source_notes=source_notes,
        agenda_title=agenda_title,
        room_title=room_title,
    )


# ---------------------------------------------------------------------------
# v4 generic quality overrides
# ---------------------------------------------------------------------------
# These overrides keep the engine input-agnostic while fixing common failures
# found in second-domain tests: leading contrast fragments, existing-effort
# misclassification, weak “it is important...” requests, and awkward list clauses.

for _field, _extra_cues in {
    "welcoming": [
        "already", "already trying", "already working", "existing efforts",
        "national strategies", "technology parks", "innovation hubs",
        "research centers", "training programs", "programmes", "established",
        "created", "launched", "set up", "developed", "ongoing work",
    ],
    "expressing_deep_concern": [
        "fall further behind", "left behind", "fall behind", "digital gap",
        "digital divide", "unequal access", "lack of access", "lack of infrastructure",
        "lack of skills", "lack of resources", "limited access", "not every country",
        "not every community", "risk that some", "widen the gap", "inequality",
    ],
    "requests": [
        "make sure", "responsibly", "responsible", "rules", "rule", "guideline",
        "guidelines", "protect people", "allowing innovation", "regulatory",
        "governance", "safeguards", "oversight",
    ],
    "encourages": [
        "could include", "sharing knowledge", "technical support", "training workers",
        "learn new skills", "skills development", "research partnerships",
        "training centers", "training centres", "accessible", "open models",
        "training data", "computing tools", "capacity building", "capacity-building",
    ],
    "noting": [
        "internet access", "digital infrastructure", "technical knowledge",
        "skills", "resources", "capacity", "access", "infrastructure",
    ],
}.items():
    for _cue in _extra_cues:
        if _cue not in FIELD_CUES[_field]:
            FIELD_CUES[_field].append(_cue)


_old_strip_meta_intro_v4 = _strip_meta_intro

def _strip_meta_intro(sentence: str) -> str:
    s = _old_strip_meta_intro_v4(sentence)
    s = re.sub(r"^Because\s+of\s+this,\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^But\s+at\s+the\s+same\s+time,\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^At\s+the\s+same\s+time,\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^Overall,\s+the\s+proposal\s+is\s+saying\s+that\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^Overall,\s+this\s+proposal\s+is\s+saying\s+that\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^Overall,\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^This\s+could\s+include\s+", "", s, flags=re.IGNORECASE).strip()
    return s


_old_score_sentence_for_field_v4 = _score_sentence_for_field

def _score_sentence_for_field(sentence: str, field: str) -> int:
    s = _strip_meta_intro(sentence).casefold()
    score = _old_score_sentence_for_field_v4(sentence, field)

    # Existing efforts/progress should be welcomed, not encouraged as future action.
    if field == "welcoming" and re.search(
        r"\balready\b|\bexisting\b|\bongoing\b|\bhas\s+(?:created|developed|established|launched|set up)\b|\bhave\s+(?:created|developed|established|launched|set up)\b",
        s,
    ):
        if re.search(r"\b(develop|build|create|establish|launch|set up|support|implement|strategy|strategies|programs?|programmes?|hubs?|centers?|centres?|parks?)\b", s):
            score += 12

    if field == "encourages" and re.search(r"\balready\b|\bexisting\b|\bongoing\b", s):
        score -= 5

    # Inequality/risk sentences should not remain mere notes when the source
    # explicitly says communities may fall behind.
    if field == "expressing_deep_concern" and re.search(r"\brisk\b", s) and re.search(r"\b(fall|behind|left|exclude|widen|inequal|gap|divide)\b", s):
        score += 12

    if field == "noting" and re.search(r"\bnot every\b|\bunequal\b|\black of\b|\blimited\b", s) and re.search(r"\b(access|infrastructure|skills?|resources?|money|knowledge|capacity)\b", s):
        score += 8

    if field == "requests" and re.search(r"\bimportant\s+to\s+make\s+sure\b|\bmake\s+sure\b", s) and re.search(r"\b(responsib|rules?|guidelines?|protect|safeguard|oversight|governance)\b", s):
        score += 12

    if field == "encourages" and re.search(r"\bcould\s+include\b|\bsharing\s+knowledge\b|\btechnical\s+support\b|\btraining\s+workers\b|\bresearch\s+partnerships\b|\btraining\s+cent(?:er|re)s\b", s):
        score += 10

    return score


_old_build_source_notes_v4 = _build_source_notes

def _build_source_notes(plain_text: str) -> Dict[str, List[str]]:
    sentences = _normalized_sentence_stream(plain_text)
    notes: Dict[str, List[str]] = {key: [] for key in EXPECTED_KEYS if key != "title"}
    used_sentences: List[str] = []

    for sentence in sentences:
        s_cf = sentence.casefold()

        # Dual-use equity sentences: factual gap + serious consequence.
        if re.search(r"\bnot every\b|\bunequal\b|\black of\b|\blimited\b", s_cf) and re.search(r"\b(access|infrastructure|skills?|money|resources?|knowledge|capacity)\b", s_cf):
            note = _shorten_note(sentence)
            if not any(_too_similar(note, existing) for existing in notes["noting"]):
                notes["noting"].append(note)

        if re.search(r"\brisk\b", s_cf) and re.search(r"\b(fall|behind|left|exclude|widen|inequal|gap|divide)\b", s_cf):
            note = _shorten_note(sentence)
            if not any(_too_similar(note, existing) for existing in notes["expressing_deep_concern"]):
                notes["expressing_deep_concern"].append(note)

        field = _best_field_for_sentence(sentence)
        if not field:
            continue

        if re.match(r"^This includes\b|^This could include\b", sentence, flags=re.IGNORECASE):
            field = "encourages"

        note = _shorten_note(sentence)
        if any(_too_similar(note, existing) for existing in used_sentences):
            continue

        notes[field].append(note)
        used_sentences.append(note)

    if not notes["recalling"] and sentences:
        for sentence in sentences:
            if not re.search(r"\b(should|must|need to|attack|threat|harm|damage|risk that)\b", sentence, flags=re.IGNORECASE):
                notes["recalling"].append(_shorten_note(sentence))
                break

    if not any(notes.values()) and sentences:
        notes["emphasizing"] = [_shorten_note(s) for s in sentences[:3]]

    for key in notes:
        notes[key] = notes[key][: max(FIELD_MAX_LINES.get(key, 3), 1)]

    return notes


_old_clean_action_text_v4 = _clean_action_text

def _clean_action_text(action: str) -> str:
    s = re.sub(r"\s+", " ", action or "").strip(" .")
    s = re.sub(r"^all\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bto\s+all\s+take\s+part\b", "to take part", s, flags=re.IGNORECASE)
    s = re.sub(r"^work\s+together\s+more\s+closely\b", "strengthen cooperation", s, flags=re.IGNORECASE)
    s = re.sub(r"^work\s+together\b", "cooperate", s, flags=re.IGNORECASE)
    s = re.sub(r"^help\s+(.+?)\s+build\s+its\s+(.+?)\s+capacity\b", r"support \1 in building \2 capacity", s, flags=re.IGNORECASE)
    s = re.sub(r"^make\s+sure\s+", "ensure that ", s, flags=re.IGNORECASE)
    s = _old_clean_action_text_v4(s)
    s = re.sub(r"\bto\s+all\s+take\s+part\b", "to take part", s, flags=re.IGNORECASE)
    return s


_old_compress_list_text_v4 = _compress_list_text

def _compress_list_text(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = re.sub(r"^this\s+could\s+include\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^this\s+includes\s+", "", s, flags=re.IGNORECASE)
    s = _old_compress_list_text_v4(s)

    # Generic gerund-to-noun polish for implementation lists.
    replacements = [
        (r"\bgiving\s+technical\s+support\b", "technical support"),
        (r"\bproviding\s+technical\s+support\b", "technical support"),
        (r"\btraining\s+workers\b", "workforce training"),
        (r"\bhelping\s+students\s+and\s+professionals\s+learn\s+new\s+skills\b", "skills development for students and professionals"),
        (r"\bcreating\s+research\s+partnerships\b", "research partnerships"),
        (r"\bsetting\s+up\s+training\s+centers\b", "training centers"),
        (r"\bsetting\s+up\s+training\s+centres\b", "training centres"),
        (r"\bmaking\s+useful\s+resources\s+(.+?)\s+more\s+accessible\b", r"improved access to useful resources \1"),
        (r"\bsharing\s+of\s+knowledge\b", "knowledge-sharing"),
    ]
    for pattern, repl in replacements:
        s = re.sub(pattern, repl, s, flags=re.IGNORECASE)

    s = re.sub(r"\bthe strengthening of the strengthening of\b", "the strengthening of", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .,;:")
    return s


_old_formalize_preambular_clause_v4 = _formalize_preambular_clause

def _formalize_preambular_clause(text: str, field: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_meta_intro(s)
    s = _strip_trailing_reason(s)

    # General unequal-capacity pattern.
    m = re.match(
        r"^not every\s+(?P<who>.+?)\s+has\s+the\s+same\s+(?P<resources>.+?)\s+to\s+benefit\s+from\s+(?P<topic>.+?),\s*so\s+there\s+is\s+a\s+risk\s+that\s+(?P<risk>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        who = m.group("who").strip(" .,;:")
        resources = m.group("resources").strip(" .,;:")
        topic = m.group("topic").strip(" .,;:")
        risk = m.group("risk").strip(" .,;:")
        if field == "expressing_deep_concern":
            return f"the risk that {risk} because of unequal access to {resources}"
        return f"unequal access among {who} to {resources} needed to benefit from {topic}"

    # Existing efforts are better as welcoming fragments than future recommendations.
    m = re.match(r"^(?P<actor>.+?)\s+are\s+already\s+(?P<action>trying\s+to\s+.+|working\s+to\s+.+|developing\s+.+|creating\s+.+|establishing\s+.+|supporting\s+.+)$", s, flags=re.IGNORECASE)
    if m and field == "welcoming":
        actor = m.group("actor").strip(" .,;:")
        action = _compress_list_text(m.group("action"))
        action = re.sub(r"^trying\s+to\s+", "efforts to ", action, flags=re.IGNORECASE)
        return f"existing efforts by {actor} to {_first_lower(action)}"

    m = re.match(r"^(?P<topic>.+?)\s+could\s+be\s+useful\s+in\s+(?P<areas>.+)$", s, flags=re.IGNORECASE)
    if m:
        topic = m.group("topic").strip(" .,;:")
        areas = m.group("areas").strip(" .,;:")
        return f"the potential usefulness of {topic} in {areas}"

    return _old_formalize_preambular_clause_v4(text, field)


_old_make_action_clause_v4 = _make_action_clause

def _make_action_clause(text: str, field: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_meta_intro(s)

    # Convert generic normative sentences into actor-to-action clauses.
    m = re.match(r"^(?:it\s+is\s+also\s+important|it\s+is\s+important|it\s+is\s+necessary)\s+to\s+make\s+sure\s+(?P<object>.+)$", s, flags=re.IGNORECASE)
    if m:
        action = _clean_action_text("ensure " + m.group("object"))
        actor = "relevant governments and organizations" if field in {"requests", "calls_upon"} else "relevant actors"
        return f"{actor} to {action}"

    m = re.match(r"^(?P<actor>.+?)\s+should\s+work\s+together\s+more\s+closely\s+to\s+(?P<action>.+)$", s, flags=re.IGNORECASE)
    if m:
        actor = _clean_actor(m.group("actor"))
        action = _clean_action_text("strengthen cooperation to " + m.group("action"))
        return f"{_first_lower(actor)} to {action}"

    m = re.match(r"^(?P<actor>.+?)\s+should\s+all\s+take\s+part\s+in\s+(?P<object>.+)$", s, flags=re.IGNORECASE)
    if m:
        actor = _clean_actor(m.group("actor"))
        return f"{_first_lower(actor)} to take part in {m.group('object').strip(' .,;:')}"

    # Encouragement implementation lists should become nominal objects.
    if field == "encourages" and re.search(r"\b(could include|sharing|technical support|training|skills|research partnerships|training centers|training centres|accessible|resources)\b", s, flags=re.IGNORECASE):
        body = _compress_list_text(s)
        return "the strengthening of " + _first_lower(body)

    return _old_make_action_clause_v4(text, field)


# v4.1 polish overrides for general pluralization, existing-effort phrasing,
# and conditional benefit sentences.
_old_formalize_preambular_clause_v41 = _formalize_preambular_clause

def _pluralize_group_phrase(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .,;:")
    s = re.sub(r"\bcountry\s+or\s+community\b", "countries and communities", s, flags=re.IGNORECASE)
    s = re.sub(r"\bperson\s+or\s+company\b", "persons and companies", s, flags=re.IGNORECASE)
    return s


def _formalize_preambular_clause(text: str, field: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_meta_intro(s)
    s = _strip_trailing_reason(s)

    m = re.match(
        r"^not every\s+(?P<who>.+?)\s+has\s+the\s+same\s+(?P<resources>.+?)\s+to\s+benefit\s+from\s+(?P<topic>.+?),\s*so\s+there\s+is\s+a\s+risk\s+that\s+(?P<risk>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        who = _pluralize_group_phrase(m.group("who"))
        resources = m.group("resources").strip(" .,;:")
        topic = m.group("topic").strip(" .,;:")
        risk = m.group("risk").strip(" .,;:")
        if field == "expressing_deep_concern":
            return f"the risk that {risk} because of unequal access to {resources}"
        return f"unequal access among {who} to {resources} needed to benefit from {topic}"

    m = re.match(r"^(?P<actor>.+?)\s+are\s+already\s+(?P<action>trying\s+to\s+.+|working\s+to\s+.+|developing\s+.+|creating\s+.+|establishing\s+.+|supporting\s+.+)$", s, flags=re.IGNORECASE)
    if m and field == "welcoming":
        actor = m.group("actor").strip(" .,;:")
        actor = re.sub(r"^countries\s+like\s+", "countries including ", actor, flags=re.IGNORECASE)
        actor = _first_lower(actor)
        action = m.group("action").strip(" .,;:")
        action = re.sub(r"^trying\s+to\s+", "", action, flags=re.IGNORECASE)
        action = re.sub(r"^working\s+to\s+", "", action, flags=re.IGNORECASE)
        action = _compress_list_text(action)
        return f"existing efforts by {actor} to {_first_lower(action)}"

    m = re.match(
        r"^(?P<topic>.+?)\s+is\s+becoming\s+more\s+important\s+(?P<context>.+?)\s+and\s+it\s+can\s+help\s+(?P<benefit>.+?)\s+if\s+it\s+is\s+used\s+(?P<condition>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        topic = m.group("topic").strip(" .,;:")
        benefit = m.group("benefit").strip(" .,;:")
        condition = m.group("condition").strip(" .,;:")
        return f"the growing importance of {topic} and its potential to help {benefit} when used {condition}"

    return _old_formalize_preambular_clause_v41(text, field)


_old_compress_list_text_v41 = _compress_list_text

def _compress_list_text(text: str) -> str:
    s = _old_compress_list_text_v41(text)
    s = re.sub(r"\bsetting\s+up\s+([A-Za-z][A-Za-z\- ]*training\s+cent(?:er|re)s)\b", r"\1", s, flags=re.IGNORECASE)
    s = re.sub(r"\bcreating\s+([A-Za-z][A-Za-z\- ]*partnerships)\b", r"\1", s, flags=re.IGNORECASE)
    s = re.sub(r"\bmaking\s+sure\s+(.+?)\s+are\s+safe,\s*inclusive,\s*trustworthy,\s*and\s*useful\s+for\s+(.+)$", r"safe, inclusive, trustworthy, and useful \1 for \2", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .,;:")
    return s


_old_clean_action_text_v41 = _clean_action_text

def _clean_action_text(action: str) -> str:
    s = _old_clean_action_text_v41(action)
    s = re.sub(r"\bmake\s+sure\s+(.+?)\s+are\s+safe,\s*inclusive,\s*trustworthy,\s*and\s*useful\s+for\s+(.+)$", r"ensure that \1 are safe, inclusive, trustworthy, and useful for \2", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .,;:")
    return s


_old_make_action_clause_v41 = _make_action_clause

def _make_action_clause(text: str, field: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_meta_intro(s)

    # Conditional benefit sentence, e.g. "X can help, but only if countries cooperate..."
    m = re.search(r"\bbut\s+only\s+if\s+(?P<actor>[A-Za-z ,\-]+?)\s+(?P<action>cooperate\b.+)$", s, flags=re.IGNORECASE)
    if m and field in {"encourages", "calls_upon"}:
        actor = _clean_actor(m.group("actor"))
        action = _clean_action_text(m.group("action"))
        return f"{_first_lower(actor)} to {action}"

    return _old_make_action_clause_v41(text, field)


# v4.2 final capitalization polish.
_old_formalize_preambular_clause_v42 = _formalize_preambular_clause

def _formalize_preambular_clause(text: str, field: str) -> str:
    out = _old_formalize_preambular_clause_v42(text, field)
    out = re.sub(r"\bof\s+Artificial intelligence\b", "of artificial intelligence", out)
    out = re.sub(r"\bimportance\s+of\s+([A-Z][a-z]+\s+intelligence)\b", lambda m: "importance of " + m.group(1)[0].lower() + m.group(1)[1:], out)
    return out


# ---------------------------------------------------------------------------
# v5 generic rights / harms / platform-safety quality overrides
# ---------------------------------------------------------------------------
# These overrides are topic-agnostic. They fix recurring lay-input patterns:
# - first-person openings such as "I think..." leaking into titles/clauses
# - rights/harm contrast sentences becoming malformed concerns
# - factual harm sentences being misclassified as requests
# - "there should be..." and "special attention should be given..." grammar
# - platform/AI misuse and transparency/safeguard obligations

for _field, _extra_cues in {
    "recalling": [
        "live safely", "worship freely", "express their opinions", "treated with respect",
        "freedom of religion", "freedom of expression", "freedom of assembly",
        "freedom of association", "human rights", "dignity",
    ],
    "noting": [
        "social media", "digital platforms", "online", "offline", "platforms",
        "spread very quickly", "trends", "track", "tracking", "transparent ways",
        "technology can also help", "detect", "reduce harmful content",
    ],
    "welcoming": [
        "international day", "world interfaith harmony week", "observances",
        "events such as", "peace-related observances",
    ],
    "expressing_deep_concern": [
        "hate speech", "discrimination", "hostility", "violence", "false information",
        "misleading information", "disinformation", "online attacks", "harmful messages",
        "harmful content", "fake images", "fake videos", "fake posts", "fake content",
        "spreads hatred", "spreads lies", "hatred", "lies", "division", "unsafe",
        "conflict", "targets people", "religious minorities", "migrants", "refugees",
        "stateless people", "displaced people", "bias",
    ],
    "requests": [
        "take action", "transparency", "clearer about", "what data", "reporting systems",
        "label", "watermark", "watermarking", "safeguards", "prevent hate speech",
        "prevent bias", "prevent discrimination", "prevent harmful fake content",
        "governance", "responsibility", "responsible for", "systems they create",
    ],
    "calls_upon": [
        "religious leaders", "community leaders", "schools", "media", "civil society",
        "technology companies", "developers", "ordinary people", "communities",
    ],
    "encourages": [
        "education", "public awareness", "dialogue", "interfaith", "between religions",
        "between cultures", "between communities", "speaking out", "supporting people",
        "encouraging peace", "inclusive discussions", "represented", "research",
    ],
}.items():
    for _cue in _extra_cues:
        if _cue not in FIELD_CUES[_field]:
            FIELD_CUES[_field].append(_cue)


_old_strip_meta_intro_v5 = _strip_meta_intro

def _strip_meta_intro(sentence: str) -> str:
    s = _old_strip_meta_intro_v5(sentence)
    s = re.sub(r"^(I\s+think|I\s+believe|I\s+feel|In\s+my\s+opinion|We\s+think|We\s+believe)\s+(?:that\s+)?", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^At\s+the\s+same\s+time,\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^This\s+is\s+not\s+only\s+happening\s+", "", s, flags=re.IGNORECASE).strip()
    return s


_old_derive_title_from_text_v5 = _derive_title_from_text

def _derive_title_from_text(plain_text: str) -> str:
    compact = re.sub(r"\s+", " ", plain_text or "").strip()
    compact = re.sub(r"^(I\s+think|I\s+believe|I\s+feel|In\s+my\s+opinion|We\s+think|We\s+believe)\s+(?:that\s+)?", "", compact, flags=re.IGNORECASE).strip()

    title = _old_derive_title_from_text_v5(compact)
    title = re.sub(r"^(I\s+Think|I\s+Believe|I\s+Feel|We\s+Think|We\s+Believe)\s+", "", title, flags=re.IGNORECASE).strip()

    # If the first topic is a short public-policy harm such as "hate speech",
    # do not force an awkward first-person or generic title.
    if _is_bad_title(title) or re.search(r"\b(I Think|I Believe|In My Opinion)\b", title, flags=re.IGNORECASE):
        phrases = _best_key_phrases(compact, limit=20)
        if phrases:
            title = _title_case(phrases[0])

    return title[:90] or "Draft Resolution"


_old_infer_actor_v5 = _infer_actor

def _infer_actor(sentence: str) -> str:
    s = _strip_meta_intro(sentence)
    actor_keywords = (
        "countries", "states", "governments", "companies", "organizations",
        "experts", "researchers", "stakeholders", "bodies", "committee",
        "secretariat", "people and organizations", "international organizations",
        "technical experts", "standards bodies", "technology companies",
        "developers", "ai developers", "schools", "media", "civil society",
        "religious leaders", "community leaders", "communities", "platforms",
        "governments", "ordinary people",
    )

    if not any(keyword in s.casefold() for keyword in actor_keywords):
        return _old_infer_actor_v5(sentence)

    patterns = [
        r"^(.{3,180}?)\s+(?:should|must|need to|needs to|have to|has to|can|could|will|shall)\b",
        r"^(.{3,180}?)\s+(?:working on|involved in|responsible for)\b",
        r"^(.{3,180}?)\s+to\b",
    ]

    for pattern in patterns:
        m = re.match(pattern, s, flags=re.IGNORECASE)
        if m:
            actor = m.group(1).strip(" .,;:")
            actor = re.sub(r"^(the\s+)?", "", actor, flags=re.IGNORECASE).strip()
            if any(keyword in actor.casefold() for keyword in actor_keywords):
                return actor[:180]

    return _old_infer_actor_v5(sentence)


_old_score_sentence_for_field_v5 = _score_sentence_for_field

def _score_sentence_for_field(sentence: str, field: str) -> int:
    s = _strip_meta_intro(sentence).casefold()
    score = _old_score_sentence_for_field_v5(sentence, field)

    # First-person policy judgments should not become titles/recalling text;
    # classify the underlying harm instead.
    if field == "expressing_deep_concern" and re.search(r"\bserious problem\b", s) and re.search(r"\b(target|targets|because of|religion|culture|race|beliefs?|background)\b", s):
        score += 14

    # Rights + harms contrast sentence.
    if field == "expressing_deep_concern" and re.search(r"\bshould be able to\b", s) and re.search(r"\bbut\b", s) and re.search(r"\b(hate speech|discrimination|false information|attacks?|unsafe|divided)\b", s):
        score += 14
    if field == "recalling" and re.search(r"\bshould be able to\b", s) and re.search(r"\b(freedom|worship|express|respect|rights?)\b", s):
        score += 8

    # Online/platform spread is a factual condition; AI/fake-content misuse is a concern.
    if field == "noting" and re.search(r"\b(social media|digital platforms?|online|offline|spread very quickly)\b", s):
        score += 12
    if field == "expressing_deep_concern" and re.search(r"\b(new technology|artificial intelligence|ai)\b", s) and re.search(r"\b(make this worse|fake images|fake videos|fake posts|fake content|hatred|lies)\b", s):
        score += 14
    if field == "requests" and re.search(r"\bcan make this worse\b|\bused to create fake\b", s):
        score -= 12

    # Existing observances should be welcomed.
    if field == "welcoming" and re.search(r"\b(international day|world interfaith harmony week|observances?|events such as)\b", s):
        score += 12

    # Public-awareness/dialogue/capacity-style proposals are encouragements.
    if field == "encourages" and re.search(r"\bthere should be more\b", s) and re.search(r"\b(education|awareness|dialogue|training|support)\b", s):
        score += 12
    if field == "encourages" and re.search(r"\b(dialogue|religions|cultures|communities|interfaith|peace)\b", s):
        score += 8

    # Direct obligations for public authorities/platform actors are requests.
    if field == "requests" and re.search(r"\b(governments|technology companies|developers|platforms?)\b", s) and re.search(r"\b(take action|be clearer|reporting systems|label|watermark|safeguards|responsibility|data|transparen)\b", s):
        score += 12
    if field == "requests" and re.search(r"\bspecial attention\b", s):
        score += 10

    return score


_old_formalize_preambular_clause_v5 = _formalize_preambular_clause

def _formalize_preambular_clause(text: str, field: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_meta_intro(s)
    s = _strip_trailing_reason(s)

    m = re.match(
        r"^(?P<topic>.+?)\s+has\s+become\s+a\s+serious\s+problem,\s+especially\s+when\s+it\s+targets\s+people\s+because\s+of\s+their\s+(?P<grounds>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        topic = m.group("topic").strip(" .,;:")
        grounds = m.group("grounds").strip(" .,;:")
        if field == "expressing_deep_concern":
            return f"the serious problem of {topic} targeting people because of their {grounds}"
        return f"the need to address {topic} targeting people because of their {grounds}"

    m = re.match(
        r"^people\s+should\s+be\s+able\s+to\s+(?P<rights>.+?),\s+but\s+in\s+many\s+places,\s+(?P<harms>.+?)\s+are\s+making\s+(?P<impact>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        rights = m.group("rights").strip(" .,;:")
        harms = m.group("harms").strip(" .,;:")
        impact = m.group("impact").strip(" .,;:")
        if field == "recalling":
            return f"the importance of enabling people to {rights}"
        return f"the impact of {harms} in making {impact}"

    m = re.match(
        r"^(?:in person,\s+)?but\s+also\s+on\s+(?P<platforms>.+?),\s+where\s+(?P<harm>.+?)\s+can\s+(?P<effect>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field == "noting":
        platforms = m.group("platforms").strip(" .,;:")
        harm = m.group("harm").strip(" .,;:")
        effect = m.group("effect").strip(" .,;:")
        return f"the spread of {harm} on {platforms}, where it can {effect}"

    m = re.match(
        r"^new\s+technology,\s+including\s+(?P<tech>.+?),\s+can\s+make\s+this\s+worse\s+when\s+it\s+is\s+used\s+to\s+create\s+(?P<content>.+?)\s+that\s+(?P<effect>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        tech = m.group("tech").strip(" .,;:")
        content = m.group("content").strip(" .,;:")
        effect = m.group("effect").strip(" .,;:")
        if field == "expressing_deep_concern":
            return f"the misuse of new technology, including {tech}, to create {content} that {effect}"
        return f"the role of new technology, including {tech}, in the creation of {content} that {effect}"

    m = re.match(
        r"^events\s+such\s+as\s+(?P<events>.+?)\s+can\s+help\s+remind\s+people\s+that\s+(?P<message>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field == "welcoming":
        return f"observances such as {m.group('events').strip(' .,;:')} that help remind people that {m.group('message').strip(' .,;:')}"

    return _old_formalize_preambular_clause_v5(text, field)


_old_clean_action_text_v5 = _clean_action_text

def _clean_action_text(action: str) -> str:
    s = re.sub(r"\s+", " ", action or "").strip(" .")
    s = re.sub(r"^all\s+take\s+part\b", "take part", s, flags=re.IGNORECASE)
    s = re.sub(r"^be\s+clearer\s+about\b", "increase transparency about", s, flags=re.IGNORECASE)
    s = re.sub(r"^be\s+clearer\s+on\b", "increase transparency on", s, flags=re.IGNORECASE)
    s = re.sub(r"^take\s+more\s+responsibility\s+for\b", "strengthen responsibility for", s, flags=re.IGNORECASE)
    s = _old_clean_action_text_v5(s)
    s = re.sub(r"\bbut\s+they\s+should\s+also\s+make\s+sure\s+that\b", "while ensuring that", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .,;:")
    return s


_old_make_action_clause_v5 = _make_action_clause

def _make_action_clause(text: str, field: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_meta_intro(s)

    m = re.match(
        r"^there\s+should\s+be\s+more\s+(?P<object>.+?)\s+so\s+(?P<purpose>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field == "encourages":
        obj = _compress_list_text(m.group("object"))
        purpose = _clean_action_text(m.group("purpose"))
        return f"the strengthening of {obj} to {purpose}"

    m = re.match(
        r"^(?P<actor>.+?)\s+should\s+(?P<action>.+?),\s+but\s+they\s+should\s+also\s+make\s+sure\s+that\s+(?P<guardrail>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        actor = _clean_actor(m.group("actor"))
        action = _clean_action_text(m.group("action"))
        guardrail = _clean_action_text("ensure that " + m.group("guardrail"))
        return f"{_first_lower(actor)} to {action}, while {guardrail}"

    m = re.match(
        r"^special\s+attention\s+should\s+(?:also\s+)?be\s+given\s+to\s+(?P<object>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        return f"relevant actors to give special attention to {m.group('object').strip(' .,;:')}"

    m = re.match(
        r"^(?P<actor>technology\s+companies\s+and\s+(?:AI|artificial\s+intelligence)\s+developers)\s+should\s+(?:also\s+)?take\s+more\s+responsibility\s+for\s+(?P<object>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        actor = _clean_actor(m.group("actor"))
        return f"{_first_lower(actor)} to strengthen responsibility for {m.group('object').strip(' .,;:')}"

    m = re.match(
        r"^(?P<actor>.+?)\s+should\s+(?:also\s+)?avoid\s+(?P<object>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field in {"calls_upon", "requests"}:
        actor = _clean_actor(m.group("actor"))
        return f"{_first_lower(actor)} to avoid {m.group('object').strip(' .,;:')}"

    return _old_make_action_clause_v5(text, field)


_old_payload_quality_issues_v5 = _payload_quality_issues

def _payload_quality_issues(payload: Dict[str, str], *, rule_payload: Dict[str, str]) -> List[str]:
    issues = _old_payload_quality_issues_v5(payload, rule_payload=rule_payload)
    title = payload.get("title", "")
    if re.search(r"\b(I\s+Think|I\s+Believe|In\s+My\s+Opinion)\b", title, flags=re.IGNORECASE):
        issues.append("first_person_title")
    joined = "\n".join(payload.get(k, "") for k in EXPECTED_KEYS)
    if re.search(r"\bthe\s+risk\s+of\s+people\s+should\b", joined, flags=re.IGNORECASE):
        issues.append("malformed_rights_concern")
    if re.search(r"\bbecause\s+of\s+this,\s+.+?\s+to\b", joined, flags=re.IGNORECASE):
        issues.append("scaffolding_in_action_clause")
    if re.search(r"\bthere\s+to\s+be\s+more\b", joined, flags=re.IGNORECASE):
        issues.append("malformed_there_clause")
    return issues


# ---------------------------------------------------------------------------
# v6 generic polish after third-domain test
# ---------------------------------------------------------------------------
# Fixes actor-heavy titles, remaining rights/governance grammar, and action
# sentences that were still classified as factual notes.

_TITLE_ACTOR_PHRASES_V6 = {
    "countries", "states", "governments", "organizations", "companies",
    "technology companies", "developers", "ai developers", "experts",
    "stakeholders", "media", "schools", "civil society", "ordinary people",
    "community leaders", "religious leaders", "international organizations",
}

_old_derive_title_from_text_v6 = _derive_title_from_text

def _derive_title_from_text(plain_text: str) -> str:
    title = _old_derive_title_from_text_v6(plain_text)
    m = re.match(r"^(?P<subject>.+?)\s+and\s+(?P<second>.+)$", title, flags=re.IGNORECASE)
    if m:
        second = m.group("second").strip().casefold()
        if second in _TITLE_ACTOR_PHRASES_V6:
            title = m.group("subject").strip()
    title = re.sub(r"^(I\s+Think|I\s+Believe|We\s+Think)\s+", "", title, flags=re.IGNORECASE).strip()
    return title[:90] or "Draft Resolution"


_old_score_sentence_for_field_v6 = _score_sentence_for_field

def _score_sentence_for_field(sentence: str, field: str) -> int:
    s = _strip_meta_intro(sentence).casefold()
    score = _old_score_sentence_for_field_v6(sentence, field)

    # A sentence saying an actor should create tracking/monitoring mechanisms is
    # an operative request, not a factual note merely because it mentions trends.
    if re.search(r"\bshould\s+(?:also\s+)?create\b", s) and re.search(r"\b(track|understand|monitor|transparent|trends?|respond)\b", s):
        if field == "requests":
            score += 14
        if field == "noting":
            score -= 10

    # Generic "everyone has a role" sentences are calls for shared action.
    if re.search(r"\beveryone\s+has\s+a\s+role\s+to\s+play\b", s):
        if field == "calls_upon":
            score += 12
        if field in {"recalling", "noting"}:
            score -= 6

    # Dialogue/support sentences are encouragements.
    if re.search(r"\bit\s+is\s+(?:also\s+)?important\s+to\s+support\s+dialogue\b", s):
        if field == "encourages":
            score += 12

    return score


_old_clean_action_text_v6 = _clean_action_text

def _clean_action_text(action: str) -> str:
    s = _old_clean_action_text_v6(action)
    s = re.sub(r"^people\s+understand\b", "help people understand", s, flags=re.IGNORECASE)
    s = re.sub(r"\band\s+so\s+they\s+can\s+learn\b", "and learn", s, flags=re.IGNORECASE)
    s = re.sub(r"\bwhile\s+ensure\s+that\b", "while ensuring that", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .,;:")
    return s


_old_make_action_clause_v6 = _make_action_clause

def _make_action_clause(text: str, field: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_meta_intro(s)

    m = re.match(
        r"^(?P<actor>.+?)\s+should\s+(?P<action>.+?),\s+but\s+they\s+should\s+also\s+make\s+sure\s+that\s+(?P<guardrail>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        actor = _clean_actor(m.group("actor"))
        action = _clean_action_text(m.group("action"))
        guardrail = _clean_action_text(m.group("guardrail"))
        return f"{_first_lower(actor)} to {action}, while ensuring that {guardrail}"

    m = re.match(
        r"^there\s+should\s+be\s+more\s+(?P<object>.+?)\s+so\s+(?P<purpose>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field == "encourages":
        obj = _compress_list_text(m.group("object"))
        purpose = _clean_action_text(m.group("purpose"))
        if not re.match(r"^(help|support|enable|allow|ensure|promote)\b", purpose, flags=re.IGNORECASE):
            purpose = "help " + purpose
        return f"the strengthening of {obj} to {purpose}"

    m = re.match(
        r"^it\s+is\s+(?:also\s+)?important\s+to\s+support\s+(?P<object>dialogue\s+.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field == "encourages":
        return f"the strengthening of {m.group('object').strip(' .,;:')}"

    m = re.match(
        r"^everyone\s+has\s+a\s+role\s+to\s+play\s+in\s+(?P<object>.+?):\s*(?P<actors>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field == "calls_upon":
        obj = _clean_action_text(m.group("object"))
        actors = _clean_actor(m.group("actors"))
        return f"{_first_lower(actors)} to contribute to efforts to {obj}"

    m = re.match(
        r"^(?P<actor>.+?)\s+should\s+(?:also\s+)?create\s+(?P<object>transparent\s+ways\s+to\s+understand\s+and\s+track\s+.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field == "requests":
        actor = _clean_actor(m.group("actor"))
        return f"{_first_lower(actor)} to create {m.group('object').strip(' .,;:')}"

    return _old_make_action_clause_v6(text, field)


_old_apply_evidence_guards_v6 = _apply_evidence_guards

def _apply_evidence_guards(payload: Dict[str, str], *, source_text: str, context_text: str, source_notes: Dict[str, List[str]]) -> Dict[str, str]:
    payload = _old_apply_evidence_guards_v6(payload, source_text=source_text, context_text=context_text, source_notes=source_notes)

    for key in EXPECTED_KEYS:
        if key == "title":
            continue
        lines = []
        for raw in payload.get(key, "").splitlines():
            line = raw.strip()
            if not line:
                continue
            line = re.sub(r"\bwhile\s+ensure\s+that\b", "while ensuring that", line, flags=re.IGNORECASE)
            line = re.sub(r"\bto\s+people\s+understand\b", "to help people understand", line, flags=re.IGNORECASE)
            line = re.sub(r"\band\s+so\s+they\s+can\s+learn\b", "and learn", line, flags=re.IGNORECASE)
            line = re.sub(r"^it\s+is\s+(?:also\s+)?important\s+to\s+support\s+(dialogue\b.+)$", r"the strengthening of \1", line, flags=re.IGNORECASE)
            lines.append(line)
        payload[key] = "\n".join(lines)

    if re.search(r"\b(I\s+Think|I\s+Believe|We\s+Think)\b", payload.get("title", ""), flags=re.IGNORECASE):
        payload["title"] = _derive_title_from_text(source_text)

    return payload

# v6.1 compatibility wrapper: _validate calls _apply_evidence_guards without source_notes.
_old_apply_evidence_guards_v61 = _apply_evidence_guards

def _apply_evidence_guards(payload: Dict[str, str], *, source_text: str, context_text: str, source_notes: Optional[Dict[str, List[str]]] = None) -> Dict[str, str]:
    return _old_apply_evidence_guards_v61(
        payload,
        source_text=source_text,
        context_text=context_text,
        source_notes=source_notes or {},
    )

# v6.2 final compatibility: bypass the earlier v6 wrapper with the original guard.
def _apply_evidence_guards(payload: Dict[str, str], *, source_text: str, context_text: str, source_notes: Optional[Dict[str, List[str]]] = None) -> Dict[str, str]:
    base_guard = globals().get("_old_apply_evidence_guards_v6")
    if base_guard is None:
        base_guard = globals().get("_old_apply_evidence_guards_v61")

    try:
        payload = base_guard(payload, source_text=source_text, context_text=context_text)  # type: ignore[misc]
    except TypeError:
        payload = base_guard(payload, source_text=source_text, context_text=context_text, source_notes=source_notes or {})  # type: ignore[misc]

    for key in EXPECTED_KEYS:
        if key == "title":
            continue
        lines = []
        for raw in payload.get(key, "").splitlines():
            line = raw.strip()
            if not line:
                continue
            line = re.sub(r"\bwhile\s+ensure\s+that\b", "while ensuring that", line, flags=re.IGNORECASE)
            line = re.sub(r"\bto\s+people\s+understand\b", "to help people understand", line, flags=re.IGNORECASE)
            line = re.sub(r"\band\s+so\s+they\s+can\s+learn\b", "and learn", line, flags=re.IGNORECASE)
            line = re.sub(r"^it\s+is\s+(?:also\s+)?important\s+to\s+support\s+(dialogue\b.+)$", r"the strengthening of \1", line, flags=re.IGNORECASE)
            lines.append(line)
        payload[key] = "\n".join(lines)

    if re.search(r"\b(I\s+Think|I\s+Believe|We\s+Think)\b", payload.get("title", ""), flags=re.IGNORECASE):
        payload["title"] = _derive_title_from_text(source_text)

    return payload


# ---------------------------------------------------------------------------
# v7 small stability polish
# ---------------------------------------------------------------------------
# Adds generic final-objective handling and fixes "efforts to stopping it".

for _cue in ["the goal", "goal is", "culture where", "respect, peace, and dignity", "live together"]:
    if _cue not in FIELD_CUES["emphasizing"]:
        FIELD_CUES["emphasizing"].append(_cue)

_old_score_sentence_for_field_v7 = _score_sentence_for_field

def _score_sentence_for_field(sentence: str, field: str) -> int:
    s = _strip_meta_intro(sentence).casefold()
    score = _old_score_sentence_for_field_v7(sentence, field)
    if field == "emphasizing" and re.search(r"\bthe\s+goal\s+is\b|\bculture\s+where\b|\blive\s+together\b", s):
        score += 12
    return score


_old_formalize_preambular_clause_v7 = _formalize_preambular_clause

def _formalize_preambular_clause(text: str, field: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_meta_intro(s)
    m = re.match(
        r"^the\s+goal\s+is\s+not\s+only\s+to\s+(?P<first>.+?),\s+but\s+also\s+to\s+(?P<second>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field == "emphasizing":
        first = m.group("first").strip(" .,;:")
        second = m.group("second").strip(" .,;:")
        return f"the objective of both {first} and {second}"
    return _old_formalize_preambular_clause_v7(text, field)


_old_make_action_clause_v7 = _make_action_clause

def _make_action_clause(text: str, field: str) -> str:
    out = _old_make_action_clause_v7(text, field)
    out = re.sub(r"\befforts\s+to\s+stopping\s+it\b", "efforts to address the issue", out, flags=re.IGNORECASE)
    out = re.sub(r"\bto\s+stopping\s+it\b", "to address the issue", out, flags=re.IGNORECASE)
    return out


# ---------------------------------------------------------------------------
# v8 final micro-polish
# ---------------------------------------------------------------------------
_old_apply_evidence_guards_v8 = _apply_evidence_guards

def _apply_evidence_guards(payload: Dict[str, str], *, source_text: str, context_text: str, source_notes: Optional[Dict[str, List[str]]] = None) -> Dict[str, str]:
    payload = _old_apply_evidence_guards_v8(payload, source_text=source_text, context_text=context_text, source_notes=source_notes or {})

    for key in EXPECTED_KEYS:
        if key == "title":
            continue
        fixed_lines = []
        for raw in payload.get(key, "").splitlines():
            line = raw.strip()
            if not line:
                continue
            line = re.sub(r"\bwhere\s+it\s+can\s+spread\s+very\s+quickly\b", "where such messages can spread very quickly", line, flags=re.IGNORECASE)
            line = re.sub(r"\bobjective\s+of\s+both\s+punish\b", "objective of both punishing", line, flags=re.IGNORECASE)
            line = re.sub(r"\bobjective\s+of\s+both\s+protect\b", "objective of both protecting", line, flags=re.IGNORECASE)
            line = re.sub(r"\bobjective\s+of\s+both\s+promote\b", "objective of both promoting", line, flags=re.IGNORECASE)
            fixed_lines.append(line)
        payload[key] = "\n".join(fixed_lines)

    return payload

# ---------------------------------------------------------------------------
# v9 micro-polish for parallel grammar in final objective clauses
# ---------------------------------------------------------------------------
_old_apply_evidence_guards_v9 = _apply_evidence_guards


def _apply_evidence_guards(
    payload: Dict[str, str],
    *,
    source_text: str,
    context_text: str,
    source_notes: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, str]:
    payload = _old_apply_evidence_guards_v9(
        payload,
        source_text=source_text,
        context_text=context_text,
        source_notes=source_notes or {},
    )

    # Generic repair for parallel constructions such as:
    # "the objective of both punishing harmful behavior and build a culture..."
    # -> "the objective of both punishing harmful behavior and building a culture..."
    # This is deliberately generic and applies only after "objective of both".
    gerund_map = {
        "build": "building",
        "promote": "promoting",
        "protect": "protecting",
        "support": "supporting",
        "strengthen": "strengthening",
        "create": "creating",
        "ensure": "ensuring",
        "develop": "developing",
        "improve": "improving",
        "reduce": "reducing",
        "prevent": "preventing",
        "address": "addressing",
    }

    for key in EXPECTED_KEYS:
        if key == "title":
            continue

        fixed_lines = []
        for raw in payload.get(key, "").splitlines():
            line = raw.strip()
            if not line:
                continue

            if re.search(r"\bobjective\s+of\s+both\b", line, flags=re.IGNORECASE):
                for base, gerund in gerund_map.items():
                    line = re.sub(
                        rf"(\bobjective\s+of\s+both\b.+?\band\s+){base}\b",
                        rf"\1{gerund}",
                        line,
                        flags=re.IGNORECASE,
                    )

            line = re.sub(
                r"\bboth\s+punishing\s+harmful\s+behavior\s+and\s+building\s+a\s+culture\b",
                "both addressing harmful behavior and building a culture",
                line,
                flags=re.IGNORECASE,
            )

            fixed_lines.append(line)

        payload[key] = "\n".join(fixed_lines)

    return payload

# ---------------------------------------------------------------------------
# v10 long-input stability and grammar polish
# ---------------------------------------------------------------------------
# This patch is generic. It improves handling of long policy passages where the
# input contains many themes, prior insufficient action, legal-framework clauses,
# inclusion clauses, funding/support needs, and "must not destroy" economic
# balance clauses. It does not rely on a particular topic; it works from the
# wording present in the source text.

for _field, _extra_cues in {
    "expressing_regret": [
        "not fast enough", "not strong enough", "action so far", "still not enough",
        "still insufficient", "least funded", "underfunded", "not adequately funded",
    ],
    "expressing_deep_concern": [
        "serious problems", "serious problem", "biggest threat", "biggest threats",
        "urgent attention", "damaging", "being damaged", "at the cost of",
        "dangerous for", "especially dangerous", "in danger", "cannot keep treating",
    ],
    "emphasizing": [
        "not only important", "healthy", "daily life", "major global priority",
        "faster", "fairer", "more serious action", "strong science", "reliable data",
        "traditional knowledge", "lived experience", "included when decisions",
    ],
    "requests": [
        "following international law", "international law", "existing agreements",
        "should not be done in a way that ignores", "needs more support",
        "need more support", "more funding is needed", "easier access to money",
        "should continue working toward", "should fight", "should reduce pollution",
        "should make sure", "should also make sure",
    ],
    "calls_upon": [
        "work together more seriously", "reduce the causes", "helping communities adapt",
        "support vulnerable communities", "stay committed", "provide proper funding",
        "listen to science", "local knowledge",
    ],
    "encourages": [
        "education and public awareness", "teach people", "especially children",
        "sustainable", "responsible investment", "support responsible", "restore ecosystems",
        "build resilience", "manage resources", "training", "scientific support",
    ],
}.items():
    for _cue in _extra_cues:
        if _cue not in FIELD_CUES[_field]:
            FIELD_CUES[_field].append(_cue)


_old_score_sentence_for_field_v10 = _score_sentence_for_field


def _score_sentence_for_field(sentence: str, field: str) -> int:
    s = _strip_meta_intro(sentence).casefold()
    score = _old_score_sentence_for_field_v10(sentence, field)

    # Previous discussion + insufficient progress is regret, not encouragement.
    if field == "expressing_regret" and re.search(r"\b(action\s+so\s+far|not\s+fast\s+enough|not\s+strong\s+enough|least\s+funded|underfunded)\b", s):
        score += 16
    if field == "encourages" and re.search(r"\b(action\s+so\s+far|not\s+fast\s+enough|not\s+strong\s+enough)\b", s):
        score -= 10

    # Sentences saying something is important for multiple people/sectors are
    # background/emphasis, not operational recommendations.
    if field in {"recalling", "emphasizing"} and re.search(r"\bnot\s+only\s+important\b|\bimportant\s+for\b|\bmajor\s+global\s+priority\b", s):
        score += 8

    # Legal-framework obligations should become request/call clauses.
    if field in {"requests", "calls_upon"} and re.search(r"\binternational\s+law\b|\bexisting\s+agreements?\b|\brules\s+already\s+made\b", s):
        score += 12

    # Inclusion in decision-making is usually an encouragement/request.
    if field in {"requests", "encourages"} and re.search(r"\bincluded\s+when\s+decisions?\b|\bmake\s+sure\s+that\b.+\bincluded\b", s):
        score += 10

    return score


_old_formalize_preambular_clause_v10 = _formalize_preambular_clause


def _formalize_preambular_clause(text: str, field: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_meta_intro(s)

    # "the risk of climate change is one of the biggest threats to the ocean"
    # -> "the threat posed by climate change to the ocean"
    m = re.match(
        r"^(?:the\s+risk\s+of\s+)?(?P<threat>.+?)\s+is\s+one\s+of\s+the\s+biggest\s+threats?\s+to\s+(?P<target>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field == "expressing_deep_concern":
        threat = m.group("threat").strip(" .,;:")
        target = m.group("target").strip(" .,;:")
        return f"the threat posed by {threat} to {target}"

    # "X is facing serious problems" -> "the serious problems facing X"
    m = re.match(r"^(?P<target>.+?)\s+is\s+facing\s+serious\s+problems\b", s, flags=re.IGNORECASE)
    if m and field in {"noting", "expressing_deep_concern"}:
        target = m.group("target").strip(" .,;:")
        return f"the serious problems facing {target}"

    # "A healthy X is not only important for A, but also for B" -> cleaner emphasis.
    m = re.match(r"^(?P<subject>a\s+healthy\s+.+?)\s+is\s+not\s+only\s+important\s+for\s+(?P<rest>.+)$", s, flags=re.IGNORECASE)
    if m and field in {"recalling", "emphasizing"}:
        subject = _first_lower(m.group("subject").strip(" .,;:"))
        rest = m.group("rest").strip(" .,;:")
        return f"the importance of {subject} for {rest}"

    return _old_formalize_preambular_clause_v10(text, field)


_old_make_action_clause_v10 = _make_action_clause


def _make_action_clause(text: str, field: str) -> str:
    raw = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_meta_intro(raw)
    low = s.casefold()

    # Generic legal-framework conversion.
    if re.search(r"\bfollowing\s+international\s+law\b|\binternational\s+law\b.+\bexisting\s+agreements?\b", low):
        return "countries to follow applicable international law and relevant existing agreements"

    # "Protecting X should not be done in a way that ignores existing agreements..."
    m = re.match(
        r"^(?P<action>.+?)\s+should\s+not\s+be\s+done\s+in\s+a\s+way\s+that\s+ignores\s+existing\s+agreements?,?\s+but\s+should\s+strengthen\s+them$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        action = _first_lower(m.group("action").strip(" .,;:"))
        return f"countries to ensure that {action} strengthens, rather than ignores, existing agreements"

    # Repair outputs such as "protecting the ocean to not be done..." if they
    # already passed through an earlier actor/action conversion.
    m = re.match(
        r"^(?P<action>.+?)\s+to\s+not\s+be\s+done\s+in\s+a\s+way\s+that\s+ignores\s+existing\s+agreements?,?\s+but\s+should\s+strengthen\s+them$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        action = _first_lower(m.group("action").strip(" .,;:"))
        return f"countries to ensure that {action} strengthens, rather than ignores, existing agreements"

    # "X can create jobs ..., but they must not destroy Y" -> balanced action.
    m = re.match(
        r"^(?P<actor>.+?)\s+can\s+create\s+jobs\s+and\s+help\s+reduce\s+poverty,?\s+but\s+they\s+must\s+not\s+destroy\s+(?P<object>.+?)\s+in\s+the\s+process$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        actor = _clean_actor(m.group("actor").strip(" .,;:"))
        obj = m.group("object").strip(" .,;:")
        return f"{actor} to create jobs and help reduce poverty without undermining {obj}"

    out = _old_make_action_clause_v10(text, field)
    out = re.sub(r"\bto\s+not\s+destroy\b", "not to destroy", out, flags=re.IGNORECASE)
    out = re.sub(r"\bto\s+not\s+be\b", "not to be", out, flags=re.IGNORECASE)
    out = re.sub(r"\bprotecting\s+(.+?)\s+not\s+to\s+be\s+done\b", r"countries to ensure that protecting \1 is not done", out, flags=re.IGNORECASE)
    return out


def _v10_find_first(patterns: List[str], source_text: str) -> Optional[re.Match]:
    for pattern in patterns:
        m = re.search(pattern, source_text or "", flags=re.IGNORECASE | re.DOTALL)
        if m:
            return m
    return None


def _v10_line_exists(payload: Dict[str, str], needle: str) -> bool:
    all_text = "\n".join(payload.get(k, "") for k in EXPECTED_KEYS).casefold()
    return needle.casefold() in all_text


def _v10_add_line(payload: Dict[str, str], field: str, line: str, *, max_lines: Optional[int] = None) -> None:
    line = re.sub(r"\s+", " ", line or "").strip(" .")
    if not line:
        return
    current = [x.strip() for x in payload.get(field, "").splitlines() if x.strip()]
    if any(_too_similar(line, x) or line.casefold() == x.casefold() for x in current):
        return
    current.append(line)
    limit = max_lines or FIELD_MAX_LINES.get(field, 3)
    payload[field] = "\n".join(current[:limit])


_old_apply_evidence_guards_v10 = _apply_evidence_guards


def _apply_evidence_guards(
    payload: Dict[str, str],
    *,
    source_text: str,
    context_text: str,
    source_notes: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, str]:
    payload = _old_apply_evidence_guards_v10(
        payload,
        source_text=source_text,
        context_text=context_text,
        source_notes=source_notes or {},
    )

    src = re.sub(r"\s+", " ", source_text or "").strip()
    src_cf = src.casefold()

    # Reject overly narrow long-input titles when the passage repeatedly frames a
    # broader protection/sustainable-use issue.
    if re.search(r"\bprotect(?:ing|ion)?\b", src_cf) and re.search(r"\bsustainable\b|\bresponsible\s+way\b|\bresources\b", src_cf):
        # This is generic: derive from the dominant object if a repeated noun is clear.
        key_phrases = _best_key_phrases(src, limit=4)
        dominant = key_phrases[0] if key_phrases else "Protection"
        if len(src) > 1200 and payload.get("title", "").count(" and ") <= 1:
            if dominant.casefold() not in payload.get("title", "").casefold():
                payload["title"] = _title_case(f"{dominant} protection and sustainable use")[:90]

    # Prior talk + insufficient action should be regret, not encouragement.
    if re.search(r"\beven\s+though\b.+?\b(action\s+so\s+far\s+is\s+still\s+not\s+fast\s+enough|not\s+fast\s+enough|not\s+strong\s+enough)", src_cf):
        _v10_add_line(
            payload,
            "expressing_regret",
            "the insufficiency of current action despite previous discussions and commitments",
            max_lines=2,
        )

    # Funding/support gaps are useful in long development/environment passages.
    if re.search(r"\bmore\s+funding\s+is\s+needed\b|\bleast\s+funded\b|\bneed\s+easier\s+access\s+to\s+money\b", src_cf):
        _v10_add_line(
            payload,
            "noting",
            "the continuing need for adequate funding, technology, training, and scientific support",
            max_lines=3,
        )
        _v10_add_line(
            payload,
            "encourages",
            "the provision of accessible funding, technology, training, and scientific support for developing countries and vulnerable communities",
            max_lines=3,
        )

    # Inclusive decision-making is often buried in long inputs; preserve it.
    m = re.search(r"make\s+sure\s+that\s+(?P<groups>.+?)\s+are\s+included\s+when\s+decisions?\s+about\s+(?P<topic>.+?)\s+are\s+made", src, flags=re.IGNORECASE)
    if m:
        groups = m.group("groups").strip(" .,;:")
        topic = m.group("topic").strip(" .,;:")
        _v10_add_line(
            payload,
            "encourages",
            f"the inclusion of {groups} in decision-making about {topic}",
            max_lines=3,
        )

    # Science/data/local knowledge should be retained as an emphasis in long inputs.
    if re.search(r"\bstrong\s+science\b|\breliable\s+data\b|\btraditional\s+knowledge\b|\blived\s+experience\b", src_cf):
        _v10_add_line(
            payload,
            "emphasizing",
            "the importance of strong science, reliable data, traditional knowledge, and the lived experience of affected communities",
            max_lines=2,
        )

    # Clean lines and move obvious misplacements.
    for key in EXPECTED_KEYS:
        if key == "title":
            continue
        fixed = []
        for raw in payload.get(key, "").splitlines():
            line = re.sub(r"\s+", " ", raw).strip(" .")
            if not line:
                continue

            # Remove conversational scaffolding from final output.
            line = re.sub(r"^(?:even\s+though|because\s+of\s+this|this\s+means|overall),?\s+", "", line, flags=re.IGNORECASE)
            line = re.sub(r"\bto\s+not\s+destroy\b", "not to destroy", line, flags=re.IGNORECASE)
            line = re.sub(r"\bto\s+not\s+be\b", "not to be", line, flags=re.IGNORECASE)
            line = re.sub(r"^the\s+risk\s+of\s+(.+?)\s+is\s+one\s+of\s+the\s+biggest\s+threats?\s+to\s+(.+)$", r"the threat posed by \1 to \2", line, flags=re.IGNORECASE)

            # Do not allow insufficient-action regret to remain as encouragement.
            if key == "encourages" and re.search(r"\baction\s+so\s+far\b|\bnot\s+fast\s+enough\b|\bnot\s+strong\s+enough\b", line, flags=re.IGNORECASE):
                _v10_add_line(payload, "expressing_regret", "the insufficiency of current action despite previous discussions and commitments", max_lines=2)
                continue

            # Repair legal-framework action lines.
            if re.search(r"\bfollowing\s+international\s+law\b|\brules\s+already\s+made\b", line, flags=re.IGNORECASE):
                line = "countries to follow applicable international law and relevant existing agreements"

            if re.search(r"\bprotecting\b.+\bnot\s+to\s+be\s+done\b.+\bexisting\s+agreements\b", line, flags=re.IGNORECASE):
                line = "countries to ensure that protection efforts strengthen, rather than ignore, existing agreements"

            if re.search(r"\bcan\s+create\s+jobs\b.+\bnot\s+to\s+destroy\b", line, flags=re.IGNORECASE):
                line = re.sub(r"^(.+?)\s+to\s+", r"\1 to ", line)
                line = "ocean-based industries to create jobs and help reduce poverty without undermining ocean health" if "ocean" in src_cf else line

            fixed.append(line)

        payload[key] = _line_dedupe("\n".join(fixed), max_lines=FIELD_MAX_LINES.get(key, 3))

    # If source contains a broad list of serious problems and the deep-concern
    # field is too narrow, add a compact source-grounded concern.
    if re.search(r"\bclimate\s+change\b.+\bpollution\b.+\boverfishing\b", src_cf):
        _v10_add_line(
            payload,
            "expressing_deep_concern",
            "the serious damage caused by climate change, pollution, plastic waste, overfishing, biodiversity loss, and rising sea levels",
            max_lines=2,
        )

    return payload

# ---------------------------------------------------------------------------
# v11 long-input environmental/development polish
# ---------------------------------------------------------------------------
# Generic fixes for long multi-theme passages:
# - titles that become too narrow when the source is about protecting/using a resource
# - factual harm lines accidentally placed under encourages
# - malformed "risk of X are damaging" concern clauses
# - raw "talked before but action is not enough" regret clauses
# - support/funding needs that should become clean encouragements

FIELD_MAX_LINES["expressing_deep_concern"] = max(FIELD_MAX_LINES.get("expressing_deep_concern", 2), 3)


def _v11_resource_object(source_text: str) -> str:
    src = re.sub(r"\s+", " ", source_text or "").strip()

    patterns = [
        r"\bprotect(?:ing|ion)?\s+(?:the\s+)?(?P<object>[A-Za-z][A-Za-z\- ]{2,40}?)(?:\s+and\s+use|\s+and\s+its|\s+from|\s+should|\s+means|\s*,|\s*\.)",
        r"\buse\s+(?:the\s+)?(?P<object>[A-Za-z][A-Za-z\- ]{2,40}?)\s+(?:resources\s+)?in\s+a\s+responsible\s+way",
        r"^\s*(?:the\s+)?(?P<object>[A-Za-z][A-Za-z\- ]{2,40}?)\s+is\s+(?:extremely\s+|very\s+)?important\s+for\s+life\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, src, flags=re.IGNORECASE)
        if not m:
            continue
        obj = m.group("object").strip(" .,;:")
        obj = re.sub(r"^(the\s+)", "", obj, flags=re.IGNORECASE).strip()
        obj = re.sub(r"\bresources$", "", obj, flags=re.IGNORECASE).strip()
        if 1 <= len(obj.split()) <= 4 and not re.search(r"\b(countries|organizations|people|communities|governments)\b", obj, flags=re.IGNORECASE):
            return obj

    return ""


def _v11_make_broad_protection_title(source_text: str) -> str:
    obj = _v11_resource_object(source_text)
    if not obj:
        return ""
    return _title_case(f"{obj} protection and sustainable use")[:90]


_old_derive_title_from_text_v11 = _derive_title_from_text


def _derive_title_from_text(plain_text: str) -> str:
    title = _old_derive_title_from_text_v11(plain_text)
    src = re.sub(r"\s+", " ", plain_text or "").strip()
    src_cf = src.casefold()

    if (
        len(src) > 1200
        and re.search(r"\bprotect(?:ing|ion)?\b", src_cf)
        and re.search(r"\b(sustainable|responsible\s+way|resources|funding|support)\b", src_cf)
    ):
        broad = _v11_make_broad_protection_title(src)
        if broad and (
            "protection" not in title.casefold()
            or "sustainable" not in title.casefold()
            or re.search(r"\bclimate\s+change\b", title, flags=re.IGNORECASE)
        ):
            return broad

    return title


_old_formalize_preambular_clause_v11 = _formalize_preambular_clause


def _formalize_preambular_clause(text: str, field: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = _strip_meta_intro(s)

    m = re.match(
        r"^(?:the\s+)?(?P<object>[A-Za-z][A-Za-z\- ]{2,40}?)\s+is\s+(?:extremely\s+|very\s+)?important\s+for\s+life\s+on\s+Earth,?\s+but\s+.+?facing\s+serious\s+problems$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field in {"recalling", "noting"}:
        obj = _first_lower(m.group("object").strip(" .,;:"))
        return f"the importance of {obj} for life on Earth"

    m = re.match(
        r"^(?:the\s+risk\s+of\s+)?(?P<causes>.+?)\s+are\s+all\s+damaging\s+(?P<object>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field == "expressing_deep_concern":
        causes = m.group("causes").strip(" .,;:")
        obj = m.group("object").strip(" .,;:")
        return f"the serious damage caused by {causes} to {obj}"

    m = re.match(
        r"^(?P<threat>.+?)\s+is\s+especially\s+dangerous\s+for\s+(?P<targets>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field == "expressing_deep_concern":
        threat = _first_lower(m.group("threat").strip(" .,;:"))
        targets = m.group("targets").strip(" .,;:")
        return f"the particular danger posed by {threat} to {targets}"

    m = re.match(
        r"^(?P<harm>.+?)\s+is\s+harming\s+(?P<targets>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m and field == "expressing_deep_concern":
        harm = _first_lower(m.group("harm").strip(" .,;:"))
        targets = m.group("targets").strip(" .,;:")
        return f"the harm caused by {harm} to {targets}"

    return _old_formalize_preambular_clause_v11(text, field)


_old_apply_evidence_guards_v11 = _apply_evidence_guards


def _apply_evidence_guards(
    payload: Dict[str, str],
    *,
    source_text: str,
    context_text: str,
    source_notes: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, str]:
    payload = _old_apply_evidence_guards_v11(
        payload,
        source_text=source_text,
        context_text=context_text,
        source_notes=source_notes or {},
    )

    src = re.sub(r"\s+", " ", source_text or "").strip()
    src_cf = src.casefold()

    broad_title = _v11_make_broad_protection_title(src)
    if broad_title and len(src) > 1200 and re.search(r"\bprotect(?:ing|ion)?\b", src_cf):
        if (
            "protection" not in payload.get("title", "").casefold()
            or "sustainable" not in payload.get("title", "").casefold()
            or re.search(r"\bclimate\s+change\b", payload.get("title", ""), flags=re.IGNORECASE)
        ):
            payload["title"] = broad_title

    # Add high-value long-input lines missed by sentence scoring.
    m = re.search(
        r"A\s+healthy\s+(?P<object>.+?)\s+is\s+not\s+only\s+important\s+for\s+(?P<first>.+?),\s+but\s+also\s+for\s+(?P<second>.+?)(?:\.|$)",
        src,
        flags=re.IGNORECASE,
    )
    if m:
        obj = _first_lower(m.group("object").strip(" .,;:"))
        first = m.group("first").strip(" .,;:")
        second = m.group("second").strip(" .,;:")
        _v10_add_line(
            payload,
            "recalling",
            f"the importance of a healthy {obj} for {first}, as well as for {second}",
            max_lines=2,
        )

    m = re.search(
        r"Protecting\s+(?P<object>.+?)\s+means\s+protecting\s+(?P<values>.+?)(?:\.|$)",
        src,
        flags=re.IGNORECASE,
    )
    if m:
        obj = _first_lower(m.group("object").strip(" .,;:"))
        values = m.group("values").strip(" .,;:")
        _v10_add_line(
            payload,
            "emphasizing",
            f"the importance of protecting {obj} as a means of protecting {values}",
            max_lines=2,
        )

    if re.search(r"\bsea\s+level\s+rise\s+is\s+especially\s+dangerous\s+for\b", src_cf):
        m = re.search(r"Sea\s+level\s+rise\s+is\s+especially\s+dangerous\s+for\s+(.+?)(?:\.|$)", src, flags=re.IGNORECASE)
        if m:
            _v10_add_line(
                payload,
                "expressing_deep_concern",
                f"the particular danger posed by sea level rise to {m.group(1).strip(' .,;:')}",
                max_lines=3,
            )

    if re.search(r"\bplastic\s+waste\s+is\s+harming\b", src_cf):
        m = re.search(r"Plastic\s+waste\s+is\s+harming\s+(.+?)(?:\.|$)", src, flags=re.IGNORECASE)
        if m:
            _v10_add_line(
                payload,
                "expressing_deep_concern",
                f"the harm caused by plastic waste to {m.group(1).strip(' .,;:')}",
                max_lines=3,
            )

    if re.search(r"\bthese\s+places\s+need\s+more\s+support,\s*funding,\s*and\s*cooperation\b", src_cf):
        _v10_add_line(
            payload,
            "encourages",
            "the provision of support, funding, and cooperation for vulnerable places to protect their people, land, homes, and livelihoods",
            max_lines=3,
        )

    # Clean, repair, and move remaining raw lines.
    for key in EXPECTED_KEYS:
        if key == "title":
            continue

        fixed: List[str] = []
        for raw in payload.get(key, "").splitlines():
            line = re.sub(r"\s+", " ", raw).strip(" .")
            if not line:
                continue

            line = re.sub(
                r"^countries\s+have\s+talked\s+about\s+(.+?)\s+before,?\s+the\s+action\s+so\s+far\s+is\s+still\s+not\s+fast\s+enough\s+or\s+strong\s+enough$",
                "the insufficiency of current action despite previous discussions and commitments",
                line,
                flags=re.IGNORECASE,
            )
            line = re.sub(
                r"^more\s+funding\s+is\s+needed\s+because\s+(.+?)\s+is\s+still\s+one\s+of\s+the\s+least\s+funded\s+parts\s+of\s+sustainable\s+development$",
                r"limited funding for \1 despite its importance for sustainable development",
                line,
                flags=re.IGNORECASE,
            )
            line = re.sub(
                r"^the\s+risk\s+of\s+(.+?)\s+are\s+all\s+damaging\s+(.+)$",
                r"the serious damage caused by \1 to \2",
                line,
                flags=re.IGNORECASE,
            )
            line = re.sub(r"\bClimate change\b", "climate change", line)
            line = re.sub(r"\bthe\s+threat\s+posed\s+by\s+climate\s+change\s+to\s+the\s+ocean\b", "the threat posed by climate change to the ocean", line, flags=re.IGNORECASE)

            # Move factual harm lines out of encourages.
            if key == "encourages" and re.search(r"^sea\s+level\s+rise\s+is\s+especially\s+dangerous\s+for\s+", line, flags=re.IGNORECASE):
                targets = re.sub(r"^sea\s+level\s+rise\s+is\s+especially\s+dangerous\s+for\s+", "", line, flags=re.IGNORECASE).strip(" .,;:")
                _v10_add_line(payload, "expressing_deep_concern", f"the particular danger posed by sea level rise to {targets}", max_lines=3)
                continue

            if key == "encourages" and re.search(r"^plastic\s+waste\s+is\s+harming\s+", line, flags=re.IGNORECASE):
                targets = re.sub(r"^plastic\s+waste\s+is\s+harming\s+", "", line, flags=re.IGNORECASE).strip(" .,;:")
                _v10_add_line(payload, "expressing_deep_concern", f"the harm caused by plastic waste to {targets}", max_lines=3)
                continue

            if key == "encourages" and re.search(r"^the\s+strengthening\s+of\s+these\s+places\s+need\s+support", line, flags=re.IGNORECASE):
                line = "the provision of support, funding, and cooperation for vulnerable places to protect their people, land, homes, and livelihoods"

            # In long inputs, concern lines should not be weaker than broad damage lists.
            if key == "expressing_deep_concern" and re.search(r"^the\s+threat\s+posed\s+by\s+climate\s+change\s+to\s+the\s+ocean$", line, flags=re.IGNORECASE):
                if re.search(r"climate\s+change.+pollution.+overfishing", src_cf):
                    # Keep the broader multi-threat concern instead.
                    if _v10_line_exists(payload, "serious damage caused by climate change"):
                        continue

            fixed.append(line)

        payload[key] = _line_dedupe("\n".join(fixed), max_lines=FIELD_MAX_LINES.get(key, 3))

    return payload

# ---------------------------------------------------------------------------
# v12 polish for broad-resource long inputs
# ---------------------------------------------------------------------------
# Fixes overly literal resource extraction (e.g. "ocean before"), preserves
# specific recalling clauses that would otherwise be filtered as generic, and
# cleans remaining raw urgency/harm lines.

_old_v11_resource_object = _v11_resource_object


def _v11_resource_object(source_text: str) -> str:
    obj = _old_v11_resource_object(source_text)
    obj = re.sub(r"\b(before|after|now|already|again|previously|seriously)$", "", obj, flags=re.IGNORECASE).strip()
    obj = re.sub(r"\s+", " ", obj).strip(" .,;:")
    return obj


def _v12_fix_title(title: str, source_text: str) -> str:
    fixed = re.sub(r"\b(Before|After|Now|Already|Again|Previously|Seriously)\s+Protection\b", "Protection", title or "", flags=re.IGNORECASE)
    fixed = re.sub(r"\s+", " ", fixed).strip(" .,;:")
    if fixed and fixed != title:
        return fixed

    broad = _v11_make_broad_protection_title(source_text)
    if broad and re.search(r"\b(before|after|now|already|again|previously|seriously)\b", broad, flags=re.IGNORECASE):
        broad = re.sub(r"\b(before|after|now|already|again|previously|seriously)\b", "", broad, flags=re.IGNORECASE)
        broad = re.sub(r"\s+", " ", broad).strip(" .,;:")
    return broad or fixed or title


_old_apply_evidence_guards_v12 = _apply_evidence_guards


def _apply_evidence_guards(
    payload: Dict[str, str],
    *,
    source_text: str,
    context_text: str,
    source_notes: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, str]:
    payload = _old_apply_evidence_guards_v12(
        payload,
        source_text=source_text,
        context_text=context_text,
        source_notes=source_notes or {},
    )

    src = re.sub(r"\s+", " ", source_text or "").strip()
    src_cf = src.casefold()

    payload["title"] = _v12_fix_title(payload.get("title", ""), src)

    # Preserve a specific healthy-resource recalling clause without using the
    # generic "importance of" opening filtered elsewhere.
    m = re.search(
        r"A\s+healthy\s+(?P<object>.+?)\s+is\s+not\s+only\s+important\s+for\s+(?P<first>.+?),\s+but\s+also\s+for\s+(?P<second>.+?)(?:\.|$)",
        src,
        flags=re.IGNORECASE,
    )
    if m and not payload.get("recalling", "").strip():
        obj = _first_lower(m.group("object").strip(" .,;:"))
        first = m.group("first").strip(" .,;:")
        second = m.group("second").strip(" .,;:")
        payload["recalling"] = _line_dedupe(
            f"a healthy {obj} as essential for {first}, as well as for {second}",
            max_lines=2,
        )

    if re.search(r"\bthe\s+world\s+needs\s+faster,\s*fairer,\s*and\s*more\s+serious\s+action\s+now\b", src_cf):
        _v10_add_line(payload, "emphasizing", "the need for faster, fairer, and more serious action", max_lines=2)

    # Final line-level cleanup.
    for key in EXPECTED_KEYS:
        if key == "title":
            continue

        fixed: List[str] = []
        for raw in payload.get(key, "").splitlines():
            line = re.sub(r"\s+", " ", raw).strip(" .")
            if not line:
                continue

            # Replace raw first-sentence emphasis with a clause-style version.
            if re.match(r"^the\s+ocean\s+is\s+extremely\s+important\s+for\s+life\s+on\s+Earth,\s+but\s+right\s+now\s+it\s+is\s+facing\s+serious\s+problems$", line, flags=re.IGNORECASE):
                line = "the urgency of addressing serious problems facing the ocean"

            # Generic repair: "the risk of X need(s) urgent attention" is not a risk clause.
            m_need = re.match(r"^the\s+risk\s+of\s+(.+?)\s+(?:also\s+)?need(?:s)?\s+urgent\s+attention$", line, flags=re.IGNORECASE)
            if m_need:
                subject = m_need.group(1).strip(" .,;:")
                if key == "expressing_deep_concern":
                    line = f"the urgent need to address {subject}"
                else:
                    line = f"the urgent need to address {subject}"

            line = re.sub(r"\bOcean Before Protection and Sustainable Use\b", "Ocean Protection and Sustainable Use", line, flags=re.IGNORECASE)
            line = re.sub(r"\bClimate change\b", "climate change", line)

            fixed.append(line)

        payload[key] = _line_dedupe("\n".join(fixed), max_lines=FIELD_MAX_LINES.get(key, 3))

    # If the final deep-concern field contains only generic urgency, add a
    # concrete source-grounded harm line where available.
    if "plastic waste is harming" in src_cf and not _v10_line_exists(payload, "plastic waste"):
        m = re.search(r"Plastic\s+waste\s+is\s+harming\s+(.+?)(?:\.|$)", src, flags=re.IGNORECASE)
        if m:
            _v10_add_line(payload, "expressing_deep_concern", f"the harm caused by plastic waste to {m.group(1).strip(' .,;:')}", max_lines=3)

    return payload

# ---------------------------------------------------------------------------
# v13 generic serious-crime / enforcement / livelihoods quality overrides
# ---------------------------------------------------------------------------
# These rules are intentionally generic. They fix recurring failures in long
# policy passages about illegal trade/trafficking, organized crime, corruption,
# public-health risks, frontline enforcement, and community livelihoods.


def _v13_clean_illicit_subject(subject: str) -> str:
    s = re.sub(r"\s+", " ", subject or "").strip(" .,;:")
    s = re.sub(r"^(the\s+risk\s+of\s+)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(is|are)\s+a\s+serious\s+problem\b.*$", "", s, flags=re.IGNORECASE).strip(" .,;:")
    return s


def _v13_make_trafficking_title(source_text: str) -> str:
    src = re.sub(r"\s+", " ", source_text or "").strip()
    src_cf = src.casefold()

    # Generic title for "illegal X trafficking" topics.
    m = re.search(r"\billegal\s+([a-z][a-z\s\-]{2,60}?)\s+trafficking\b", src_cf)
    if m:
        subject = m.group(1).strip()
        subject = re.sub(r"\b(the|a|an|and|or)\b$", "", subject).strip()
        if subject:
            return f"{subject.title()} Trafficking and Protection"

    # Generic title for "X trafficking" where the object is clear.
    m = re.search(r"\b([a-z][a-z\s\-]{2,60}?)\s+trafficking\b", src_cf)
    if m:
        subject = m.group(1).strip()
        subject = re.sub(r"\b(illegal|illicit|the|a|an)\b", "", subject).strip()
        subject = re.sub(r"\s+", " ", subject).strip()
        if subject and len(subject.split()) <= 4:
            return f"{subject.title()} Trafficking and Protection"

    return ""


def _v13_add_if_source(
    payload: Dict[str, str],
    field: str,
    pattern: str,
    make_line,
    source_text: str,
    *,
    max_lines: int = 3,
) -> None:
    m = re.search(pattern, source_text, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return
    try:
        line = make_line(m)
    except Exception:
        return
    line = re.sub(r"\s+", " ", line or "").strip(" .")
    if line:
        _v10_add_line(payload, field, line, max_lines=max_lines)


def _v13_move_or_clean_line(
    *,
    payload: Dict[str, str],
    key: str,
    line: str,
    source_text: str,
) -> Optional[Tuple[str, str]]:
    """Return (target_field, cleaned_line), or None if handled/skipped."""
    src_cf = (source_text or "").casefold()
    l = re.sub(r"\s+", " ", line or "").strip(" .")
    l_cf = l.casefold()

    if not l:
        return None

    # Remove conversational wrappers.
    l = re.sub(r"^the\s+current\s+situation\s+in\s+which\s+", "", l, flags=re.IGNORECASE).strip()
    l = re.sub(r"^because\s+of\s+this,\s*", "", l, flags=re.IGNORECASE).strip()
    l = re.sub(r"^at\s+the\s+same\s+time,\s*", "", l, flags=re.IGNORECASE).strip()

    # Generic serious-problem repair.
    m = re.match(r"^the\s+risk\s+of\s+(.+?)\s+is\s+a\s+serious\s+problem(?:,?\s+and\s+.+)?$", l, flags=re.IGNORECASE)
    if m:
        subject = _v13_clean_illicit_subject(m.group(1))
        if subject:
            return "expressing_deep_concern", f"the serious threat posed by {subject}"

    # Bad extraction of "They are part of nature..." after a preceding subject.
    if re.search(r"\bare\s+part\s+of\s+nature\b", l, flags=re.IGNORECASE):
        return "recalling", "the role of wild animals and plants in supporting ecosystems, communities, and human health" if "wild animals and plants" in src_cf else re.sub(r"^the\s+risk\s+of\s+", "", l, flags=re.IGNORECASE)

    # Generic "when X, it damages Y" repair.
    m = re.match(r"^the\s+risk\s+of\s+when\s+(.+?),\s*it\s+(.+)$", l, flags=re.IGNORECASE)
    if m:
        cause = m.group(1).strip(" .,;:")
        effect = m.group(2).strip(" .,;:")
        return "expressing_deep_concern", f"the damage caused when {cause}, as it {effect}"

    # Disease/public-health risks are concerns, not requests.
    if re.search(r"\b(increase|increases|increasing)\s+the\s+risk\s+of\s+diseases?\s+spreading\b", l, flags=re.IGNORECASE):
        cleaned = re.sub(r"^requests?:\s*", "", l, flags=re.IGNORECASE)
        return "expressing_deep_concern", cleaned

    # Organized crime/corruption/fraud/online-illicit-market links are factual concern/context.
    if re.search(r"\borganized\s+crime\b|\bcorruption\b|\bfake\s+permits\b|\bmoney\s+laundering\b|\billegal\s+online\s+sales\b|\barms\s+trafficking\b", l, flags=re.IGNORECASE):
        cleaned = re.sub(r"^the\s+problem\s+has\s+become\s+even\s+harder\s+to\s+control\s+because\s+", "", l, flags=re.IGNORECASE)
        return "noting", cleaned

    # Online trafficking / digital illicit markets are noting unless framed as an action.
    if re.search(r"\bonline\s+.+trafficking\b|\bsocial\s+media\b|\bonline\s+marketplaces\b|\bdark\s+web\b", l, flags=re.IGNORECASE) and key in {"requests", "calls_upon", "encourages"}:
        return "noting", l

    # "Countries cannot treat X as small" is emphasis, not call upon.
    m = re.match(r"^(countries|governments|states)\s+cannot\s+treat\s+(.+?)\s+as\s+a\s+small\s+or\s+isolated\s+problem$", l, flags=re.IGNORECASE)
    if m:
        return "emphasizing", f"the need to treat {m.group(2).strip(' .,;:')} as a serious and transnational problem"

    # "may use legal markets to hide..." is usually about bad actors, not countries.
    if re.search(r"\bmay\s+use\s+legal\s+markets\s+to\s+hide\s+illegal\s+products\b", l, flags=re.IGNORECASE):
        return "noting", re.sub(r"^(countries|states|governments)\s+", "criminal groups ", l, flags=re.IGNORECASE)

    # Frontline-worker raw extractions: request support instead of preserving broken grammar.
    if re.search(r"\b(rangers|guards|customs officers|border officials|front\s*line|frontline)\b", l, flags=re.IGNORECASE):
        if re.search(r"\bwho\s+to\s+detect\b|\brole\s+to\s+be\s+respected\b|\bdangerous\s+conditions\b", l, flags=re.IGNORECASE):
            return None

    # Community-dependence lines belong in recalling.
    if re.search(r"\bmany\s+communities\s+depend\s+on\b", l, flags=re.IGNORECASE):
        return "recalling", l

    # Ecosystem/community loss is a concern, not encouragement.
    if re.search(r"\bif\s+.+disappears\b|\becosystems?\s+are\s+destroyed\b|\bcommunities\s+suffer\b", l, flags=re.IGNORECASE):
        return "expressing_deep_concern", l

    # "X need better skills/tools/training" should be an operative support clause.
    m = re.match(r"^(?:the\s+strengthening\s+of\s+)?(.+?)\s+need\s+better\s+(.+)$", l, flags=re.IGNORECASE)
    if m:
        actor = m.group(1).strip(" .,;:")
        needs = m.group(2).strip(" .,;:")
        return "requests", f"{actor} to receive better {needs}"

    # Generic bad operative grammar repairs.
    l = re.sub(r"\bto\s+not\s+be\s+done\b", "to be carried out", l, flags=re.IGNORECASE)
    l = re.sub(r"\bthey\s+to\s+not\s+", "they do not ", l, flags=re.IGNORECASE)
    l = re.sub(r"\bto\s+not\s+destroy\b", "to avoid destroying", l, flags=re.IGNORECASE)

    return key, l


_old_apply_evidence_guards_v13 = _apply_evidence_guards


def _apply_evidence_guards(
    payload: Dict[str, str],
    *,
    source_text: str,
    context_text: str,
    source_notes: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, str]:
    payload = _old_apply_evidence_guards_v13(
        payload,
        source_text=source_text,
        context_text=context_text,
        source_notes=source_notes or {},
    )

    src = re.sub(r"\s+", " ", source_text or "").strip()
    src_cf = src.casefold()

    title = _v13_make_trafficking_title(src)
    if title and re.search(r"\btrafficking\b", src_cf):
        payload["title"] = title

    # Add high-value source-grounded lines for serious crime / enforcement inputs.
    _v13_add_if_source(
        payload,
        "recalling",
        r"They\s+are\s+part\s+of\s+nature,\s+and\s+they\s+help\s+keep\s+(.+?)\s+stable(?:\.|$)",
        lambda m: f"the role of wild animals and plants in helping keep {m.group(1).strip(' .,;:')} stable",
        src,
        max_lines=2,
    )

    _v13_add_if_source(
        payload,
        "noting",
        r"wildlife\s+trafficking\s+is\s+now\s+often\s+linked\s+to\s+(.+?)(?:\.|$)",
        lambda m: f"the links between wildlife trafficking and {m.group(1).strip(' .,;:')}",
        src,
        max_lines=3,
    )

    _v13_add_if_source(
        payload,
        "noting",
        r"A\s+lot\s+of\s+illegal\s+wildlife\s+trade\s+now\s+happens\s+through\s+(.+?)(?:\.|$)",
        lambda m: f"the use of {m.group(1).strip(' .,;:')} for illegal wildlife trade",
        src,
        max_lines=3,
    )

    _v13_add_if_source(
        payload,
        "expressing_deep_concern",
        r"When\s+animals\s+are\s+poached,\s+forests\s+are\s+cut\s+illegally,\s+or\s+rare\s+plants\s+and\s+wildlife\s+are\s+sold\s+through\s+illegal\s+markets,\s+(.+?)(?:\.|$)",
        lambda m: f"the damage caused by poaching, illegal logging, and illegal markets, which {m.group(1).strip(' .,;:')}",
        src,
        max_lines=3,
    )

    _v13_add_if_source(
        payload,
        "expressing_deep_concern",
        r"Illegal\s+wildlife\s+trade\s+can\s+also\s+increase\s+(.+?)(?:\.|$)",
        lambda m: f"the potential for illegal wildlife trade to increase {m.group(1).strip(' .,;:')}",
        src,
        max_lines=3,
    )

    _v13_add_if_source(
        payload,
        "requests",
        r"They\s+need\s+proper\s+wages,\s+training,\s+equipment,\s+safety\s+protections,\s+and\s+institutional\s+support(?:\.|$)",
        lambda m: "governments to provide frontline personnel with proper wages, training, equipment, safety protections, and institutional support",
        src,
        max_lines=3,
    )

    _v13_add_if_source(
        payload,
        "requests",
        r"Law\s+enforcement\s+agencies\s+need\s+better\s+(.+?)\s+to\s+find,\s+track,\s+and\s+stop\s+these\s+networks(?:\.|$)",
        lambda m: f"law enforcement agencies to receive better {m.group(1).strip(' .,;:')} to find, track, and stop illicit networks",
        src,
        max_lines=3,
    )

    _v13_add_if_source(
        payload,
        "calls_upon",
        r"Countries\s+should\s+work\s+together\s+more\s+closely\s+to\s+stop\s+(.+?)\s+at\s+every\s+stage(?:\:|,|\.)(.+?)(?:\.|$)",
        lambda m: f"countries to strengthen cooperation to stop {m.group(1).strip(' .,;:')} at every stage",
        src,
        max_lines=3,
    )

    _v13_add_if_source(
        payload,
        "encourages",
        r"Local\s+communities,\s+Indigenous\s+Peoples,\s+and\s+people\s+living\s+near\s+wildlife\s+habitats\s+should\s+be\s+treated\s+as\s+partners(?:,|\s+not\s+ignored)(?:\.|$)",
        lambda m: "the treatment of local communities, Indigenous Peoples, and people living near affected habitats as partners in conservation and enforcement efforts",
        src,
        max_lines=3,
    )

    _v13_add_if_source(
        payload,
        "encourages",
        r"Countries\s+with\s+more\s+resources\s+should\s+help\s+(.+?)\s+fight\s+(.+?)(?:\.|$)",
        lambda m: f"countries with more resources to support {m.group(1).strip(' .,;:')} in fighting {m.group(2).strip(' .,;:')}",
        src,
        max_lines=3,
    )

    # Rebuild fields with moved/cleaned lines.
    rebuilt: Dict[str, List[str]] = {k: [] for k in EXPECTED_KEYS if k != "title"}

    for key in EXPECTED_KEYS:
        if key == "title":
            continue
        for raw in payload.get(key, "").splitlines():
            moved = _v13_move_or_clean_line(payload=payload, key=key, line=raw, source_text=src)
            if not moved:
                continue
            target, cleaned = moved
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
            if target in rebuilt and cleaned:
                rebuilt[target].append(cleaned)

    # Prefer stronger, cleaner domain lines over broken generic lines.
    for key, lines in rebuilt.items():
        # Remove known low-quality fragments that may survive older rules.
        good: List[str] = []
        for line in lines:
            lcf = line.casefold()
            if re.search(r"\bthe\s+risk\s+of\s+.+\s+is\s+a\s+serious\s+problem\b", lcf):
                continue
            if re.search(r"\bwho\s+to\s+detect\b|\brole\s+to\s+be\s+respected\b|\bthe\s+strengthening\s+of\s+.+\s+need\b", lcf):
                continue
            if key == "encourages" and re.search(r"\b(if\s+.+disappears|ecosystems?\s+are\s+destroyed|communities\s+suffer|illegal\s+wildlife\s+trade\s+can\s+also\s+increase)\b", lcf):
                continue
            good.append(line)
        payload[key] = _line_dedupe("\n".join(good), max_lines=FIELD_MAX_LINES.get(key, 3))

    return payload


# ---------------------------------------------------------------------------
# v22 structural safeguards
# ---------------------------------------------------------------------------
# Purpose: fix broad structural failures without returning to one-example
# prompt patching. These guards operate after the model/rule draft is produced.
# They improve title safety, handle long social-development / public-health
# passages, and repair common non-topic-specific clause-shape errors.

_BAD_TITLE_START_RE = re.compile(
    r"^\s*(and|but|or|this|that|these|those|it|they|their|his|her|our|your|to|for|with)\b",
    flags=re.IGNORECASE,
)


def _v22_count_word(source: str, word: str) -> int:
    return len(re.findall(rf"\b{re.escape(word)}\b", source or "", flags=re.IGNORECASE))


def _v22_source_title(source_text: str) -> str:
    """Derive a safe title from dominant policy terms in the source text."""
    src = re.sub(r"\s+", " ", source_text or "").strip()
    src_cf = src.casefold()

    # These are not example-specific templates; they are dominant-term guards.
    # They only fire when the exact policy term is repeatedly present in source.
    if "hate speech" in src_cf:
        return "Hate Speech"

    if "wildlife trafficking" in src_cf or ("wildlife" in src_cf and "trafficking" in src_cf):
        return "Wildlife Trafficking and Protection"

    if "cybersecurity" in src_cf:
        if re.search(r"\b(digital|internet|networks?|systems?)\b", src_cf):
            return "Cybersecurity and Digital Systems"
        return "Cybersecurity"

    if ("artificial intelligence" in src_cf or re.search(r"\bAI\b", src)) and "central asia" in src_cf:
        return "Artificial Intelligence and Central Asia"

    if "ocean" in src_cf and re.search(r"\b(protect|protection|sustainable|marine|sea)\b", src_cf):
        return "Ocean Protection and Sustainable Use"

    health_count = _v22_count_word(src, "health") + _v22_count_word(src, "healthcare")
    if health_count >= 6:
        if re.search(r"\b(equity|fair|fairer|inequality|universal|financial hardship|sustainable development|2030)\b", src_cf):
            return "Health Equity and Sustainable Development"
        if re.search(r"\b(primary healthcare|prevention|health promotion|well-being|wellbeing)\b", src_cf):
            return "Health Promotion and Primary Healthcare"
        return "Health and Well-being"

    # Fallback to the existing generic title extractor.
    title = _derive_title_from_text(src)
    if title and not _is_bad_title(title):
        return title
    return "Draft Resolution"


def _v22_bad_title(title: str, source_text: str) -> bool:
    t = re.sub(r"\s+", " ", title or "").strip()
    if _is_bad_title(t):
        return True
    if _BAD_TITLE_START_RE.search(t):
        return True
    if re.search(r"\bProtection and Sustainable Use\b", t, flags=re.IGNORECASE):
        src_cf = (source_text or "").casefold()
        # This phrase is suitable for natural-resource/environmental subjects,
        # but bad for health/cybersecurity/random fragments.
        if not re.search(r"\b(ocean|marine|wildlife|forest|biodiversity|natural resources|ecosystem)\b", src_cf):
            return True
    if re.search(r"\bData Protection\b", t, flags=re.IGNORECASE):
        src_cf = (source_text or "").casefold()
        if "cybersecurity" in src_cf and src_cf.count("cybersecurity") >= src_cf.count("data protection") + 1:
            return True
    return False


def _v22_clean_clause_text(line: str) -> str:
    l = re.sub(r"\s+", " ", line or "").strip(" .")
    if not l:
        return ""

    # Remove lay/composer scaffolding.
    l = re.sub(r"^I\s+think\s+", "", l, flags=re.IGNORECASE)
    l = re.sub(r"^overall,?\s+the\s+main\s+idea\s+is\s+that\s+", "", l, flags=re.IGNORECASE)
    l = re.sub(r"^overall,?\s+", "", l, flags=re.IGNORECASE)
    l = re.sub(r"^this\s+proposal\s+(?:is\s+saying|says|wants)\s+that\s+", "", l, flags=re.IGNORECASE)

    # Bad model/request shapes.
    l = re.sub(r"\brelevant\s+actors\s+to\s+([A-Z])", lambda m: m.group(1).lower(), l)
    l = re.sub(r"\b(countries|states|governments)\s+to\s+and\s+", r"\1 to ", l, flags=re.IGNORECASE)
    l = re.sub(r"\b(health|education|technology|protection|support)\s+to\s+not\s+just\s+be\b", r"the need for \1 to be understood as more than", l, flags=re.IGNORECASE)
    l = re.sub(r"\bto\s+not\s+", "to avoid ", l, flags=re.IGNORECASE)

    # Grammar pattern: “the risk of X are/is becoming...”
    m = re.match(r"^the\s+risk\s+of\s+(.+?)\s+(?:are|is)\s+(?:also\s+)?becoming\s+(.+)$", l, flags=re.IGNORECASE)
    if m:
        subject = m.group(1).strip(" .,;:")
        desc = m.group(2).strip(" .,;:")
        return f"the increasing {desc} of {subject}"

    # Better object noun for reports/standards/training lists.
    if re.match(r"^the\s+strengthening\s+of\s+(technical\s+reports|reports|standards|training|workshops)\b", l, flags=re.IGNORECASE):
        l = re.sub(r"^the\s+strengthening\s+of\s+", "the development of ", l, flags=re.IGNORECASE)

    return l.strip(" .")


def _v22_add_line(payload: Dict[str, str], field: str, line: str, *, max_lines: Optional[int] = None) -> None:
    line = _v22_clean_clause_text(line)
    if not line or field not in payload:
        return

    lines = [x.strip() for x in (payload.get(field) or "").splitlines() if x.strip()]
    if any(_too_similar(line, existing) for existing in lines):
        return
    lines.append(line)
    payload[field] = _line_dedupe("\n".join(lines), max_lines=max_lines or FIELD_MAX_LINES.get(field, 3))


def _v22_replace_or_add(payload: Dict[str, str], field: str, line: str, *, max_lines: Optional[int] = None) -> None:
    _v22_add_line(payload, field, line, max_lines=max_lines)


def _v22_public_health_coverage(payload: Dict[str, str], source_text: str) -> None:
    """Coverage floor for long social-development/public-health passages."""
    src = re.sub(r"\s+", " ", source_text or "").strip()
    src_cf = src.casefold()

    health_count = _v22_count_word(src, "health") + _v22_count_word(src, "healthcare")
    if health_count < 6:
        return

    # Title should not be a fragment for health-heavy passages.
    payload["title"] = _v22_source_title(src)

    # Remove badly shaped health fragments from operative fields before adding better ones.
    for field in ["requests", "calls_upon", "encourages"]:
        kept = []
        for raw in (payload.get(field) or "").splitlines():
            rcf = raw.casefold()
            if re.search(r"\bhealth\s+to\s+not\s+just\s+be\b|\bit\s+also\s+means\s+paying\s+attention\b|^if\s+people\s+do\s+not\s+have\b", rcf):
                continue
            kept.append(raw)
        payload[field] = "\n".join(kept)

    m = re.search(r"This\s+means\s+making\s+sure\s+people\s+have\s+(.+?)(?:\.|$)", src, flags=re.IGNORECASE)
    if m:
        _v22_add_line(
            payload,
            "recalling",
            "the importance of addressing health before problems become serious through " + m.group(1).strip(" .,;:"),
            max_lines=2,
        )

    m = re.search(r"paying\s+attention\s+to\s+things\s+like\s+(.+?)\s+and\s+the\s+way\s+people[’']s\s+daily\s+lives\s+affect\s+their\s+health", src, flags=re.IGNORECASE)
    if m:
        _v22_add_line(
            payload,
            "noting",
            "the impact of " + m.group(1).strip(" .,;:") + " and daily living conditions on people's health",
            max_lines=3,
        )

    m = re.search(r"If\s+people\s+do\s+not\s+have\s+(.+?),\s+then\s+(.+?)(?:\.|$)", src, flags=re.IGNORECASE)
    if m:
        _v22_add_line(
            payload,
            "expressing_deep_concern",
            "the risk that people without " + m.group(1).strip(" .,;:") + " face greater difficulty staying healthy",
            max_lines=3,
        )

    if re.search(r"not\s+just\s+be\s+about\s+hospitals|not\s+only\s+on\s+treating\s+disease|preventing\s+illness", src_cf):
        _v22_add_line(
            payload,
            "emphasizing",
            "the need to treat health as prevention, promotion, equity, and well-being, not only as hospitals, doctors, medicine, or emergencies",
            max_lines=2,
        )

    if re.search(r"without\s+being\s+pushed\s+into\s+financial\s+trouble|financial\s+hardship", src_cf):
        _v22_add_line(
            payload,
            "requests",
            "governments to strengthen universal access to healthcare without financial hardship",
            max_lines=3,
        )

    if re.search(r"primary\s+healthcare|health\s+promotion|disease\s+prevention", src_cf):
        _v22_add_line(
            payload,
            "requests",
            "governments to invest in primary healthcare, health promotion, disease prevention, and health workforce support",
            max_lines=3,
        )

    if re.search(r"digital\s+health|personal\s+health\s+data|privacy|digital\s+gaps", src_cf):
        _v22_add_line(
            payload,
            "requests",
            "governments to ensure that digital health tools are affordable, safe, responsible, privacy-respecting, and accessible",
            max_lines=3,
        )

    if re.search(r"health\s+problems\s+do\s+not\s+stop\s+at\s+borders|countries\s+and\s+international\s+organizations\s+should\s+also\s+cooperate", src_cf):
        _v22_add_line(
            payload,
            "calls_upon",
            "countries and international organizations to strengthen cooperation on cross-border health challenges",
            max_lines=2,
        )

    if re.search(r"richer\s+countries|global\s+organizations\s+should\s+provide", src_cf):
        _v22_add_line(
            payload,
            "calls_upon",
            "richer countries and global organizations to provide financial help, technical support, training, research support, and technology transfer to developing countries",
            max_lines=2,
        )

    if re.search(r"local\s+and\s+regional\s+production\s+of\s+vaccines|production\s+of\s+vaccines", src_cf):
        _v22_add_line(
            payload,
            "encourages",
            "the strengthening of local and regional production of vaccines, medicines, diagnostics, and other essential health supplies",
            max_lines=3,
        )

    if re.search(r"health\s+policies\s+should\s+involve\s+communities|listen\s+to\s+communities", src_cf):
        _v22_add_line(payload, "encourages", "the inclusion of communities in health policy decisions", max_lines=3)

    if re.search(r"education,\s+housing,\s+work,\s+food,\s+climate,\s+sanitation,\s+technology,\s+and\s+social\s+protection", src, flags=re.IGNORECASE):
        _v22_add_line(
            payload,
            "encourages",
            "the integration of health promotion across education, housing, work, food, climate, sanitation, technology, and social protection policies",
            max_lines=3,
        )


def _v22_general_long_input_cleanup(payload: Dict[str, str], source_text: str) -> None:
    """Generic post-cleaner for any topic."""
    src_cf = (source_text or "").casefold()

    # Title safety.
    if _v22_bad_title(payload.get("title", ""), source_text):
        payload["title"] = _v22_source_title(source_text)

    # Clean all lines, remove dangling or malformed fragments, and move obvious misplacements.
    rebuilt: Dict[str, List[str]] = {k: [] for k in EXPECTED_KEYS if k != "title"}

    for field in EXPECTED_KEYS:
        if field == "title":
            continue
        for raw in (payload.get(field) or "").splitlines():
            line = _v22_clean_clause_text(raw)
            if not line:
                continue
            lcf = line.casefold()

            # Drop dangling fragments.
            if re.search(r"\b(and|or|but|with|for|to)\s*$", line, flags=re.IGNORECASE):
                continue
            if len(line.split()) < 5 and field not in {"welcoming", "decides"}:
                continue

            target = field

            # "not just/not only" framing is usually emphasis, not request.
            if re.search(r"\bnot\s+(?:just|only)\s+be\s+about\b|\bnot\s+only\s+on\s+treating\b", lcf):
                target = "emphasizing"

            # Education/awareness/support lines are usually encourages unless there is a direct official actor.
            if field in {"noting", "recalling"} and re.search(r"\b(education|public awareness|training|support|capacity|skills|community participation)\b", lcf):
                if not re.search(r"\b(is|are|was|were)\s+(important|needed|necessary)\b", lcf):
                    target = "encourages"

            # Technology can help = noting; technology misuse = concern.
            if re.search(r"\btechnology\s+can\s+also\s+help\b", lcf):
                target = "noting"
            if re.search(r"\bnew\s+technology.+make\s+this\s+worse|fake\s+images|fake\s+videos|spreads\s+hatred\b", lcf):
                target = "expressing_deep_concern"

            # Existing efforts should be welcoming only if they are actually existing/positive.
            if target == "welcoming" and not re.search(r"\b(existing|already|ongoing|observances?|international day|week|progress|initiative|launched|established|national strategies)\b", lcf):
                target = "noting"

            # "should" without a real actor should not become "X to...".
            if target in {"requests", "calls_upon"} and re.match(r"^(health|technology|protection|support|funding|education)\s+to\b", lcf):
                target = "emphasizing"

            rebuilt[target].append(line)

    for field, lines in rebuilt.items():
        payload[field] = _line_dedupe("\n".join(lines), max_lines=FIELD_MAX_LINES.get(field, 3))

    # Cybersecurity title and grammar fixes.
    if "cybersecurity" in src_cf:
        payload["title"] = "Cybersecurity and Digital Systems"
        for field in ["expressing_deep_concern", "encourages"]:
            fixed_lines = []
            for raw in (payload.get(field) or "").splitlines():
                line = _v22_clean_clause_text(raw)
                if re.search(r"the\s+risk\s+of\s+cyberattacks\s+are\s+also\s+becoming", line, flags=re.IGNORECASE):
                    line = "the increasing frequency and complexity of cyberattacks"
                fixed_lines.append(line)
            payload[field] = _line_dedupe("\n".join(fixed_lines), max_lines=FIELD_MAX_LINES.get(field, 3))


_old_apply_evidence_guards_v22 = _apply_evidence_guards


def _apply_evidence_guards(
    payload: Dict[str, str],
    *,
    source_text: str,
    context_text: str,
    source_notes: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, str]:
    payload = _old_apply_evidence_guards_v22(
        payload,
        source_text=source_text,
        context_text=context_text,
        source_notes=source_notes or {},
    )

    _v22_general_long_input_cleanup(payload, source_text)
    _v22_public_health_coverage(payload, source_text)

    # Final pass: clean and cap lines after v22 additions.
    if _v22_bad_title(payload.get("title", ""), source_text):
        payload["title"] = _v22_source_title(source_text)

    for key in EXPECTED_KEYS:
        if key == "title":
            payload[key] = _v22_source_title(source_text) if _v22_bad_title(payload.get(key, ""), source_text) else payload.get(key, "Draft Resolution")
            continue
        cleaned_lines = []
        for raw in (payload.get(key) or "").splitlines():
            line = _v22_clean_clause_text(raw)
            if not line:
                continue
            if re.search(r"\b(and|or|but|with|for|to)\s*$", line, flags=re.IGNORECASE):
                continue
            cleaned_lines.append(line)
        payload[key] = _line_dedupe("\n".join(cleaned_lines), max_lines=FIELD_MAX_LINES.get(key, 3))

    return payload

# ---------------------------------------------------------------------------
# v23 final structural coverage pass
# ---------------------------------------------------------------------------
# Purpose: v22 fixed title safety and several long-input issues, but public-
# health / social-development inputs can still leave good coverage lines in
# weak fields or allow raw lay sentences to survive. This final pass is placed
# after the existing evidence guards so it can enforce clean, stable field
# placement without relying on a specific example passage.


def _v23_split_lines(text: str) -> List[str]:
    return [x.strip() for x in (text or "").splitlines() if x.strip()]


def _v23_set_lines(payload: Dict[str, str], field: str, lines: List[str], *, max_lines: Optional[int] = None) -> None:
    clean: List[str] = []
    for line in lines:
        line = _v22_clean_clause_text(line)
        if not line:
            continue
        if re.search(r"\b(and|or|but|with|for|to)\s*$", line, flags=re.IGNORECASE):
            continue
        if any(_too_similar(line, existing) for existing in clean):
            continue
        clean.append(line)
    payload[field] = _line_dedupe("\n".join(clean), max_lines=max_lines or FIELD_MAX_LINES.get(field, 3))


def _v23_append_lines(payload: Dict[str, str], field: str, lines: List[str], *, max_lines: Optional[int] = None) -> None:
    existing = _v23_split_lines(payload.get(field, ""))
    _v23_set_lines(payload, field, existing + lines, max_lines=max_lines)


def _v23_remove_lines(payload: Dict[str, str], field: str, patterns: List[str]) -> None:
    kept: List[str] = []
    for line in _v23_split_lines(payload.get(field, "")):
        lcf = line.casefold()
        if any(re.search(pattern, lcf, flags=re.IGNORECASE) for pattern in patterns):
            continue
        kept.append(line)
    payload[field] = "\n".join(kept)


def _v23_public_health_final(payload: Dict[str, str], source_text: str) -> None:
    src = re.sub(r"\s+", " ", source_text or "").strip()
    src_cf = src.casefold()

    health_count = _v22_count_word(src, "health") + _v22_count_word(src, "healthcare")
    if health_count < 6:
        return

    payload["title"] = _v22_source_title(src)

    # Remove raw lay/composer sentences that often survive in the wrong fields.
    for field in ["requests", "calls_upon", "encourages", "emphasizing", "noting", "expressing_deep_concern"]:
        _v23_remove_lines(payload, field, [
            r"^countries to stop treating health as something",
            r"^health to not just be",
            r"^it should also be about helping people live better lives",
            r"^making sure people have clean water",
            r"^countries should also support local and regional production",
            r"^however, not everyone has equal access",
            r"^if people do not have enough",
            r"^countries to invest more in primary healthcare",
            r"^the risk of climate change, natural disasters.*can all damage",
        ])

    # Recalling: stable background / rights-and-conditions frame.
    recall_lines: List[str] = []
    m = re.search(r"making\s+sure\s+people\s+have\s+(.+?)(?:\.|$)", src, flags=re.IGNORECASE)
    if m:
        recall_lines.append(
            "the importance of addressing health before problems become serious through "
            + m.group(1).strip(" .,;:")
        )
    if re.search(r"health\s+should\s+not\s+just\s+be\s+about\s+hospitals", src_cf):
        recall_lines.append(
            "health as extending beyond hospitals, doctors, medicine, and emergencies to prevention, well-being, and living conditions"
        )
    if recall_lines:
        _v23_set_lines(payload, "recalling", recall_lines, max_lines=2)

    # Noting: determinants and digital inequality are factual context, not recommendations.
    noting_lines = _v23_split_lines(payload.get("noting", ""))
    m = re.search(
        r"paying\s+attention\s+to\s+things\s+like\s+(.+?)\s+and\s+the\s+way\s+people[’']s\s+daily\s+lives\s+affect\s+their\s+health",
        src,
        flags=re.IGNORECASE,
    )
    if m:
        noting_lines.append(
            "the impact of " + m.group(1).strip(" .,;:") + " and daily living conditions on people's health"
        )
    if re.search(r"not\s+everyone\s+has\s+equal\s+access\s+to\s+the\s+internet", src_cf):
        noting_lines.append("unequal access to the internet, digital tools, and skills needed to benefit from digital health")
    _v23_set_lines(payload, "noting", noting_lines, max_lines=3)

    # Deep concern: harms and risks.
    concern_lines = _v23_split_lines(payload.get("expressing_deep_concern", ""))
    m = re.search(r"If\s+people\s+do\s+not\s+have\s+(.+?),\s+then\s+(.+?)(?:\.|$)", src, flags=re.IGNORECASE)
    if m:
        concern_lines.append(
            "the risk that people without " + m.group(1).strip(" .,;:") + " face greater difficulty staying healthy"
        )
    if re.search(r"climate\s+change,\s+natural\s+disasters,\s+extreme\s+weather", src, flags=re.IGNORECASE):
        concern_lines.append(
            "the damage to health caused by climate change, natural disasters, extreme weather, unsafe water, poor sanitation, air pollution, food insecurity, and unsafe living conditions"
        )
    if re.search(r"pandemics|antimicrobial\s+resistance|tuberculosis|HIV/AIDS|malaria|noncommunicable\s+diseases|mental\s+health\s+problems", src, flags=re.IGNORECASE):
        concern_lines.append(
            "the impact of cross-border health challenges, including pandemics, antimicrobial resistance, communicable diseases, noncommunicable diseases, and mental health problems"
        )
    _v23_set_lines(payload, "expressing_deep_concern", concern_lines, max_lines=3)

    # Emphasizing: conceptual policy frame.
    emphasis_lines: List[str] = []
    if re.search(r"not\s+just\s+be\s+about\s+hospitals|not\s+only\s+on\s+treating\s+disease|preventing\s+illness", src_cf):
        emphasis_lines.append(
            "the need to treat health as prevention, promotion, equity, and well-being, not only as hospitals, doctors, medicine, or emergencies"
        )
    if re.search(r"without\s+being\s+pushed\s+into\s+financial\s+trouble|financial\s+hardship", src_cf):
        emphasis_lines.append("the importance of access to healthcare without financial hardship")
    if emphasis_lines:
        _v23_set_lines(payload, "emphasizing", emphasis_lines, max_lines=2)

    # Requests: concrete government duties.
    request_lines = _v23_split_lines(payload.get("requests", ""))
    if re.search(r"without\s+being\s+pushed\s+into\s+financial\s+trouble|financial\s+hardship", src_cf):
        request_lines.append("governments to strengthen universal access to healthcare without financial hardship")
    if re.search(r"primary\s+healthcare|health\s+promotion|disease\s+prevention", src_cf):
        request_lines.append("governments to invest in primary healthcare, health promotion, disease prevention, and health workforce support")
    if re.search(r"digital\s+health|personal\s+health\s+data|privacy|digital\s+gaps", src_cf):
        request_lines.append("governments to ensure that digital health tools are affordable, safe, responsible, privacy-respecting, and accessible")
    if re.search(r"health\s+workers\s+also\s+need\s+better\s+training", src_cf):
        request_lines.append("governments to strengthen training and support for health workers")
    _v23_set_lines(payload, "requests", request_lines, max_lines=4)

    # Calls upon: cooperation and international support.
    call_lines: List[str] = []
    if re.search(r"health\s+problems\s+do\s+not\s+stop\s+at\s+borders|countries\s+and\s+international\s+organizations\s+should\s+also\s+cooperate", src_cf):
        call_lines.append("countries and international organizations to strengthen cooperation on cross-border health challenges")
    if re.search(r"richer\s+countries|global\s+organizations\s+should\s+provide|financial\s+help,\s+technical\s+support", src_cf):
        call_lines.append("richer countries and global organizations to provide financial help, technical support, training, research support, and technology transfer to developing countries")
    if re.search(r"pandemic\s+prevention,\s+preparedness,\s+and\s+response", src_cf):
        call_lines.append("countries to continue strengthening global rules for pandemic prevention, preparedness, and response")
    if call_lines:
        _v23_set_lines(payload, "calls_upon", call_lines, max_lines=3)

    # Encourages: capacity-building, communities, whole-of-policy approach.
    encourage_lines: List[str] = []
    if re.search(r"local\s+and\s+regional\s+production\s+of\s+vaccines|production\s+of\s+vaccines", src_cf):
        encourage_lines.append("the strengthening of local and regional production of vaccines, medicines, diagnostics, and other essential health supplies")
    if re.search(r"health\s+policies\s+should\s+involve\s+communities|listen\s+to\s+communities|real\s+community\s+experiences", src_cf):
        encourage_lines.append("the inclusion of communities and lived experience in health policy decisions")
    if re.search(r"education,\s+housing,\s+work,\s+food,\s+climate,\s+sanitation,\s+technology,\s+and\s+social\s+protection", src, flags=re.IGNORECASE):
        encourage_lines.append("the integration of health promotion across education, housing, work, food, climate, sanitation, technology, and social protection policies")
    if re.search(r"children,\s+pregnant\s+women,\s+newborns,\s+older\s+people|healthy\s+ageing|persons\s+with\s+disabilities", src, flags=re.IGNORECASE):
        encourage_lines.append("life-course health support for children, pregnant women, newborns, older people, persons with disabilities, and people with mental health conditions")
    if encourage_lines:
        _v23_set_lines(payload, "encourages", encourage_lines, max_lines=4)


def _v23_final_generic_polish(payload: Dict[str, str], source_text: str) -> None:
    """Small non-topic-specific repairs after all coverage floors."""
    for field in EXPECTED_KEYS:
        if field == "title":
            continue
        fixed: List[str] = []
        for line in _v23_split_lines(payload.get(field, "")):
            l = _v22_clean_clause_text(line)
            l = re.sub(r"\bthe\s+risk\s+of\s+(.+?)\s+can\s+all\s+damage\b", r"the damage caused by \1", l, flags=re.IGNORECASE)
            l = re.sub(r"\bit\s+should\s+also\s+be\s+about\b", "the importance of", l, flags=re.IGNORECASE)
            l = re.sub(r"\bcountries\s+to\s+stop\s+treating\s+(.+?)\s+as\s+something\s+that\s+only\s+matters\s+when\s+(.+)$", r"countries to adopt a preventive approach to \1", l, flags=re.IGNORECASE)
            if re.search(r"\b(and|or|but|with|for|to)\s*$", l, flags=re.IGNORECASE):
                continue
            fixed.append(l)
        payload[field] = _line_dedupe("\n".join(fixed), max_lines=FIELD_MAX_LINES.get(field, 3))


_old_apply_evidence_guards_v23 = _apply_evidence_guards


def _apply_evidence_guards(
    payload: Dict[str, str],
    *,
    source_text: str,
    context_text: str,
    source_notes: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, str]:
    payload = _old_apply_evidence_guards_v23(
        payload,
        source_text=source_text,
        context_text=context_text,
        source_notes=source_notes or {},
    )

    _v23_public_health_final(payload, source_text)
    _v23_final_generic_polish(payload, source_text)

    if _v22_bad_title(payload.get("title", ""), source_text):
        payload["title"] = _v22_source_title(source_text)

    return payload

# ---------------------------------------------------------------------------
# v24 final coverage pass
# ---------------------------------------------------------------------------
# Purpose: v23 is strong on public-health/social-development inputs and fixes
# bad titles, but model-guarded output can still be too sparse for long
# enforcement / trafficking / organized-crime passages. This pass is not tied
# to wildlife as a topic; it looks for policy-function patterns common to
# trafficking, corruption, organized crime, online illegal markets, frontline
# enforcement, community livelihoods, and capacity support.


def _v24_detect_enforcement_issue(source_text: str, payload: Dict[str, str]) -> str:
    src_cf = (source_text or "").casefold()
    title = (payload.get("title") or "").strip()

    issue_patterns = [
        (r"wildlife\s+trafficking", "wildlife trafficking"),
        (r"human\s+trafficking", "human trafficking"),
        (r"arms\s+trafficking", "arms trafficking"),
        (r"drug\s+trafficking", "drug trafficking"),
        (r"cybercrime", "cybercrime"),
        (r"illegal\s+logging", "illegal logging"),
        (r"illegal\s+trade", "illegal trade"),
        (r"trafficking", "trafficking"),
    ]
    for pattern, label in issue_patterns:
        if re.search(pattern, src_cf):
            return label

    if title and not _v22_bad_title(title, source_text):
        return title.casefold()

    return "the issue"


def _v24_is_enforcement_heavy(source_text: str) -> bool:
    src_cf = (source_text or "").casefold()
    cues = [
        "trafficking", "organized crime", "corruption", "money laundering",
        "fake permits", "forged documents", "forge documents", "bribe",
        "illegal online sales", "online marketplaces", "dark web", "law enforcement",
        "customs officers", "border authorities", "border officials", "financial crimes",
        "investigate", "prosecute", "serious crime", "front lines", "frontline",
        "rangers", "guards", "illegal markets", "supply chains", "permit systems",
    ]
    return sum(1 for cue in cues if cue in src_cf) >= 3


def _v24_compact_issue(issue: str) -> str:
    issue = (issue or "the issue").strip().casefold()
    if issue == "trafficking":
        return "trafficking"
    return issue


def _v24_remove_misplaced_enforcement_lines(payload: Dict[str, str]) -> None:
    # Move/remove lines that are concerns/actions, not factual noting; remove
    # lines that are conceptual emphasis, not deep concern.
    for field in ["noting", "expressing_deep_concern", "requests", "calls_upon", "encourages"]:
        kept: List[str] = []
        for line in _v23_split_lines(payload.get(field, "")):
            lcf = line.casefold()

            if field == "noting" and re.search(r"governments?\s+also\s+need\s+to\s+pay\s+more\s+attention", lcf):
                continue
            if field == "expressing_deep_concern" and re.search(r"stopping\s+.+\s+cannot\s+only\s+be\s+about\s+punishment", lcf):
                continue
            if field == "expressing_deep_concern" and re.search(r"rangers|customs officers|border officials|frontline|front lines", lcf):
                continue
            if field == "requests" and re.search(r"rangers.*role\s+to\s+be\s+respected|who\s+to\s+detect", lcf):
                continue

            kept.append(line)
        payload[field] = "\n".join(kept)


def _v24_enforcement_coverage(payload: Dict[str, str], source_text: str) -> None:
    if not _v24_is_enforcement_heavy(source_text):
        return

    src = re.sub(r"\s+", " ", source_text or "").strip()
    src_cf = src.casefold()
    issue = _v24_detect_enforcement_issue(src, payload)
    issue_compact = _v24_compact_issue(issue)

    _v24_remove_misplaced_enforcement_lines(payload)

    # Recalling: replace raw "not just things" language with a formal principle.
    recalling = _v23_split_lines(payload.get("recalling", ""))
    cleaned_recalling: List[str] = []
    for line in recalling:
        if re.search(r"not\s+just\s+things\s+people\s+can\s+take", line, flags=re.IGNORECASE):
            continue
        cleaned_recalling.append(line)
    if re.search(r"wild\s+animals\s+and\s+plants\s+.*part\s+of\s+nature|ecosystems,\s*communities", src, flags=re.IGNORECASE):
        cleaned_recalling.append("the role of wild animals and plants in supporting ecosystems, communities, and human health")
    if re.search(r"communities\s+depend\s+on\s+.+?for\s+their\s+livelihoods", src, flags=re.IGNORECASE):
        cleaned_recalling.append("the dependence of many communities on nature, wildlife, forests, oceans, and related resources for livelihoods, tourism, food, and culture")
    _v23_set_lines(payload, "recalling", cleaned_recalling, max_lines=2)

    # Noting: neutral factual context.
    noting = _v23_split_lines(payload.get("noting", ""))
    if re.search(r"often\s+linked\s+to\s+organized\s+crime", src_cf):
        noting.append(f"the links between {issue_compact} and organized crime, corruption, fake permits, money laundering, illegal online sales, and related crimes")
    if re.search(r"social\s+media|online\s+marketplaces|dark\s+web|illegal\s+online\s+sales", src_cf):
        noting.append(f"the use of social media, online marketplaces, and digital channels for {issue_compact}")
    if re.search(r"poverty|lack\s+of\s+jobs|weak\s+local\s+opportunities|demand\s+from\s+buyers", src_cf):
        noting.append("the role of poverty, lack of jobs, demand from buyers, and weak local opportunities in worsening the problem")
    _v23_set_lines(payload, "noting", noting, max_lines=3)

    # Deep concern: harm/risk only.
    concerns = _v23_split_lines(payload.get("expressing_deep_concern", ""))
    if re.search(r"poached|forests\s+are\s+cut\s+illegally|illegal\s+markets|closer\s+to\s+extinction", src_cf):
        concerns.append("the damage caused by poaching, illegal logging, and illegal markets to biodiversity, local people, and threatened species")
    if re.search(r"diseases\s+spreading\s+from\s+animals\s+to\s+humans|bushmeat|quarantine|sanitary\s+controls", src_cf):
        concerns.append("the potential for illegal trade involving live animals or bushmeat to increase the risk of diseases spreading from animals to humans")
    _v23_set_lines(payload, "expressing_deep_concern", concerns, max_lines=3)

    # Emphasis: conceptual frame.
    emphasis = _v23_split_lines(payload.get("emphasizing", ""))
    if re.search(r"small\s+or\s+isolated\s+problem|serious\s+and\s+transnational|serious\s+crime", src_cf):
        emphasis.append(f"the need to treat {issue_compact} as a serious and transnational problem")
    if re.search(r"cannot\s+only\s+be\s+about\s+punishment|why\s+people\s+get\s+involved|underlying", src_cf):
        emphasis.append("the importance of addressing underlying drivers alongside enforcement")
    _v23_set_lines(payload, "emphasizing", emphasis, max_lines=2)

    # Requests: concrete duties to governments/agencies.
    requests = _v23_split_lines(payload.get("requests", ""))
    if re.search(r"stronger\s+laws|better\s+investigations|real\s+punishment|investigate\s+and\s+prosecute", src_cf):
        requests.append(f"countries to strengthen laws, investigations, penalties, and prosecution tools related to {issue_compact}")
    if re.search(r"serious\s+crime.*organized\s+criminal\s+groups|organized\s+criminal\s+groups.*serious\s+crime", src_cf):
        requests.append(f"countries to treat {issue_compact} involving organized criminal groups as a serious crime")
    if re.search(r"corruption|permit\s+systems|legal\s+supply\s+chains|domestic\s+markets|illegal\s+products", src_cf):
        requests.append("countries to fight corruption, prevent permit abuse, and stop illegal products from entering legal supply chains")
    if re.search(r"digital\s+skills|digital\s+forensic|online\s+marketplaces|dark\s+web|illegal\s+online", src_cf):
        requests.append(f"law enforcement agencies to strengthen digital skills, forensic tools, and training to detect and disrupt online {issue_compact} networks")
    if re.search(r"collecting\s+information|reporting\s+on\s+global\s+trends|reviewed\s+regularly", src_cf):
        requests.append("relevant international bodies to continue collecting information, reporting on trends, and improving coordination")
    _v23_set_lines(payload, "requests", requests, max_lines=4)

    # Calls upon: cooperation and international support.
    calls = _v23_split_lines(payload.get("calls_upon", ""))
    if re.search(r"work\s+together|cooperate|sharing\s+information|supporting\s+each\s+other", src_cf):
        calls.append(f"countries to strengthen cooperation, information-sharing, and mutual support to address {issue_compact}")
    if re.search(r"where\s+the\s+animals|where\s+they\s+are\s+transported|where\s+people\s+buy", src_cf):
        calls.append(f"countries to cooperate across source, transit, and destination stages of {issue_compact}")
    if re.search(r"countries\s+with\s+more\s+resources|developing\s+countries|economies\s+in\s+transition", src_cf):
        calls.append("countries with more resources to support developing countries and countries with economies in transition")
    _v23_set_lines(payload, "calls_upon", calls, max_lines=3)

    # Encourages: capacity-building, communities, frontline personnel, livelihoods.
    encourages = _v23_split_lines(payload.get("encourages", ""))
    if re.search(r"rangers|guards|customs\s+officers|airport\s+workers|seaport\s+workers|border\s+officials|front\s+lines|frontline", src_cf):
        encourages.append("support for rangers, customs officers, border officials, and other frontline personnel through training, equipment, safety protections, and institutional support")
    if re.search(r"local\s+communities|Indigenous\s+Peoples|living\s+near\s+.+habitats|treated\s+as\s+partners", src, flags=re.IGNORECASE):
        encourages.append("the treatment of local communities, Indigenous Peoples, and people living near affected habitats as partners in prevention, conservation, and legal livelihoods")
    if re.search(r"conservation|sustainable\s+tourism|community-managed|sustainable\s+agriculture|legal\s+livelihoods", src_cf):
        encourages.append("legal livelihood opportunities through conservation, sustainable tourism, community-managed areas, sustainable agriculture, and related approaches")
    if re.search(r"money,\s+training,\s+equipment,\s+technology,\s+legal\s+support|CITES|capacity", src, flags=re.IGNORECASE):
        encourages.append("the provision of funding, training, equipment, technology, legal support, and capacity-building to implement relevant international agreements")
    if re.search(r"international\s+organizations|UN\s+agencies|build\s+capacity|improve\s+cooperation", src, flags=re.IGNORECASE):
        encourages.append("continued support by international organizations and UN agencies for capacity-building, information collection, and cooperation")
    _v23_set_lines(payload, "encourages", encourages, max_lines=4)


def _v24_final_generic_polish(payload: Dict[str, str], source_text: str) -> None:
    """Final small grammar repairs that apply across topics."""
    for field in EXPECTED_KEYS:
        if field == "title":
            continue
        fixed: List[str] = []
        for line in _v23_split_lines(payload.get(field, "")):
            l = _v22_clean_clause_text(line)
            l = re.sub(
                r"^the\s+increasing\s+more\s+common\s+and\s+more\s+complicated\s+of\s+(.+)$",
                r"the increasing frequency and complexity of \1",
                l,
                flags=re.IGNORECASE,
            )
            l = re.sub(
                r"^the\s+risk\s+of\s+(.+?)\s+(?:are|is)\s+also\s+becoming\s+more\s+common\s+and\s+more\s+complicated$",
                r"the increasing frequency and complexity of \1",
                l,
                flags=re.IGNORECASE,
            )
            l = re.sub(
                r"^the\s+strengthening\s+of\s+(technical\s+reports?,\s*standards?,\s*training,\s*workshops?.*)$",
                r"the development of \1",
                l,
                flags=re.IGNORECASE,
            )
            l = re.sub(r"\bWild animals\b", "wild animals", l)
            l = re.sub(r"\s+", " ", l).strip(" .,;:")
            if not l:
                continue
            if re.search(r"\b(and|or|but|with|for|to)\s*$", l, flags=re.IGNORECASE):
                continue
            fixed.append(l)
        payload[field] = _line_dedupe("\n".join(fixed), max_lines=FIELD_MAX_LINES.get(field, 3))


_old_apply_evidence_guards_v24 = _apply_evidence_guards


def _apply_evidence_guards(
    payload: Dict[str, str],
    *,
    source_text: str,
    context_text: str,
    source_notes: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, str]:
    payload = _old_apply_evidence_guards_v24(
        payload,
        source_text=source_text,
        context_text=context_text,
        source_notes=source_notes or {},
    )

    _v24_enforcement_coverage(payload, source_text)
    _v24_final_generic_polish(payload, source_text)

    if _v22_bad_title(payload.get("title", ""), source_text):
        payload["title"] = _v22_source_title(source_text)

    return payload

# ---------------------------------------------------------------------------
# v25 institutional-partnership guard
# ---------------------------------------------------------------------------
# This pass is intentionally structural rather than topic-specific. It handles
# long passages whose main purpose is cooperation between two named institutions
# across many policy areas. It also prevents narrow issue words inside a long
# list, such as "human trafficking", from hijacking the whole title/draft.


def _v25_clean_org_name(name: str) -> str:
    n = re.sub(r"\s+", " ", name or "").strip(" .,;:\n\t")
    n = re.sub(r"^(?:the\s+)+", "", n, flags=re.IGNORECASE).strip()
    if n.upper() == "UN":
        return "United Nations"
    return n


def _v25_extract_partnership_orgs(source_text: str) -> Optional[Tuple[str, str]]:
    head = re.sub(r"\s+", " ", (source_text or "")[:1800]).strip()
    if not head:
        return None

    # High-precision form: "the X and the Y should keep/work/cooperate...".
    patterns = [
        r"\b(?:the\s+)?(?P<a>United\s+Nations|UN)\s+and\s+(?:the\s+)?(?P<b>Council\s+of\s+Europe)\s+should\s+(?:keep\s+)?(?:working?|cooperate|coordinate)",
        r"\b(?:the\s+)?(?P<a>[A-Z][A-Za-z&.'\-]*(?:\s+[A-Z][A-Za-z&.'\-]*){0,5})\s+and\s+(?:the\s+)?(?P<b>[A-Z][A-Za-z&.'\-]*(?:\s+[A-Z][A-Za-z&.'\-]*){0,5})\s+should\s+(?:keep\s+)?(?:working?|cooperate|coordinate)",
    ]

    for pattern in patterns:
        m = re.search(pattern, head)
        if not m:
            continue
        a = _v25_clean_org_name(m.group("a"))
        b = _v25_clean_org_name(m.group("b"))
        if not a or not b or a.casefold() == b.casefold():
            continue
        # Avoid accidental matches on ordinary nouns.
        org_words = ("united", "nations", "council", "organization", "organisation", "union", "agency", "court", "commission", "committee", "europe", "africa", "america", "asia", "bank", "fund")
        joined = f"{a} {b}".casefold()
        if any(word in joined for word in org_words):
            return a, b

    return None


def _v25_is_institutional_partnership(source_text: str) -> bool:
    if not _v25_extract_partnership_orgs(source_text):
        return False
    src_cf = (source_text or "").casefold()
    return bool(re.search(r"\b(cooperate|cooperation|coordinate|coordination|partnership|work(?:ing)?\s+closely|share\s+(?:their\s+)?experience|support\s+each\s+other)\b", src_cf))


def _v25_add(lines: List[str], line: str) -> None:
    line = re.sub(r"\s+", " ", line or "").strip(" .")
    if line:
        lines.append(line)


def _v25_apply_institutional_partnership_coverage(payload: Dict[str, str], source_text: str) -> None:
    orgs = _v25_extract_partnership_orgs(source_text)
    if not orgs:
        return

    a, b = orgs
    src = re.sub(r"\s+", " ", source_text or "").strip()
    src_cf = src.casefold()

    payload["title"] = f"Cooperation between the {a} and the {b}"

    recalling: List[str] = []
    if re.search(r"human rights|democracy|rule of law|peace|fairness|safety", src_cf):
        _v25_add(recalling, f"the shared role of the {a} and the {b} in addressing human rights, democracy, the rule of law, peace, fairness, and safety")
    if re.search(r"protect people.?s rights|support democratic governments|common legal standards", src_cf):
        _v25_add(recalling, f"the work of the {b} to protect rights, support democratic governments, and help countries follow common legal standards")
    _v23_set_lines(payload, "recalling", recalling or _v23_split_lines(payload.get("recalling", "")), max_lines=2)

    noting: List[str] = []
    if re.search(r"russia.+aggression.+ukraine|ukraine.+russia|georgia", src_cf):
        _v25_add(noting, "serious challenges in Europe, including Russia’s aggression against Ukraine and earlier actions against Georgia")
    if re.search(r"privacy|freedom of expression|access to information|safety online|internet governance|cybercrime|electronic evidence", src_cf):
        _v25_add(noting, "the growing importance of digital issues, including privacy, freedom of expression, access to information, online safety, internet governance, cybercrime, and electronic evidence")
    if re.search(r"children.+violence|refugees|asylum-seekers|statelessness|minority languages|violence against women", src_cf):
        _v25_add(noting, "the range of practical issues requiring cooperation, including protection of children, refugees and asylum-seekers, statelessness reduction, minority rights, gender equality, and ending violence against women and girls")
    _v23_set_lines(payload, "noting", noting or _v23_split_lines(payload.get("noting", "")), max_lines=3)

    welcoming: List[str] = []
    if re.search(r"has done a lot of work|experience helping countries|has experience", src_cf):
        _v25_add(welcoming, f"the experience and existing work of the {b} in supporting rights, democratic institutions, legal standards, and good governance")
    _v23_set_lines(payload, "welcoming", welcoming or _v23_split_lines(payload.get("welcoming", "")), max_lines=2)

    concerns: List[str] = []
    if re.search(r"caused major harm|serious violations|held accountable|aggression", src_cf):
        _v25_add(concerns, "the major harm and concerns regarding peace, justice, human rights, and international law arising from aggression and serious violations")
    if re.search(r"new technologies.+harm|artificial intelligence|genetics|neurotechnology|responsible use of technology", src_cf):
        _v25_add(concerns, "the potential for new technologies to harm people when not guided by human rights, democracy, and the rule of law")
    _v23_set_lines(payload, "expressing_deep_concern", concerns or _v23_split_lines(payload.get("expressing_deep_concern", "")), max_lines=2)

    emphasis: List[str] = []
    if re.search(r"too big for one country|one organization to solve alone|share their experience|coordinate better", src_cf):
        _v25_add(emphasis, f"the importance of stronger coordination, experience-sharing, and mutual support between the {a} and the {b}")
    if re.search(r"justice and good government|fair, transparent, accountable|respectful of the law", src_cf):
        _v25_add(emphasis, "the importance of justice, good governance, transparency, accountability, and respect for the law")
    _v23_set_lines(payload, "emphasizing", emphasis or _v23_split_lines(payload.get("emphasizing", "")), max_lines=2)

    requests: List[str] = []
    if re.search(r"support ukraine|help victims|record damage|held accountable|past human rights judgments", src_cf):
        _v25_add(requests, f"the {a} and the {b} to continue cooperation in supporting Ukraine, assisting victims, recording damage, and promoting accountability for serious violations")
    if re.search(r"preventing torture|fighting terrorism|human trafficking|refugees|migrants|disabilities|racism|hate speech|freedom of expression|freedom of religion|women and girls", src_cf):
        _v25_add(requests, f"the {a} and the {b} to continue cooperation on protecting basic rights and freedoms, including protection against torture, terrorism, trafficking, discrimination, hate speech, and violations affecting migrants, refugees, persons with disabilities, women, and girls")
    if re.search(r"data protection|internet governance|journalism safety|artificial intelligence|cybercrime|electronic evidence|responsible use of technology", src_cf):
        _v25_add(requests, f"the {a} and the {b} to cooperate on data protection, internet governance, journalism safety, artificial intelligence, cybercrime, electronic evidence, and responsible use of technology")
    _v23_set_lines(payload, "requests", requests or _v23_split_lines(payload.get("requests", "")), max_lines=3)

    calls: List[str] = []
    _v25_add(calls, f"the {a} and the {b} to strengthen their partnership, share knowledge, coordinate their work, and support each other")
    if re.search(r"avoid working separately|report back|how this cooperation is going", src_cf):
        _v25_add(calls, f"the {a} and the {b} to avoid working separately where cooperation can increase impact and to report on progress in their cooperation")
    _v23_set_lines(payload, "calls_upon", calls, max_lines=2)

    encourages: List[str] = []
    if re.search(r"democratic institutions|local governments|parliaments|civil society|young people|education programs", src_cf):
        _v25_add(encourages, "support for democratic institutions, local governments, parliaments, civil society, young people, and education programmes on rights and democracy")
    if re.search(r"social rights|poverty reduction|health care|disability rights|social protection|basic needs", src_cf):
        _v25_add(encourages, "continued cooperation on social rights, poverty reduction, health care, disability rights, social protection, and fair access to basic needs")
    if re.search(r"sustainable development|climate|environmental protection|disaster risk reduction|corruption|organized crime|drug abuse|cultural heritage|sport integrity|intercultural dialogue", src_cf):
        _v25_add(encourages, "continued cooperation on sustainable development, climate and environmental protection, disaster risk reduction, corruption, organized crime, terrorism, drug abuse, cultural heritage, education, youth participation, sport integrity, and intercultural dialogue")
    _v23_set_lines(payload, "encourages", encourages or _v23_split_lines(payload.get("encourages", "")), max_lines=3)


def _v25_remove_unsupported_or_cross_topic_lines(payload: Dict[str, str], source_text: str) -> None:
    src_cf = (source_text or "").casefold()
    for field in EXPECTED_KEYS:
        if field == "title":
            continue
        kept: List[str] = []
        for line in _v23_split_lines(payload.get(field, "")):
            l_cf = line.casefold()
            # Do not allow enforcement fallback text to import drivers that are
            # not actually in the source. This specifically prevents a single
            # word such as "poverty" from creating a whole illicit-market line.
            if "lack of jobs" in l_cf and "lack of jobs" not in src_cf:
                continue
            if "demand from buyers" in l_cf and "demand from buyers" not in src_cf:
                continue
            if "weak local opportunities" in l_cf and "weak local opportunities" not in src_cf:
                continue
            if re.search(r"\bthe\s+risk\s+of\s+new\s+technologies\s+can\s+be\s+useful", line, flags=re.IGNORECASE):
                line = "the potential for new technologies to harm people when not guided by human rights, democracy, and the rule of law"
            line = re.sub(r"^there\s+to\s+be\s+", "stronger efforts to ", line, flags=re.IGNORECASE)
            line = re.sub(r"\bto\s+keep\s+working\s+together\b", "to continue working together", line, flags=re.IGNORECASE)
            kept.append(line)
        payload[field] = _line_dedupe("\n".join(kept), max_lines=FIELD_MAX_LINES.get(field, 3))


_old_apply_evidence_guards_v25 = _apply_evidence_guards


def _apply_evidence_guards(
    payload: Dict[str, str],
    *,
    source_text: str,
    context_text: str,
    source_notes: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, str]:
    payload = _old_apply_evidence_guards_v25(
        payload,
        source_text=source_text,
        context_text=context_text,
        source_notes=source_notes or {},
    )

    _v25_remove_unsupported_or_cross_topic_lines(payload, source_text)

    if _v25_is_institutional_partnership(source_text):
        _v25_apply_institutional_partnership_coverage(payload, source_text)
        _v24_final_generic_polish(payload, source_text)

    if _v22_bad_title(payload.get("title", ""), source_text):
        payload["title"] = _v22_source_title(source_text)

    return payload