# app/services/draft_ai.py
import json
import os
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.services.local_llm import get_chat_client
from pathlib import Path

from dotenv import load_dotenv

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


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"Model did not return JSON. Raw output: {text[:800]}")

    return json.loads(m.group(0))


def _normalize_value(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, list):
        return "\n".join(str(x).strip() for x in value if str(x).strip())

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}

    for key in EXPECTED_KEYS:
        out[key] = _normalize_value(payload.get(key, ""))

    if not out.get("title"):
        out["title"] = "Draft Resolution"

    return out


def _contains_any(text: str, cues: List[str]) -> bool:
    t = (text or "").casefold()
    return any(cue.casefold() in t for cue in cues)


def _line_dedupe(text: str) -> str:
    lines = []
    seen = set()

    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip(" -•\t\r\n")
        if not line:
            continue

        key = line.casefold()
        if key in seen:
            continue

        seen.add(key)
        lines.append(line)

    return "\n".join(lines)


def _strip_unsourced_parenthetical_acronyms(text: str, source: str) -> str:
    """
    Generic guard:
    If the model creates something like "(ITU)" but the acronym never appears in
    the source/context, remove only the parenthetical acronym.
    """
    source_upper = (source or "").upper()

    def repl(match: re.Match) -> str:
        acronym = match.group(1)
        if acronym and acronym.upper() not in source_upper:
            return ""
        return match.group(0)

    return re.sub(r"\s*\(([A-Z]{2,10})\)", repl, text)


def _de_specific_request_actors(text: str, source: str) -> str:
    """
    Generic guard:
    If a request line begins with a very specific capitalized institution that
    does not appear in the source, replace it with a generic actor.
    This avoids hallucinations like naming a specific institution not provided
    by the user.
    """
    source_cf = (source or "").casefold()
    safe_prefixes = (
        "relevant ",
        "member states",
        "states",
        "countries",
        "international organizations",
        "experts",
        "stakeholders",
        "technical experts",
        "relevant bodies",
        "relevant organizations",
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

        # Pattern: "The Specific Named Institution to ..."
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


def _apply_evidence_guards(payload: Dict[str, str], *, source_text: str, context_text: str) -> Dict[str, str]:
    """
    These guards are intentionally generic.
    They do not depend on cybersecurity, data governance, or any specific example.
    They only stop common over-inference:
    - fake welcoming
    - fake regret
    - fake deep concern
    - fake decides
    - named institution hallucinations
    """

    evidence = f"{context_text}\n\n{source_text}"

    welcoming_cues = [
        "welcomes",
        "welcomed",
        "welcoming",
        "commends",
        "commended",
        "appreciates",
        "appreciated",
        "acknowledges with appreciation",
        "progress achieved",
        "positive development",
        "successful initiative",
    ]

    regret_cues = [
        "regret",
        "regrets",
        "regretting",
        "failure",
        "failed",
        "lack of progress",
        "insufficient progress",
        "missed commitment",
        "missed commitments",
        "not fulfilled",
        "shortfall",
    ]

    deep_concern_cues = [
        "deep concern",
        "grave concern",
        "serious concern",
        "serious problem",
        "serious threat",
        "grave threat",
        "major risk",
        "grave risk",
        "widespread risk",
        "widespread damage",
        "critical risk",
        "harm",
        "damage",
        "attack",
        "attacks",
        "crisis",
        "instability",
        "severe",
        "alarming",
    ]

    decision_cues = [
        "decides",
        "decide",
        "resolved",
        "resolves",
        "determines",
        "shall establish",
        "shall create",
        "shall adopt",
        "this body decides",
        "the committee decides",
        "the assembly decides",
    ]

    if payload.get("welcoming") and not _contains_any(evidence, welcoming_cues):
        payload["welcoming"] = ""

    if payload.get("expressing_regret") and not _contains_any(evidence, regret_cues):
        payload["expressing_regret"] = ""

    if payload.get("expressing_deep_concern") and not _contains_any(evidence, deep_concern_cues):
        payload["expressing_deep_concern"] = ""

    if payload.get("decides") and not _contains_any(evidence, decision_cues):
        payload["decides"] = ""

    # Generic anti-hallucination for named actors.
    for key in ["requests", "calls_upon", "encourages"]:
        payload[key] = _strip_unsourced_parenthetical_acronyms(payload.get(key, ""), evidence)

    payload["requests"] = _de_specific_request_actors(payload.get("requests", ""), evidence)

    # Dedupe and normalize all multi-line fields.
    for key in EXPECTED_KEYS:
        payload[key] = _line_dedupe(payload.get(key, ""))

    if not payload.get("title"):
        payload["title"] = "Draft Resolution"

    return payload


def _validate(payload: Dict[str, Any], *, source_text: str, context_text: str) -> DraftFill:
    normalized = _normalize_payload(payload)
    guarded = _apply_evidence_guards(
        normalized,
        source_text=source_text,
        context_text=context_text,
    )

    if hasattr(DraftFill, "model_validate"):
        return DraftFill.model_validate(guarded)

    return DraftFill.parse_obj(guarded)


def generate_draft_from_paragraphs(
    *,
    plain_text: str,
    agenda_title: Optional[str] = None,
    room_title: Optional[str] = None,
) -> DraftFill:
    client = get_chat_client()

    context_bits = []

    if agenda_title:
        context_bits.append(f"Agenda title: {agenda_title}")

    if room_title:
        context_bits.append(
            "Discussion room title, for context only. Do not copy it as the draft title unless it is clearly a real policy title: "
            f"{room_title}"
        )

    context_text = "\n".join(context_bits).strip()

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

    resp = client.chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        max_tokens=int(os.getenv("DRAFT_AI_MAX_TOKENS", "650")),
        timeout_s=float(os.getenv("DRAFT_AI_TIMEOUT_S", "240")),
        response_format="json",
    )

    content = (resp.choices[0].message.content or "").strip()
    payload = _extract_json_object(content)

    return _validate(
        payload,
        source_text=plain_text,
        context_text=context_text,
    )