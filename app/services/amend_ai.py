# app/services/amend_ai.py
import json
import os
import re
from typing import Any, Dict, List, Literal, Optional

from app.services.local_llm import get_chat_client
from pydantic import BaseModel, Field

from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=True)


Action = Literal["REMOVE", "ADD", "REPLACE"]
ClauseType = Literal["preambular", "operative"]


class AmendOp(BaseModel):
    action: Action
    clause_type: ClauseType
    target: str = ""
    content: str = ""


class AmendGen(BaseModel):
    label: str = ""
    operations: List[AmendOp] = Field(default_factory=list)


ACTION_MAP = {
    "REMOVE": "REMOVE",
    "DELETE": "REMOVE",
    "OMIT": "REMOVE",
    "STRIKE": "REMOVE",
    "ADD": "ADD",
    "INSERT": "ADD",
    "APPEND": "ADD",
    "REPLACE": "REPLACE",
    "SUBSTITUTE": "REPLACE",
    "AMEND": "REPLACE",
    "CLARIFY": "REPLACE",
    "MODIFY": "REPLACE",
}


EXPECTED_ACTIONS = {"REMOVE", "ADD", "REPLACE"}
EXPECTED_CLAUSE_TYPES = {"preambular", "operative"}

def _normalize_amend_text(text: str) -> str:
    text = (text or "").strip()
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("‘", "'").replace("’", "'")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    """
    Generic cleanup only.
    Does not depend on any topic.
    - normalizes quote style
    - fixes awkward ADD targets
    - avoids exact duplicate operations
    - limits excessive operations
    """
    cleaned: List[AmendOp] = []
    seen = set()

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _normalize_amend_text(op.target)
        content = _normalize_amend_text(op.content)

        if action == "ADD":
            if not content:
                continue

            if not target:
                target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"

            target = re.sub(r"^as a new\b", "as new", target, flags=re.I)

            # If model says only "after clause starting with", make the target more explicit.
            if target.lower().startswith("after clause starting with"):
                target = f"after {clause_type} clause starting with" + target[len("after clause starting with"):]

        elif action == "REPLACE":
            if not target or not content:
                continue

            if target.lower().startswith("clause starting with"):
                target = f"{clause_type} " + target

        elif action == "REMOVE":
            if not target:
                continue
            content = ""

            if target.lower().startswith("clause starting with"):
                target = f"{clause_type} " + target

        key = (
            action.casefold(),
            clause_type.casefold(),
            target.casefold(),
            content.casefold(),
        )

        if key in seen:
            continue

        seen.add(key)
        cleaned.append(
            AmendOp(
                action=action,
                clause_type=clause_type,
                target=target,
                content=content,
            )
        )

    return AmendGen(
        label=_normalize_amend_text(gen.label) or "Generated amendment",
        operations=cleaned[:8],
    )


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError(f"Model did not return JSON. Raw output: {text[:800]}")
        obj = json.loads(m.group(0))

    if not isinstance(obj, dict):
        raise ValueError("Top-level amendment output must be a JSON object.")

    return obj


def _as_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip()
    if isinstance(x, list):
        return "; ".join(str(i).strip() for i in x if str(i).strip())
    if isinstance(x, dict):
        return json.dumps(x, ensure_ascii=False)
    return str(x).strip()


def _infer_clause_type(text: str) -> str:
    t = (text or "").casefold()

    if any(x in t for x in [
        "recalling", "noting", "welcoming", "expressing regret",
        "expressing deep concern", "emphasizing", "preambular"
    ]):
        return "preambular"

    return "operative"


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    label = _as_str(payload.get("label", ""))

    ops_raw = payload.get("operations", [])
    if isinstance(ops_raw, str):
        try:
            ops_raw = json.loads(ops_raw)
        except Exception:
            ops_raw = []
    if isinstance(ops_raw, dict):
        ops_raw = [ops_raw]
    if not isinstance(ops_raw, list):
        ops_raw = []

    norm_ops: List[Dict[str, str]] = []

    for op in ops_raw:
        if not isinstance(op, dict):
            continue

        action_raw = _as_str(op.get("action", "")).upper()
        action = ACTION_MAP.get(action_raw, action_raw)

        target = _as_str(op.get("target", ""))
        content = _as_str(op.get("content", ""))

        clause_raw = _as_str(op.get("clause_type", "")).lower()
        if "preamb" in clause_raw:
            clause_type = "preambular"
        elif "oper" in clause_raw:
            clause_type = "operative"
        elif target or content:
            clause_type = _infer_clause_type(f"{target} {content}")
        else:
            clause_type = ""

        if action not in EXPECTED_ACTIONS:
            continue

        if clause_type not in EXPECTED_CLAUSE_TYPES:
            continue

        if action == "ADD":
            if not target:
                target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"
            if not content:
                continue

        if action == "REPLACE":
            if not target or not content:
                continue

        if action == "REMOVE":
            if not target:
                continue
            content = ""

        norm_ops.append({
            "action": action,
            "clause_type": clause_type,
            "target": target,
            "content": content,
        })

    return {
        "label": label,
        "operations": norm_ops,
    }


def _validate(payload: Dict[str, Any]) -> AmendGen:
    payload = _normalize_payload(payload)

    if hasattr(AmendGen, "model_validate"):
        return AmendGen.model_validate(payload)

    return AmendGen.parse_obj(payload)


def _split_amend_intents(text: str) -> List[str]:
    """
    Lightweight generic splitting.
    This only helps the model see separate amendment intentions.
    """
    t = " ".join((text or "").split())
    if not t:
        return []

    chunks = re.split(r"\s*;\s*|\n+", t)
    out: List[str] = []

    for c in chunks:
        subs = re.split(
            r"\s+(?=(?:also|while also|and also)\s+(?:add|adding|replace|replacing|remove|removing|delete|deleting)\b)",
            c,
            flags=re.I,
        )

        for s in subs:
            s = s.strip().lstrip(",").strip()
            s = re.sub(r"^(and|also)\s+", "", s, flags=re.I)
            if s:
                out.append(s)

    return out


def generate_amend_ops_from_paragraphs(
    *,
    plain_text: str,
    draft_symbol: Optional[str] = None,
    draft_title: Optional[str] = None,
    agenda_label: Optional[str] = None,
    draft_text: Optional[str] = None,
) -> AmendGen:
    client = get_chat_client()

    ctx: List[str] = []

    if agenda_label:
        ctx.append(f"Agenda item: {agenda_label}")
    if draft_symbol:
        ctx.append(f"Draft/resolution symbol: {draft_symbol}")
    if draft_title:
        ctx.append(f"Draft title: {draft_title}")
    if draft_text:
        ctx.append("Existing draft text for targeting:\n" + draft_text)

    context = "\n\n".join(ctx).strip()

    intents = _split_amend_intents(plain_text)
    intent_block = "\n".join(
        f"{i + 1}. {x}" for i, x in enumerate(intents)
    ) if intents else (plain_text or "").strip()

    system = """
You convert plain-language amendment requests into formal amendment operations.

You are evidence-grounded:
- Use only the user's amendment request and the existing draft text.
- Do not invent clause numbers.
- Do not invent existing draft wording.
- Do not omit major requested changes.
- It is better to create several clear operations than one vague operation.

Return complete valid JSON only.
No markdown.
No explanation.
""".strip()

    user = f"""
{context}

Return exactly this JSON object shape:

{{
  "label": "",
  "operations": [
    {{
      "action": "ADD",
      "clause_type": "operative",
      "target": "as new operative clause",
      "content": ""
    }}
  ]
}}

Allowed values:
- action: "REMOVE", "ADD", "REPLACE"
- clause_type: "preambular", "operative"

Meaning of actions:
- ADD: insert a new clause or phrase.
- REMOVE: delete an existing clause, phrase, or wording.
- REPLACE: substitute existing wording with revised wording.

General field rules:
- label: short human-readable label for the amendment.
- target:
  - For ADD: use "as new operative clause", "as new preambular clause", or "after operative/preambular clause starting with ...".
  - For REPLACE or REMOVE: use exact clause number only if given by the user or visible in the existing draft text.
  - If no clause number exists, use "operative clause starting with ..." or "preambular clause starting with ...".
  - If the user asks to soften or remove a type of framing but no exact wording is visible, use a descriptive target such as "wording that frames the issue too narrowly".
- content:
  - Required for ADD and REPLACE.
  - Empty string for REMOVE.

Coverage rules:
- Preserve each distinct amendment request from the user.
- If the user says they want to "add" a point, usually create an ADD operation.
- If the user says they want to "replace", "expand", "revise", or "change" an existing idea, create a REPLACE operation when a matching existing clause is visible.
- If the user says they want to "remove" or "soften" wording, create REMOVE or REPLACE.
- Do not collapse several unrelated themes into one operation.
- Do not repeat the exact same target for many ADD operations. If several new points are being added, prefer separate "as new operative clause" targets.
- Do not make future additions look like existing draft text.
- Do not create more than 8 operations.

Quality rules:
- Each operation must be atomic: one operation = one change.
- Keep each content field concise but specific.
- Use formal amendment language.
- Do not include procedural commentary.
- Do not mention the user, the model, or the drafting process.
- Finish the JSON object completely.

User amendment request, split into intents:
{intent_block}
""".strip()

    resp = client.chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        max_tokens=int(os.getenv("AMEND_AI_MAX_TOKENS", "950")),
        timeout_s=float(os.getenv("AMEND_AI_TIMEOUT_S", "240")),
        response_format="json",
    )

    content = (resp.choices[0].message.content or "").strip()
    payload = _extract_json_object(content)
    gen = _validate(payload)

    return _postprocess_amend_gen(gen)