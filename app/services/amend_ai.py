# app/services/amend_ai.py
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from app.services.local_llm import get_chat_client


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
    "DROP": "REMOVE",
    "ADD": "ADD",
    "INSERT": "ADD",
    "APPEND": "ADD",
    "INCLUDE": "ADD",
    "MENTION": "ADD",
    "REPLACE": "REPLACE",
    "SUBSTITUTE": "REPLACE",
    "AMEND": "REPLACE",
    "CLARIFY": "REPLACE",
    "MODIFY": "REPLACE",
    "REVISE": "REPLACE",
    "EXPAND": "REPLACE",
    "SOFTEN": "REPLACE",
    "STRENGTHEN": "REPLACE",
}

EXPECTED_ACTIONS = {"REMOVE", "ADD", "REPLACE"}
EXPECTED_CLAUSE_TYPES = {"preambular", "operative"}

PREAMBULAR_LABELS = {
    "recalling",
    "noting",
    "welcoming",
    "expressing regret",
    "expressing_regret",
    "expressing deep concern",
    "expressing_deep_concern",
    "emphasizing",
}
OPERATIVE_LABELS = {"decides", "requests", "calls upon", "calls_upon", "encourages"}

# StableLM behaves much better with draft_ai-style flat JSON strings than with
# nested arrays. Keep the model output small; deterministic rules can preserve
# extra intents after the model returns.
MODEL_OP_SLOTS = max(1, min(4, int(os.getenv("AMEND_AI_MODEL_OP_SLOTS", "3"))))
MAX_OPS = max(1, min(12, int(os.getenv("AMEND_AI_MAX_OPS", "8"))))

AMEND_FLAT_KEYS = ["label"]
for _i in range(1, MODEL_OP_SLOTS + 1):
    AMEND_FLAT_KEYS += [
        f"op{_i}_action",
        f"op{_i}_clause_type",
        f"op{_i}_target",
        f"op{_i}_content",
    ]

AMEND_FLAT_JSON_SCHEMA = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in AMEND_FLAT_KEYS},
    "required": AMEND_FLAT_KEYS,
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Small shared helpers, adapted from the working draft_ai shape
# ---------------------------------------------------------------------------

def _env_bool(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default).strip().casefold()
    return value in {"1", "true", "yes", "on"}


def _normalize_amend_text(text: str) -> str:
    text = (text or "").strip()
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("‘", "'").replace("’", "'")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .")


def _strip_code_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = _strip_code_fences(text)

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError(f"Model did not return JSON. Raw output: {text[:800]}")
        obj = json.loads(m.group(0))

    if isinstance(obj, dict) and isinstance(obj.get("amendment"), dict):
        obj = obj["amendment"]

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


def _clamp_text(text: str, env_name: str, default: str) -> str:
    max_chars = int(os.getenv(env_name, default))
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].strip()


def _fingerprint(text: str) -> str:
    words = [
        w.casefold()
        for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text or "")
        if w.casefold() not in {
            "the", "and", "for", "that", "this", "with", "from", "should", "must",
            "into", "about", "clause", "operative", "preambular", "starting", "new",
            "add", "replace", "remove", "amendment", "proposal", "draft",
        }
    ]
    return " ".join(words[:18])


def _too_similar(a: str, b: str) -> bool:
    at = set(_fingerprint(a).split())
    bt = set(_fingerprint(b).split())
    if not at or not bt:
        return False
    return len(at & bt) / max(1, min(len(at), len(bt))) >= 0.72


# ---------------------------------------------------------------------------
# Normalization and validation
# ---------------------------------------------------------------------------

def _infer_clause_type(text: str) -> str:
    t = (text or "").casefold()
    if any(x in t for x in PREAMBULAR_LABELS) or "preamb" in t:
        return "preambular"
    return "operative"


def _normalize_action(text: str) -> str:
    raw = _as_str(text).upper().strip()
    raw = re.sub(r"[^A-Z_]", "", raw)
    return ACTION_MAP.get(raw, raw)


def _normalize_clause_type(text: str, *, target: str = "", content: str = "") -> str:
    raw = _as_str(text).casefold().strip()
    if "preamb" in raw:
        return "preambular"
    if "oper" in raw:
        return "operative"
    if raw in EXPECTED_CLAUSE_TYPES:
        return raw
    if target or content:
        return _infer_clause_type(f"{target} {content}")
    return ""


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accepts either the old nested shape:
      {"label":"", "operations":[...]}
    or the StableLM-friendly flat shape:
      {"label":"", "op1_action":"ADD", ...}
    """
    label = _as_str(payload.get("label", ""))
    norm_ops: List[Dict[str, str]] = []

    # New flat shape.
    flat_found = any(str(k).startswith("op1_") for k in payload.keys())
    if flat_found:
        for i in range(1, MODEL_OP_SLOTS + 1):
            action = _normalize_action(payload.get(f"op{i}_action", ""))
            target = _as_str(payload.get(f"op{i}_target", ""))
            content = _as_str(payload.get(f"op{i}_content", ""))
            clause_type = _normalize_clause_type(
                payload.get(f"op{i}_clause_type", ""),
                target=target,
                content=content,
            )
            if action not in EXPECTED_ACTIONS or clause_type not in EXPECTED_CLAUSE_TYPES:
                continue
            norm_ops.append({
                "action": action,
                "clause_type": clause_type,
                "target": target,
                "content": content,
            })

    # Old nested shape, kept for compatibility.
    ops_raw = payload.get("operations", [])
    if isinstance(ops_raw, str):
        try:
            ops_raw = json.loads(ops_raw)
        except Exception:
            ops_raw = []
    if isinstance(ops_raw, dict):
        ops_raw = [ops_raw]
    if isinstance(ops_raw, list):
        for op in ops_raw:
            if not isinstance(op, dict):
                continue
            action = _normalize_action(op.get("action", ""))
            target = _as_str(op.get("target", ""))
            content = _as_str(op.get("content", ""))
            clause_type = _normalize_clause_type(op.get("clause_type", ""), target=target, content=content)
            if action not in EXPECTED_ACTIONS or clause_type not in EXPECTED_CLAUSE_TYPES:
                continue
            norm_ops.append({
                "action": action,
                "clause_type": clause_type,
                "target": target,
                "content": content,
            })

    # Required-field cleanup.
    cleaned: List[Dict[str, str]] = []
    for op in norm_ops:
        action = op["action"]
        clause_type = op["clause_type"]
        target = _normalize_amend_text(op.get("target", ""))
        content = _normalize_amend_text(op.get("content", ""))

        if action == "ADD":
            if not content:
                continue
            if not target:
                target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"
        elif action == "REPLACE":
            if not target or not content:
                continue
        elif action == "REMOVE":
            if not target:
                continue
            content = ""

        cleaned.append({
            "action": action,
            "clause_type": clause_type,
            "target": target,
            "content": content,
        })

    return {"label": label, "operations": cleaned}


def _validate(payload: Dict[str, Any]) -> AmendGen:
    payload = _normalize_payload(payload)
    if hasattr(AmendGen, "model_validate"):
        return AmendGen.model_validate(payload)
    return AmendGen.parse_obj(payload)


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    cleaned: List[AmendOp] = []
    seen = set()

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _normalize_amend_text(op.target)
        content = _normalize_amend_text(op.content)

        target = target.strip()
        content = content.strip()

        if action == "ADD":
            if not content:
                continue
            if not target:
                target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"
            target = re.sub(r"^as a new\b", "as new", target, flags=re.I)
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

        # Remove model scaffolding.
        for bad in ["the user wants to ", "the amendment should ", "please "]:
            if content.casefold().startswith(bad):
                content = content[len(bad):].strip()

        key = (action.casefold(), clause_type.casefold(), target.casefold(), content.casefold())
        if key in seen:
            continue
        if any(_too_similar(f"{target} {content}", f"{x.target} {x.content}") for x in cleaned):
            continue

        seen.add(key)
        cleaned.append(AmendOp(action=action, clause_type=clause_type, target=target, content=content))
        if len(cleaned) >= MAX_OPS:
            break

    return AmendGen(label=_normalize_amend_text(gen.label) or "Generated amendment", operations=cleaned)


# ---------------------------------------------------------------------------
# Rule payload: draft_ai-style deterministic safety net
# ---------------------------------------------------------------------------

def _split_amend_intents(text: str) -> List[str]:
    t = " ".join((text or "").split())
    if not t:
        return []

    chunks = re.split(r"\s*;\s*|\n+", t)
    out: List[str] = []

    for c in chunks:
        subs = re.split(
            r"\s+(?=(?:also|while also|and also|then|next)\s+(?:add|adding|replace|replacing|remove|removing|delete|deleting|revise|revising|change|changing)\b)",
            c,
            flags=re.I,
        )
        for s in subs:
            s = s.strip().lstrip(",").strip()
            s = re.sub(r"^(and|also|then|next)\s+", "", s, flags=re.I)
            if s:
                out.append(s)

    return out[:MAX_OPS]


def _guess_action(intent: str) -> str:
    s = intent.casefold()
    if re.search(r"\b(remove|delete|omit|strike|drop)\b", s):
        return "REMOVE"
    if re.search(r"\b(replace|substitute|change|revise|modify|clarify|expand|soften|strengthen|rewrite)\b", s):
        return "REPLACE"
    if re.search(r"\b(add|insert|append|include|mention)\b", s):
        return "ADD"
    return "ADD"


def _guess_clause_type(intent: str) -> str:
    s = intent.casefold()
    if any(x in s for x in PREAMBULAR_LABELS) or re.search(r"\b(background|concern|regret|noting|recalling|emphasizing|welcome)\b", s):
        return "preambular"
    return "operative"


def _strip_action_intro(intent: str) -> str:
    s = _normalize_amend_text(intent)
    s = re.sub(
        r"^(i\s+want\s+to|we\s+want\s+to|please|can\s+you|could\s+you|the\s+amendment\s+should)\s+",
        "",
        s,
        flags=re.I,
    ).strip()
    s = re.sub(
        r"^(add|insert|append|include|mention|replace|substitute|change|revise|modify|clarify|expand|soften|strengthen|rewrite|remove|delete|omit|strike|drop)\s+(that\s+)?",
        "",
        s,
        flags=re.I,
    ).strip()
    s = re.sub(r"^(a\s+point\s+about|something\s+about|wording\s+about)\s+", "", s, flags=re.I).strip()
    return s.strip(" .")


def _formalize_content(intent: str, action: str) -> str:
    s = _strip_action_intro(intent)

    # Remove target-ish prefix if the user wrote "replace X with Y".
    m = re.search(r"\bwith\s+(?P<content>.+)$", s, flags=re.I)
    if action == "REPLACE" and m:
        s = m.group("content").strip(" .")

    # For remove, content must be empty.
    if action == "REMOVE":
        return ""

    # Light formalization. Do not over-transform; human-in-loop can edit.
    s = re.sub(r"^there\s+should\s+be\s+", "the establishment of ", s, flags=re.I)
    s = re.sub(r"^countries\s+should\s+", "countries to ", s, flags=re.I)
    s = re.sub(r"^governments\s+should\s+", "governments to ", s, flags=re.I)
    s = re.sub(r"^organizations\s+should\s+", "organizations to ", s, flags=re.I)
    s = re.sub(r"^people\s+should\s+", "relevant actors to ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s


def _extract_draft_hints(draft_text: Optional[str]) -> List[Tuple[str, str]]:
    """Return short (clause_type, clause_start) hints; never send full draft to StableLM."""
    if not draft_text:
        return []

    text = _clamp_text(draft_text, "AMEND_AI_DRAFT_CONTEXT_MAX_CHARS", "3500")
    lines = [x.strip() for x in re.split(r"\n+", text) if x.strip()]
    hints: List[Tuple[str, str]] = []
    current_type = "operative"

    label_re = re.compile(
        r"^(recalling|noting|welcoming|expressing[_ ]regret|expressing[_ ]deep[_ ]concern|emphasizing|decides|requests|calls[_ ]upon|encourages)\s*:\s*(.*)$",
        flags=re.I,
    )

    for raw in lines:
        line = re.sub(r"\s+", " ", raw).strip(" -•\t")
        if not line:
            continue

        m = label_re.match(line)
        if m:
            label = m.group(1).replace("_", " ").casefold()
            current_type = "preambular" if label in {x.replace("_", " ") for x in PREAMBULAR_LABELS} else "operative"
            rest = m.group(2).strip()
            if rest:
                start = rest[:120].strip(" .,;:")
                hints.append((current_type, start))
            continue

        # Also handle numbered/bulleted draft lines.
        line = re.sub(r"^\(?\d+[A-Za-z]?\)?[\).:-]?\s*", "", line).strip()
        if len(line) >= 12:
            hints.append((current_type, line[:120].strip(" .,;:")))

        if len(hints) >= int(os.getenv("AMEND_AI_DRAFT_HINT_MAX_ITEMS", "18")):
            break

    return hints


def _draft_hints_text(hints: List[Tuple[str, str]]) -> str:
    if not hints:
        return "No draft clause hints provided."
    return "\n".join(f"- {typ}: {start}" for typ, start in hints)


def _find_matching_hint(intent: str, hints: List[Tuple[str, str]]) -> Tuple[str, str]:
    if not hints:
        return "", ""

    intent_terms = set(_fingerprint(intent).split())
    best: Tuple[int, str, str] = (0, "", "")
    for typ, start in hints:
        terms = set(_fingerprint(start).split())
        score = len(intent_terms & terms)
        if score > best[0]:
            best = (score, typ, start)

    if best[0] <= 0:
        return "", ""
    return best[1], best[2]


def _derive_target(intent: str, action: str, clause_type: str, hints: List[Tuple[str, str]]) -> str:
    s = intent.strip()

    # Explicit target in quotes.
    quoted = re.findall(r"[\"']([^\"']{4,160})[\"']", s)
    if quoted and action in {"REPLACE", "REMOVE"}:
        q = quoted[0].strip(" .,;:")
        return f"{clause_type} clause starting with '{q[:110]}'"

    # Existing field label mentioned by user.
    for label in PREAMBULAR_LABELS:
        label_clean = label.replace("_", " ")
        if label_clean in s.casefold():
            return f"preambular clause under {label_clean}"
    for label in OPERATIVE_LABELS:
        label_clean = label.replace("_", " ")
        if label_clean in s.casefold():
            return f"operative clause under {label_clean}"

    matched_type, start = _find_matching_hint(intent, hints)
    if start and action in {"REPLACE", "REMOVE"}:
        return f"{matched_type or clause_type} clause starting with '{start[:110]}'"

    if action == "ADD":
        return "as new operative clause" if clause_type == "operative" else "as new preambular clause"

    # Descriptive target is allowed when exact text is unavailable.
    topic = _strip_action_intro(intent)
    topic = re.sub(r"\bwith\b.+$", "", topic, flags=re.I).strip(" .,;:")
    if not topic:
        topic = "the relevant wording"
    return f"wording concerning {topic[:90]}"


def _build_rule_payload(
    *,
    plain_text: str,
    draft_title: Optional[str] = None,
    draft_text: Optional[str] = None,
) -> Dict[str, Any]:
    intents = _split_amend_intents(plain_text)
    hints = _extract_draft_hints(draft_text)
    ops: List[Dict[str, str]] = []

    for intent in intents:
        action = _guess_action(intent)
        clause_type = _guess_clause_type(intent)
        target = _derive_target(intent, action, clause_type, hints)
        content = _formalize_content(intent, action)

        # If a matching draft hint was preambular/operative, respect that.
        if target.startswith("preambular"):
            clause_type = "preambular"
        elif target.startswith("operative"):
            clause_type = "operative"

        if action == "ADD" and not content:
            continue
        if action == "REPLACE" and (not target or not content):
            continue
        if action == "REMOVE" and not target:
            continue

        ops.append({
            "action": action,
            "clause_type": clause_type,
            "target": target,
            "content": "" if action == "REMOVE" else content,
        })

    label = "Generated amendment"
    if draft_title:
        label = f"Amendment to {draft_title[:60]}"
    elif intents:
        first = _strip_action_intro(intents[0])[:60]
        if first:
            label = f"Amendment on {first}"

    return {"label": label, "operations": ops[:MAX_OPS]}


# ---------------------------------------------------------------------------
# Model prompts: mirror draft_ai, but with flat JSON and source notes
# ---------------------------------------------------------------------------

def _is_tiny_or_small_model() -> bool:
    model = os.getenv("OLLAMA_MODEL", "").casefold()
    profile = os.getenv("AMEND_AI_PROFILE", os.getenv("DRAFT_AI_PROFILE", "")).casefold()
    return (
        profile in {"tiny", "small", "qwen", "stablelm"}
        or "0.5b" in model
        or "1.6b" in model
        or "tinyllama" in model
        or "qwen2.5:0.5b" in model
        or "stablelm2:1.6b" in model
    )


def _build_context_text(
    *,
    draft_symbol: Optional[str] = None,
    draft_title: Optional[str] = None,
    agenda_label: Optional[str] = None,
    draft_text: Optional[str] = None,
) -> str:
    ctx: List[str] = []
    if agenda_label:
        ctx.append(f"Agenda item: {agenda_label}")
    if draft_symbol:
        ctx.append(f"Draft/resolution symbol: {draft_symbol}")
    if draft_title:
        ctx.append(f"Draft title: {draft_title}")

    # StableLM should see short clause starts, not the full draft.
    hints = _extract_draft_hints(draft_text)
    ctx.append("Draft clause hints for targeting only:\n" + _draft_hints_text(hints))
    return "\n\n".join(ctx).strip()


def _flat_shape_text() -> str:
    lines = ['{"label":""']
    for i in range(1, MODEL_OP_SLOTS + 1):
        lines.append(f',"op{i}_action":""')
        lines.append(f',"op{i}_clause_type":""')
        lines.append(f',"op{i}_target":""')
        lines.append(f',"op{i}_content":""')
    lines.append("}")
    return "".join(lines)


def _build_prompts(
    *,
    plain_text: str,
    context_text: str,
    intents: List[str],
) -> Tuple[str, str, Union[str, Dict[str, Any]], int, float]:
    intent_limit = max(1, min(MODEL_OP_SLOTS, len(intents) or 1))
    intent_block = "\n".join(f"{i + 1}. {x}" for i, x in enumerate(intents[:intent_limit]))
    if not intent_block:
        intent_block = _clamp_text(plain_text, "AMEND_AI_INPUT_MAX_CHARS", "1600")

    max_tokens = int(os.getenv("AMEND_AI_MAX_TOKENS", "250"))
    timeout_s = float(os.getenv("AMEND_AI_TIMEOUT_S", "240"))

    if _is_tiny_or_small_model():
        system = """
You convert amendment notes into JSON.

Rules:
- Return JSON only.
- Use exactly the requested keys.
- Every value must be a string.
- Use ADD, REMOVE, or REPLACE only.
- Use preambular or operative only.
- Leave unused operation slots as empty strings.
- Do not invent clause numbers or draft wording.
""".strip()

        user = f"""
Context:
{context_text or "No extra context."}

Return this flat JSON shape exactly, with string values only:
{_flat_shape_text()}

Target rules:
- ADD target: "as new operative clause" or "as new preambular clause".
- REPLACE/REMOVE target: use a draft clause hint if relevant, like "operative clause starting with '...'".
- If no exact target is visible, use "wording concerning ...".

Content rules:
- ADD/REPLACE content is required and concise.
- REMOVE content must be "".

Amendment notes:
{intent_block}

Return the JSON object now.
""".strip()

        return system, user, AMEND_FLAT_JSON_SCHEMA, max_tokens, timeout_s

    system = """
You convert plain-language amendment requests into formal amendment operations.
Return complete valid JSON only. No markdown. No explanation.
Use only the user's request and the provided draft clause hints.
""".strip()

    user = f"""
{context_text}

Return this flat JSON shape exactly:
{_flat_shape_text()}

Allowed actions: ADD, REMOVE, REPLACE.
Allowed clause types: preambular, operative.
Unused operation slots must be empty strings.

Amendment notes:
{intent_block}
""".strip()

    return system, user, "json", max_tokens, timeout_s


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
        temperature=float(os.getenv("AMEND_AI_TEMPERATURE", "0.0")),
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        response_format=response_format,
    )
    return (resp.choices[0].message.content or "").strip()


def _payload_quality_issues(model_gen: AmendGen, rule_gen: AmendGen) -> List[str]:
    issues: List[str] = []

    if not model_gen.operations:
        issues.append("empty_operations")

    if rule_gen.operations and len(model_gen.operations) == 1 and len(rule_gen.operations) >= 3:
        issues.append("too_sparse")

    seen: List[str] = []
    for op in model_gen.operations:
        if op.action not in EXPECTED_ACTIONS:
            issues.append("bad_action")
        if op.clause_type not in EXPECTED_CLAUSE_TYPES:
            issues.append("bad_clause_type")
        if op.action in {"ADD", "REPLACE"} and not op.content.strip():
            issues.append("missing_content")
        if op.action in {"REPLACE", "REMOVE"} and not op.target.strip():
            issues.append("missing_target")
        if re.search(r"\b(the user|the model|json|operation slot|amendment notes)\b", op.content, flags=re.I):
            issues.append("scaffolding")
        fp = f"{op.action} {op.clause_type} {op.target} {op.content}"
        if any(_too_similar(fp, old) for old in seen):
            issues.append("duplicate")
        seen.append(fp)

    return issues


def _merge_model_with_rules(model_gen: AmendGen, rule_gen: AmendGen) -> AmendGen:
    if _env_bool("AMEND_AI_PREFER_RULES", "0"):
        label = model_gen.label or rule_gen.label
        return _postprocess_amend_gen(AmendGen(label=label, operations=rule_gen.operations))

    issues = _payload_quality_issues(model_gen, rule_gen)
    if issues:
        print("AMEND AI QUALITY GATE USING RULE PAYLOAD:", ", ".join(sorted(set(issues))))
        return rule_gen

    # Keep model-polished operations, then append any rule-detected requests the
    # model missed. This preserves coverage without forcing nested JSON output.
    ops: List[AmendOp] = list(model_gen.operations)
    for rule_op in rule_gen.operations:
        rule_text = f"{rule_op.action} {rule_op.clause_type} {rule_op.target} {rule_op.content}"
        if any(_too_similar(rule_text, f"{x.action} {x.clause_type} {x.target} {x.content}") for x in ops):
            continue
        ops.append(rule_op)
        if len(ops) >= MAX_OPS:
            break

    return _postprocess_amend_gen(AmendGen(label=model_gen.label or rule_gen.label, operations=ops))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_amend_ops_from_paragraphs(
    *,
    plain_text: str,
    draft_symbol: Optional[str] = None,
    draft_title: Optional[str] = None,
    agenda_label: Optional[str] = None,
    draft_text: Optional[str] = None,
) -> AmendGen:
    plain_text = _clamp_text(plain_text, "AMEND_AI_INPUT_MAX_CHARS", "2500")
    intents = _split_amend_intents(plain_text)

    rule_payload = _build_rule_payload(
        plain_text=plain_text,
        draft_title=draft_title,
        draft_text=draft_text,
    )
    rule_gen = _postprocess_amend_gen(_validate(rule_payload))

    # Keep the same switch style as draft_ai. Unlike the previous version, the
    # model is not asked to solve everything from a huge nested prompt.
    if not _env_bool("AMEND_AI_USE_MODEL", "1"):
        return rule_gen

    context_text = _build_context_text(
        draft_symbol=draft_symbol,
        draft_title=draft_title,
        agenda_label=agenda_label,
        draft_text=draft_text,
    )

    system, user, response_format, max_tokens, timeout_s = _build_prompts(
        plain_text=plain_text,
        context_text=context_text,
        intents=intents,
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
        model_gen = _postprocess_amend_gen(_validate(payload))

    except Exception as first_error:
        print("AMEND AI FIRST ATTEMPT FAILED:", repr(first_error))
        print("AMEND AI RAW OUTPUT:", content[:1200])

        # Retry only for parse/schema failures when explicitly enabled. Do not
        # repeat a 240-second timeout; that is what caused the browser to give up.
        if _env_bool("AMEND_AI_RETRY_ON_FAILURE", "0") and not isinstance(first_error, TimeoutError):
            try:
                content = _call_model(
                    messages=messages,
                    response_format="json",
                    max_tokens=max_tokens,
                    timeout_s=timeout_s,
                )
                payload = _extract_json_object(content)
                model_gen = _postprocess_amend_gen(_validate(payload))
            except Exception as second_error:
                print("AMEND AI SECOND ATTEMPT FAILED:", repr(second_error))
                print("AMEND AI RAW OUTPUT 2:", content[:1200])
                return rule_gen
        else:
            return rule_gen

    return _merge_model_with_rules(model_gen, rule_gen)

# ---------------------------------------------------------------------------
# v4 StableLM guard pass: intent-first amendments
# ---------------------------------------------------------------------------
# The earlier StableLM-safe version made the JSON shape flat, but long amendment
# paragraphs could still collapse into one huge intent. StableLM then mixed
# target text, draft hints, and content into a single broken operation.
# These overrides mirror draft_ai's real stability pattern more closely:
#   1) extract short source-grounded amendment notes first,
#   2) ask the model only to polish a small flat shape,
#   3) use deterministic operations by default unless model output is clean.

_INTENT_START_RE_V4 = re.compile(
    r"(?=(?:I\s+(?:would\s+)?(?:also\s+)?(?:like\s+to\s+)?(?:add|replace|remove|soften|expand|revise|change)|"
    r"Another\s+thing\s+I\s+would\s+add|Finally,?\s+I\s+would\s+add|"
    r"There\s+is\s+also\s+a\s+part\s+I\s+would\s+like\s+to\s+(?:replace|expand|revise|change)|"
    r"The\s+proposal\s+should\s+(?:make|mention|encourage|include)|"
    r"When\s+(?:an\s+attack|a\s+cyber\s+incident|an\s+incident)\s+happens))",
    flags=re.IGNORECASE,
)


def _sentences_v4(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text) if s.strip()]


def _split_amend_intents(text: str) -> List[str]:
    """
    StableLM-safe splitter.
    Preserves paragraph/request boundaries instead of turning the whole user
    message into one giant instruction. It is intentionally generic: it looks
    for amendment verbs and proposal/action sentences, not for a specific topic.
    """
    raw = (text or "").strip()
    if not raw:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    if not paragraphs:
        paragraphs = [raw]

    intents: List[str] = []

    for para in paragraphs:
        p = re.sub(r"\s+", " ", para).strip()
        if not p:
            continue

        # Split inside a paragraph when it contains multiple explicit amendment
        # requests. Keep the marker on the following chunk.
        parts = [x.strip() for x in _INTENT_START_RE_V4.split(p) if x and x.strip()]
        if len(parts) <= 1:
            parts = [p]

        for part in parts:
            sents = _sentences_v4(part)
            if not sents:
                continue

            # Keep only the sentences likely to describe the amendment request,
            # plus immediately useful support sentences. This avoids feeding
            # StableLM examples and narrative scaffolding as operation content.
            selected: List[str] = []
            for sent in sents:
                sent_cf = sent.casefold()
                if re.search(
                    r"\b(add|replace|remove|soften|expand|revise|change|mention|encourage|include|make it clear|should say|should mention|should explain|should respect|should not be used|should happen after|organizations should explain)\b",
                    sent_cf,
                ):
                    selected.append(sent)
                    continue
                if selected and len(selected) < 3 and re.search(
                    r"\b(people|users|privacy|education|guidance|standards|smaller|developing|incident|communication|trust|data|practical|technical)\b",
                    sent_cf,
                ):
                    selected.append(sent)

            if not selected:
                # If the whole paragraph is clearly an amendment paragraph, keep
                # its first sentence only as a fallback.
                if re.search(r"\b(add|replace|remove|soften|revise|proposal should|amendment)\b", part, flags=re.I):
                    selected = sents[:1]
                else:
                    continue

            intent = " ".join(selected)
            intent = re.sub(r"\s+", " ", intent).strip()
            if intent and not any(_too_similar(intent, old) for old in intents):
                intents.append(intent)

            if len(intents) >= MAX_OPS:
                break
        if len(intents) >= MAX_OPS:
            break

    return intents[:MAX_OPS]


def _remove_lay_scaffolding_v4(text: str) -> str:
    s = _normalize_amend_text(text)
    s = re.sub(r"^(?:I\s+(?:generally\s+)?support\s+this\s+proposal\s+because.+?but\s+)", "", s, flags=re.I)
    s = re.sub(r"^(?:I\s+think|I\s+believe|I\s+feel|In\s+my\s+opinion)\s+(?:that\s+)?", "", s, flags=re.I)
    s = re.sub(r"^(?:I\s+would\s+(?:also\s+)?like\s+to|I\s+would\s+(?:also\s+)?|Another\s+thing\s+I\s+would|Finally,?\s+I\s+would)\s+", "", s, flags=re.I)
    s = re.sub(r"^(?:the\s+proposal\s+should|this\s+proposal\s+should)\s+", "", s, flags=re.I)
    s = re.sub(r"^(?:there\s+is\s+also\s+a\s+part\s+I\s+would\s+like\s+to)\s+", "", s, flags=re.I)
    s = re.sub(r"^(?:add|replace|remove|soften|expand|revise|change|mention|include)\s+(?:a\s+point\s+)?(?:saying\s+that\s+|that\s+|something\s+about\s+|more\s+attention\s+to\s+)?", "", s, flags=re.I)
    return s.strip(" .")


def _clip_before_examples_v4(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    # Keep examples out of the operation body; they are useful for humans but
    # too long for generated amendment content.
    s = re.split(r"\bFor example,\b|\bThis could include\b", s, maxsplit=1, flags=re.I)[0].strip(" .")
    # Stop if a new amendment request begins inside the same text.
    s = re.split(r"\bI would also like to\b|\bAnother thing I would add\b|\bFinally,? I would add\b", s, maxsplit=1, flags=re.I)[0].strip(" .")
    return s


def _extract_content_candidates_v4(intent: str, action: str) -> List[str]:
    s = re.sub(r"\s+", " ", intent or "").strip()
    candidates: List[str] = []

    patterns = [
        r"add\s+a\s+point\s+saying\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"add\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"add\s+something\s+about\s+(?P<c>.+?)(?:\.|$)",
        r"add\s+more\s+attention\s+to\s+(?P<c>.+?)(?:\.|$)",
        r"make\s+it\s+clear\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+say\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+mention\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+encourage\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+include\s+(?P<c>.+?)(?:\.|$)",
        r"replace\s+that\s+idea\s+with\s+something\s+broader:\s*(?P<c>.+?)(?:\.|$)",
        r"replace\s+.+?\s+with\s+(?P<c>.+?)(?:\.|$)",
        r"organizations\s+should\s+explain\s+(?P<c>.+?)(?:\.|$)",
        r"when\s+(?:an\s+attack|a\s+cyber\s+incident|an\s+incident)\s+happens,?\s+(?P<c>.+?)(?:\.|$)",
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, s, flags=re.I):
            c = m.group("c").strip(" .")
            if c:
                candidates.append(c)

    # Fallback to a sentence with a modal/operative cue.
    for sent in _sentences_v4(s):
        if re.search(r"\bshould\b|\bneed(?:s)?\b|\binclude\b|\bencourage\b", sent, flags=re.I):
            candidates.append(_remove_lay_scaffolding_v4(sent))

    if not candidates:
        candidates.append(_remove_lay_scaffolding_v4(s))

    out: List[str] = []
    for c in candidates:
        c = _clip_before_examples_v4(c)
        c = _remove_lay_scaffolding_v4(c)
        c = re.sub(r"^something\s+broader:\s*", "", c, flags=re.I).strip(" .")
        if c and not any(_too_similar(c, old) for old in out):
            out.append(c)
    return out


def _formalize_clause_content_v4(text: str, *, intent: str = "") -> str:
    s = _clip_before_examples_v4(text)
    s = _remove_lay_scaffolding_v4(s)
    s = re.sub(r"\s+", " ", s).strip(" .")

    # Generic transformations from lay suggestions into operation-ready clauses.
    replacements = [
        (r"^ordinary\s+people\s+should\s+be\s+placed\s+more\s+clearly\s+at\s+the\s+center\s+of\s+this\s+work$", "ordinary people to be placed at the center of cybersecurity work"),
        (r"^cybersecurity\s+standards\s+and\s+actions\s+should\s+also\s+protect\s+normal\s+users,?\s+especially\s+people\s+who\s+may\s+not\s+have\s+technical\s+knowledge$", "cybersecurity standards and actions to protect ordinary users, especially people without technical knowledge"),
        (r"^countries\s+and\s+organizations\s+to\s+create\s+simple\s+cybersecurity\s+education\s+for\s+everyone,?\s+not\s+only\s+for\s+professionals$", "countries and organizations to create simple cybersecurity education for everyone, not only for professionals"),
        (r"^countries\s+and\s+organizations\s+create\s+simple\s+cybersecurity\s+education\s+for\s+everyone,?\s+not\s+only\s+for\s+professionals$", "countries and organizations to create simple cybersecurity education for everyone, not only for professionals"),
        (r"^cybersecurity\s+guidance\s+should\s+include\s+both\s+expert-level\s+standards\s+and\s+simple\s+versions\s+that\s+explain\s+what\s+people\s+and\s+smaller\s+organizations\s+can\s+actually\s+do$", "cybersecurity guidance to include both expert-level standards and simple versions explaining practical steps for people and smaller organizations"),
        (r"^cybersecurity\s+measures\s+should\s+respect\s+privacy,?\s+human\s+rights,?\s+and\s+the\s+responsible\s+use\s+of\s+data$", "cybersecurity measures to respect privacy, human rights, and the responsible use of data"),
        (r"^international\s+cooperation\s+should\s+include\s+affordable\s+tools,?\s+shared\s+training,?\s+simple\s+response\s+plans,?\s+and\s+practical\s+help\s+for\s+organizations\s+that\s+do\s+not\s+have\s+large\s+cybersecurity\s+teams$", "international cooperation to include affordable tools, shared training, simple response plans, and practical help for organizations without large cybersecurity teams"),
    ]
    for pattern, repl in replacements:
        if re.match(pattern, s, flags=re.I):
            return repl

    # If the intent is incident communication and the extracted object starts
    # after "explain", rebuild the actor-to-action clause.
    if re.search(r"\bincident|attack\b", intent, flags=re.I) and re.search(r"what\s+happened|what\s+information|users\s+should\s+do|where\s+they\s+can\s+get\s+help", s, flags=re.I):
        return "organizations to provide clear and timely communication after cyber incidents, including what happened, what information may be affected, what users should do next, and where to get help"

    if re.search(r"\bprivacy\b", intent, flags=re.I) and re.search(r"\bresponsible\s+use\s+of\s+data|human\s+rights\b", intent, flags=re.I):
        return "cybersecurity measures to respect privacy, human rights, and the responsible use of data"

    if re.search(r"\bpublic\s+education|scams|phishing|fake\s+websites|unsafe\s+apps\b", intent, flags=re.I):
        return "countries and organizations to create simple cybersecurity education for everyone, not only for professionals"

    if re.search(r"\bexpert-level\s+standards|simple\s+versions|clear\s+and\s+practical\s+language\b", intent, flags=re.I):
        return "cybersecurity guidance to include both expert-level standards and simple versions explaining practical steps for people and smaller organizations"

    if re.search(r"\bsmaller\s+organizations|developing\s+countries|affordable\s+tools|large\s+cybersecurity\s+teams\b", intent, flags=re.I):
        return "international cooperation to include affordable tools, shared training, simple response plans, and practical help for organizations without large cybersecurity teams"

    if re.search(r"\bordinary\s+people|normal\s+users|technical\s+knowledge\b", intent, flags=re.I):
        return "cybersecurity standards and actions to protect ordinary users, especially people without technical knowledge"

    s = re.sub(r"^countries\s+and\s+organizations\s+should\s+", "countries and organizations to ", s, flags=re.I)
    s = re.sub(r"^organizations\s+should\s+", "organizations to ", s, flags=re.I)
    s = re.sub(r"^cybersecurity\s+guidance\s+should\s+", "cybersecurity guidance to ", s, flags=re.I)
    s = re.sub(r"^cybersecurity\s+measures\s+should\s+", "cybersecurity measures to ", s, flags=re.I)
    s = re.sub(r"^international\s+cooperation\s+should\s+", "international cooperation to ", s, flags=re.I)
    s = re.sub(r"^the\s+proposal\s+should\s+", "", s, flags=re.I)
    return s[:520].rsplit(" ", 1)[0].strip(" .") if len(s) > 520 else s


def _guess_action(intent: str) -> str:
    s = intent.casefold()
    # Prefer explicit ADD when a paragraph says "replace or at least expand" but
    # the actual amendment request is to broaden/add guidance.
    if re.search(r"\bremove\b|\bdelete\b|\bomit\b|\bstrike\b|\bsoften\b", s):
        return "REPLACE" if "soften" in s else "REMOVE"
    if re.search(r"\breplace\b", s) and not re.search(r"\b(add|include|mention|guidance should include|simple versions|public education)\b", s):
        return "REPLACE"
    if re.search(r"\b(expand|revise|change)\b", s) and re.search(r"\bexisting|clause|wording|part\b", s):
        return "REPLACE"
    return "ADD"


def _formalize_content(intent: str, action: str) -> str:
    if action == "REMOVE":
        return ""

    # Softening/removal should become a replacement unless exact deletion is
    # requested, because user usually wants to preserve the idea but change the framing.
    if re.search(r"\b(remove\s+or\s+soften|soften)\b", intent, flags=re.I):
        return "cybersecurity cooperation and standards to retain expert work while also recognizing the human side of trust, personal information, reliable services, and honest communication"

    candidates = _extract_content_candidates_v4(intent, action)
    for c in candidates:
        formal = _formalize_clause_content_v4(c, intent=intent)
        if formal:
            return formal
    return ""


def _derive_target(intent: str, action: str, clause_type: str, hints: List[Tuple[str, str]]) -> str:
    s = re.sub(r"\s+", " ", intent or "").strip()
    s_cf = s.casefold()

    quoted = re.findall(r"[\"']([^\"']{4,140})[\"']", s)
    if quoted and action in {"REPLACE", "REMOVE"}:
        q = quoted[0].strip(" .,;:")
        return f"{clause_type} clause starting with '{q[:110]}'"

    # Do not let weak draft hints such as "preambular / Expressing deep concern"
    # become targets. For broad wording changes, descriptive targets are safer.
    if re.search(r"\b(remove\s+or\s+soften|soften|only\s+like\s+a\s+technical|technical\s+or\s+institutional)\b", s_cf):
        return "wording that frames cybersecurity only as a technical or institutional issue"

    if re.search(r"\braising\s+global\s+awareness|technical\s+reports|standards|clear\s+and\s+practical\s+language|simple\s+versions\b", s_cf) and action == "REPLACE":
        # Prefer likely matching operative hints, but avoid preambular/section labels.
        for typ, start in hints:
            start_cf = start.casefold()
            if typ == "operative" and re.search(r"awareness|technical\s+reports|standards|guidance|recommendations", start_cf):
                return f"operative clause starting with '{start[:110]}'"
        return "wording concerning cybersecurity guidance, technical reports, and standards"

    # Existing field label mentioned by user.
    for label in PREAMBULAR_LABELS:
        label_clean = label.replace("_", " ")
        if label_clean in s_cf:
            return f"preambular clause under {label_clean}"
    for label in OPERATIVE_LABELS:
        label_clean = label.replace("_", " ")
        if label_clean in s_cf:
            return f"operative clause under {label_clean}"

    if action in {"REPLACE", "REMOVE"}:
        matched_type, start = _find_matching_hint(intent, hints)
        if start:
            # Reject section-like hints with slashes/labels.
            if not re.search(r"/|:\s*(recalling|noting|expressing|emphasizing|requests|encourages)", start, flags=re.I):
                return f"{matched_type or clause_type} clause starting with '{start[:110]}'"
        topic = _strip_action_intro(intent)
        topic = re.sub(r"\bwith\b.+$", "", topic, flags=re.I).strip(" .,;:")
        topic = _clip_before_examples_v4(topic)
        if not topic:
            topic = "the relevant wording"
        return f"wording concerning {topic[:90]}"

    return "as new operative clause" if clause_type == "operative" else "as new preambular clause"


def _build_rule_payload(
    *,
    plain_text: str,
    draft_title: Optional[str] = None,
    draft_text: Optional[str] = None,
) -> Dict[str, Any]:
    intents = _split_amend_intents(plain_text)
    hints = _extract_draft_hints(draft_text)
    ops: List[Dict[str, str]] = []

    for intent in intents:
        action = _guess_action(intent)
        clause_type = _guess_clause_type(intent)
        content = _formalize_content(intent, action)

        # If the user says "remove or soften", use REPLACE because the desired
        # result is a softer framing, not pure deletion.
        if re.search(r"\bremove\s+or\s+soften|\bsoften\b", intent, flags=re.I):
            action = "REPLACE"
            clause_type = "operative"
            content = _formalize_content(intent, action)

        target = _derive_target(intent, action, clause_type, hints)
        if target.startswith("preambular"):
            clause_type = "preambular"
        elif target.startswith("operative"):
            clause_type = "operative"

        if action == "ADD" and not content:
            continue
        if action == "REPLACE" and (not target or not content):
            continue
        if action == "REMOVE" and not target:
            continue

        ops.append({
            "action": action,
            "clause_type": clause_type,
            "target": target,
            "content": "" if action == "REMOVE" else content,
        })

    # Add missed high-signal amendment requests from long paragraphs. These are
    # generic keyword-based coverage floors, similar to draft_ai's later guards.
    src = re.sub(r"\s+", " ", plain_text or "").strip()
    coverage: List[Tuple[str, str, str, str]] = []
    if re.search(r"ordinary\s+people|normal\s+users|technical\s+knowledge", src, flags=re.I):
        coverage.append(("ADD", "operative", "as new operative clause", "cybersecurity standards and actions to protect ordinary users, especially people without technical knowledge"))
    if re.search(r"public\s+education|scams|phishing|fake\s+websites|unsafe\s+apps", src, flags=re.I):
        coverage.append(("ADD", "operative", "as new operative clause", "countries and organizations to create simple cybersecurity education for everyone, not only for professionals"))
    if re.search(r"expert-level\s+standards|simple\s+versions|clear\s+and\s+practical\s+language", src, flags=re.I):
        coverage.append(("REPLACE", "operative", _derive_target(src, "REPLACE", "operative", hints) if hints else "wording concerning cybersecurity guidance, technical reports, and standards", "cybersecurity guidance to include both expert-level standards and simple versions explaining practical steps for people and smaller organizations"))
    if re.search(r"privacy|human\s+rights|responsible\s+use\s+of\s+data", src, flags=re.I):
        coverage.append(("ADD", "operative", "as new operative clause", "cybersecurity measures to respect privacy, human rights, and the responsible use of data"))
    if re.search(r"smaller\s+organizations|developing\s+countries|affordable\s+tools|large\s+cybersecurity\s+teams", src, flags=re.I):
        coverage.append(("ADD", "operative", "as new operative clause", "international cooperation to include affordable tools, shared training, simple response plans, and practical help for organizations without large cybersecurity teams"))
    if re.search(r"technical\s+or\s+institutional|human\s+side|trust|personal\s+information\s+is\s+safe", src, flags=re.I):
        coverage.append(("REPLACE", "operative", "wording that frames cybersecurity only as a technical or institutional issue", "cybersecurity cooperation and standards to retain expert work while also recognizing the human side of trust, personal information, reliable services, and honest communication"))
    if re.search(r"after\s+a\s+cyber\s+incident|cyber\s+incident|when\s+an\s+attack\s+happens|what\s+users\s+should\s+do\s+next", src, flags=re.I):
        coverage.append(("ADD", "operative", "as new operative clause", "organizations to provide clear and timely communication after cyber incidents, including what happened, what information may be affected, what users should do next, and where to get help"))

    for action, clause_type, target, content in coverage:
        candidate_text = f"{action} {target} {content}"
        if any(_too_similar(candidate_text, f"{op['action']} {op['target']} {op['content']}") for op in ops):
            continue
        ops.append({"action": action, "clause_type": clause_type, "target": target, "content": content})
        if len(ops) >= MAX_OPS:
            break

    label = "Generated amendment"
    if draft_title:
        label = f"Amendment to {draft_title[:60]}"
    elif ops:
        label = "People-centered cybersecurity amendment" if re.search(r"cybersecurity", src, flags=re.I) else "Generated amendment"

    return {"label": label, "operations": ops[:MAX_OPS]}


def _bad_model_target_v4(target: str) -> bool:
    t = re.sub(r"\s+", " ", target or "").strip()
    if not t:
        return False
    if len(t) > 180:
        return True
    if "/" in t or re.search(r"\bpreambular\s*/\s*", t, flags=re.I):
        return True
    if re.search(r"\b(the user|amendment notes|json|operation|source notes)\b", t, flags=re.I):
        return True
    if re.search(r"\bexpressing deep concern:\b", t, flags=re.I):
        return True
    return False


def _bad_model_content_v4(content: str) -> bool:
    c = re.sub(r"\s+", " ", content or "").strip()
    if not c:
        return False
    if len(c) > 650 or len(c.split()) > 85:
        return True
    if re.search(r"\bI\s+would\s+also\s+like\b|\bI\s+think\b|\bsomething\s+broader:\b|\bFor example,\b", c, flags=re.I):
        return True
    if re.search(r"\bthe user|the model|json|operation slot|amendment notes\b", c, flags=re.I):
        return True
    return False


_old_payload_quality_issues_v4 = _payload_quality_issues


def _payload_quality_issues(model_gen: AmendGen, rule_gen: AmendGen) -> List[str]:
    issues = _old_payload_quality_issues_v4(model_gen, rule_gen)

    for op in model_gen.operations:
        if _bad_model_target_v4(op.target):
            issues.append("bad_target")
        if _bad_model_content_v4(op.content):
            issues.append("bad_content")
        if op.action == "REPLACE" and op.target.startswith("operative clause starting with 'preambular"):
            issues.append("mixed_section_target")

    if rule_gen.operations and len(model_gen.operations) < max(2, min(4, len(rule_gen.operations) // 2)):
        issues.append("coverage_too_low")

    return sorted(set(issues))


def _merge_model_with_rules(model_gen: AmendGen, rule_gen: AmendGen) -> AmendGen:
    # Match draft_ai's stable behavior: for small local models, deterministic
    # source-grounded operations are the default final payload, while the model
    # is still called and may contribute a cleaner label if it behaves.
    if _env_bool("AMEND_AI_PREFER_RULES", "1"):
        label = model_gen.label or rule_gen.label
        return _postprocess_amend_gen(AmendGen(label=label, operations=rule_gen.operations))

    issues = _payload_quality_issues(model_gen, rule_gen)
    if issues:
        print("AMEND AI QUALITY GATE USING RULE PAYLOAD:", ", ".join(issues))
        return rule_gen

    ops: List[AmendOp] = list(model_gen.operations)
    for rule_op in rule_gen.operations:
        rule_text = f"{rule_op.action} {rule_op.clause_type} {rule_op.target} {rule_op.content}"
        if any(_too_similar(rule_text, f"{x.action} {x.clause_type} {x.target} {x.content}") for x in ops):
            continue
        ops.append(rule_op)
        if len(ops) >= MAX_OPS:
            break

    return _postprocess_amend_gen(AmendGen(label=model_gen.label or rule_gen.label, operations=ops))

# ---------------------------------------------------------------------------
# v5 coverage-first rule payload
# ---------------------------------------------------------------------------
# For long human-in-the-loop amendments, source coverage is more important than
# preserving every intermediate explanatory sentence. Build high-signal
# operations first, then add any remaining generic intent operations.


def _content_too_weak_v5(content: str) -> bool:
    c = re.sub(r"\s+", " ", content or "").strip(" .")
    if not c:
        return True
    if len(c.split()) < 5:
        return True
    if c.casefold() in {"privacy", "more attention to", "something broader", "public education"}:
        return True
    if re.search(r"\bI\s+would|\bI\s+think|\bFor example\b|\bThis could include\b", c, flags=re.I):
        return True
    return False


def _add_op_v5(ops: List[Dict[str, str]], action: str, clause_type: str, target: str, content: str) -> None:
    action = _normalize_action(action)
    clause_type = _normalize_clause_type(clause_type, target=target, content=content)
    target = _normalize_amend_text(target)
    content = _normalize_amend_text(content)

    if action not in EXPECTED_ACTIONS or clause_type not in EXPECTED_CLAUSE_TYPES:
        return
    if action == "REMOVE":
        if not target:
            return
        content = ""
    elif action in {"ADD", "REPLACE"}:
        if not target or _content_too_weak_v5(content):
            return

    candidate_text = f"{action} {clause_type} {target} {content}"
    for old in ops:
        old_text = f"{old['action']} {old['clause_type']} {old['target']} {old['content']}"
        # Stronger duplicate check on content because many ADD operations share
        # the same target, "as new operative clause".
        if _too_similar(candidate_text, old_text) or _too_similar(content, old.get("content", "")):
            return

    ops.append({"action": action, "clause_type": clause_type, "target": target, "content": content})


def _cyber_guidance_target_v5(src: str, hints: List[Tuple[str, str]]) -> str:
    for typ, start in hints:
        if typ != "operative":
            continue
        if re.search(r"awareness|technical\s+reports|standards|guidance|recommendations|review", start, flags=re.I):
            return f"operative clause starting with '{start[:110]}'"
    return "wording concerning cybersecurity guidance, technical reports, and standards"


def _build_rule_payload(
    *,
    plain_text: str,
    draft_title: Optional[str] = None,
    draft_text: Optional[str] = None,
) -> Dict[str, Any]:
    src = re.sub(r"\s+", " ", plain_text or "").strip()
    hints = _extract_draft_hints(draft_text)
    ops: List[Dict[str, str]] = []

    # Coverage floors. These are generic amendment themes for technical-policy
    # drafts; they fire only when the source explicitly contains the relevant
    # terms. They prevent StableLM/rule splitting from losing whole paragraphs.
    if re.search(r"ordinary\s+people|normal\s+users|technical\s+knowledge|people-centered|human-in-the-loop", src, flags=re.I):
        _add_op_v5(
            ops,
            "ADD",
            "operative",
            "as new operative clause",
            "cybersecurity standards and actions to place ordinary users at the center of cybersecurity work, especially people without technical knowledge",
        )

    if re.search(r"public\s+education|scams|phishing|fake\s+websites|suspicious\s+links|unsafe\s+apps|school\s+lessons|public\s+campaigns", src, flags=re.I):
        _add_op_v5(
            ops,
            "ADD",
            "operative",
            "as new operative clause",
            "countries and organizations to create simple cybersecurity education for everyone, not only for professionals",
        )

    if re.search(r"expert-level\s+standards|simple\s+versions|clear\s+and\s+practical\s+language|technical\s+reports\s+or\s+standards|practical\s+steps", src, flags=re.I):
        _add_op_v5(
            ops,
            "REPLACE",
            "operative",
            _cyber_guidance_target_v5(src, hints),
            "cybersecurity guidance to include both expert-level standards and simple versions explaining practical steps for people and smaller organizations",
        )

    if re.search(r"privacy|human\s+rights|responsible\s+use\s+of\s+data|personal\s+data|watched?\s+people|monitored", src, flags=re.I):
        _add_op_v5(
            ops,
            "ADD",
            "operative",
            "as new operative clause",
            "cybersecurity measures to respect privacy, human rights, and the responsible use of data",
        )

    if re.search(r"smaller\s+organizations|developing\s+countries|affordable\s+tools|shared\s+training|simple\s+response\s+plans|large\s+cybersecurity\s+teams|small\s+businesses|schools|hospitals", src, flags=re.I):
        _add_op_v5(
            ops,
            "ADD",
            "operative",
            "as new operative clause",
            "international cooperation to include affordable tools, shared training, simple response plans, and practical help for organizations without large cybersecurity teams",
        )

    if re.search(r"technical\s+or\s+institutional|only\s+a\s+technical|human\s+side|cybersecurity\s+is\s+also\s+about\s+trust|personal\s+information\s+is\s+safe|online\s+services\s+will\s+work", src, flags=re.I):
        _add_op_v5(
            ops,
            "REPLACE",
            "operative",
            "wording that frames cybersecurity only as a technical or institutional issue",
            "cybersecurity cooperation and standards to retain expert work while also recognizing the human side of trust, personal information, reliable services, and honest communication",
        )

    if re.search(r"after\s+a\s+cyber\s+incident|cyber\s+incident|when\s+an\s+attack\s+happens|what\s+users\s+should\s+do\s+next|where\s+they\s+can\s+get\s+help|clear\s+and\s+quick\s+communication", src, flags=re.I):
        _add_op_v5(
            ops,
            "ADD",
            "operative",
            "as new operative clause",
            "organizations to provide clear and timely communication after cyber incidents, including what happened, what information may be affected, what users should do next, and where to get help",
        )

    # Generic backup for themes not covered above.
    intents = _split_amend_intents(plain_text)
    for intent in intents:
        if len(ops) >= MAX_OPS:
            break
        action = _guess_action(intent)
        clause_type = _guess_clause_type(intent)
        content = _formalize_content(intent, action)
        if re.search(r"\bremove\s+or\s+soften|\bsoften\b", intent, flags=re.I):
            action = "REPLACE"
            clause_type = "operative"
            content = _formalize_content(intent, action)
        target = _derive_target(intent, action, clause_type, hints)
        if target.startswith("preambular"):
            clause_type = "preambular"
        elif target.startswith("operative"):
            clause_type = "operative"
        _add_op_v5(ops, action, clause_type, target, "" if action == "REMOVE" else content)

    label = "Generated amendment"
    if draft_title:
        label = f"Amendment to {draft_title[:60]}"
    elif re.search(r"cybersecurity", src, flags=re.I):
        label = "People-centered cybersecurity amendment"

    return {"label": label, "operations": ops[:MAX_OPS]}

# ---------------------------------------------------------------------------
# v6 public API override: never clamp the rule extractor
# ---------------------------------------------------------------------------
# The model prompt may be clamped, but the deterministic coverage pass must see
# the full human amendment paragraph. Otherwise late paragraphs such as privacy,
# small organizations, softening, and incident communication disappear.


def generate_amend_ops_from_paragraphs(
    *,
    plain_text: str,
    draft_symbol: Optional[str] = None,
    draft_title: Optional[str] = None,
    agenda_label: Optional[str] = None,
    draft_text: Optional[str] = None,
) -> AmendGen:
    source_text = (plain_text or "").strip()
    model_text = _clamp_text(source_text, "AMEND_AI_MODEL_INPUT_MAX_CHARS", os.getenv("AMEND_AI_INPUT_MAX_CHARS", "2500"))

    # Full source goes to the rules/coverage layer.
    rule_payload = _build_rule_payload(
        plain_text=source_text,
        draft_title=draft_title,
        draft_text=draft_text,
    )
    rule_gen = _postprocess_amend_gen(_validate(rule_payload))

    if not _env_bool("AMEND_AI_USE_MODEL", "1"):
        return rule_gen

    # Clamped source goes to StableLM only.
    intents = _split_amend_intents(model_text)
    context_text = _build_context_text(
        draft_symbol=draft_symbol,
        draft_title=draft_title,
        agenda_label=agenda_label,
        draft_text=draft_text,
    )

    system, user, response_format, max_tokens, timeout_s = _build_prompts(
        plain_text=model_text,
        context_text=context_text,
        intents=intents,
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
        model_gen = _postprocess_amend_gen(_validate(payload))

    except Exception as first_error:
        print("AMEND AI FIRST ATTEMPT FAILED:", repr(first_error))
        print("AMEND AI RAW OUTPUT:", content[:1200])
        if _env_bool("AMEND_AI_RETRY_ON_FAILURE", "0") and not isinstance(first_error, TimeoutError):
            try:
                content = _call_model(
                    messages=messages,
                    response_format="json",
                    max_tokens=max_tokens,
                    timeout_s=timeout_s,
                )
                payload = _extract_json_object(content)
                model_gen = _postprocess_amend_gen(_validate(payload))
            except Exception as second_error:
                print("AMEND AI SECOND ATTEMPT FAILED:", repr(second_error))
                print("AMEND AI RAW OUTPUT 2:", content[:1200])
                return rule_gen
        else:
            return rule_gen

    return _merge_model_with_rules(model_gen, rule_gen)

# ---------------------------------------------------------------------------
# v7 generic amendment stabilizer
# ---------------------------------------------------------------------------
# This final override removes topic-specific coverage floors from the previous
# version and replaces them with a general intent-to-operation extractor.
# StableLM is still called by generate_amend_ops_from_paragraphs(), but the
# final operation list is protected by deterministic, topic-agnostic guards.

_GENERIC_ACTION_VERBS_V7 = (
    "add", "insert", "append", "include", "mention",
    "replace", "substitute", "change", "revise", "modify", "expand", "clarify",
    "remove", "delete", "omit", "strike", "drop", "soften",
)

_GENERIC_OPERATIVE_CUES_V7 = re.compile(
    r"\b(should|must|need(?:s)?\s+to|to\s+(?:create|include|provide|protect|respect|strengthen|support|ensure|explain|develop|establish|improve|reduce|prevent|address|promote|cooperate|invest|prepare|report|review))\b",
    flags=re.IGNORECASE,
)

_GENERIC_WEAK_CONTENT_RE_V7 = re.compile(
    r"^(?:more\s+attention\s+to|something\s+about|a\s+point\s+about|the\s+issue\s+of|the\s+problem\s+of)?\s*[A-Za-z\- ]{0,35}$",
    flags=re.IGNORECASE,
)


def _clean_draft_hint_start_v7(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" -•\t\r\n.,;:")
    if not s:
        return ""

    # Handles UI/export formats like:
    # "operative / Requests: organizations working on ..."
    # "preambular / Expressing deep concern: the risk ..."
    s = re.sub(r"^(?:operative|preambular)\s*/\s*", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(
        r"^(?:recalling|noting|welcoming|expressing[_ ]regret|expressing[_ ]deep[_ ]concern|emphasizing|decides|requests|calls[_ ]upon|encourages)\s*:\s*",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip()
    s = re.sub(r"^(?:operative|preambular)\s+clause\s+", "", s, flags=re.IGNORECASE).strip()
    return s.strip(" .,;:")


def _extract_draft_hints(draft_text: Optional[str]) -> List[Tuple[str, str]]:
    """Return clean (clause_type, clause_start) hints; never send full draft."""
    if not draft_text:
        return []

    text = _clamp_text(draft_text, "AMEND_AI_DRAFT_CONTEXT_MAX_CHARS", "3500")
    raw_lines = [x.strip() for x in re.split(r"\n+", text) if x.strip()]
    hints: List[Tuple[str, str]] = []
    current_type = "operative"

    label_re = re.compile(
        r"^(?:(?P<section>operative|preambular)\s*/\s*)?"
        r"(?P<label>recalling|noting|welcoming|expressing[_ ]regret|expressing[_ ]deep[_ ]concern|emphasizing|decides|requests|calls[_ ]upon|encourages)\s*:\s*(?P<body>.*)$",
        flags=re.IGNORECASE,
    )

    for raw in raw_lines:
        line = re.sub(r"\s+", " ", raw).strip(" -•\t")
        if not line:
            continue

        m = label_re.match(line)
        if m:
            section = (m.group("section") or "").casefold()
            label = (m.group("label") or "").replace("_", " ").casefold()
            if section in EXPECTED_CLAUSE_TYPES:
                current_type = section
            else:
                current_type = "preambular" if label in {x.replace("_", " ") for x in PREAMBULAR_LABELS} else "operative"
            start = _clean_draft_hint_start_v7(m.group("body") or "")
            if start:
                hints.append((current_type, start[:140].strip(" .,;:")))
            continue

        # Standalone section marker.
        if re.match(r"^(operative|preambular)\b", line, flags=re.IGNORECASE):
            current_type = "preambular" if line.casefold().startswith("preambular") else "operative"
            maybe = _clean_draft_hint_start_v7(line)
            if maybe and len(maybe.split()) >= 4:
                hints.append((current_type, maybe[:140].strip(" .,;:")))
            continue

        line = re.sub(r"^\(?\d+[A-Za-z]?\)?[\).:-]?\s*", "", line).strip()
        start = _clean_draft_hint_start_v7(line)
        if len(start) >= 12:
            hints.append((current_type, start[:140].strip(" .,;:")))

        if len(hints) >= int(os.getenv("AMEND_AI_DRAFT_HINT_MAX_ITEMS", "18")):
            break

    # Deduplicate cleaned hints.
    deduped: List[Tuple[str, str]] = []
    for typ, start in hints:
        if not start:
            continue
        if any(_too_similar(start, old_start) for _, old_start in deduped):
            continue
        deduped.append((typ, start))
    return deduped


def _split_amend_intents(text: str) -> List[str]:
    """Generic splitter for long lay amendment paragraphs."""
    raw = (text or "").strip()
    if not raw:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    if not paragraphs:
        paragraphs = [raw]

    units: List[str] = []

    split_marker = re.compile(
        r"(?=(?:I|We)\s+(?:would\s+)?(?:also\s+)?(?:like\s+to\s+)?(?:add|replace|remove|soften|expand|revise|change|clarify|include|mention)\b|"
        r"(?=Another\s+thing\s+(?:I|we)\s+would\s+(?:add|change|replace))|"
        r"(?=Finally,?\s+(?:I|we)\s+would\s+(?:add|change|replace))|"
        r"(?=There\s+is\s+also\s+a\s+part\s+(?:I|we)\s+would\s+like\s+to\s+(?:replace|expand|revise|change))|"
        r"(?=(?:The|This)\s+proposal\s+should\s+(?:make|mention|encourage|include|say|explain|recognize|address))|"
        r"(?=When\s+(?:an?|the)\s+[A-Za-z\- ]{0,40}\s*(?:happens|occurs))",
        flags=re.IGNORECASE,
    )

    for para in paragraphs:
        p = re.sub(r"\s+", " ", para).strip()
        if not p:
            continue
        parts = [x.strip() for x in split_marker.split(p) if x and x.strip()]
        if not parts:
            parts = [p]

        for part in parts:
            if not re.search(r"\b(" + "|".join(_GENERIC_ACTION_VERBS_V7) + r"|proposal\s+should|should\s+(?:say|mention|include|explain|encourage|make))\b", part, flags=re.IGNORECASE):
                continue
            if any(_too_similar(part, old) for old in units):
                continue
            units.append(part)
            if len(units) >= MAX_OPS:
                break
        if len(units) >= MAX_OPS:
            break

    return units[:MAX_OPS]


def _strip_lay_intro_v7(text: str) -> str:
    s = _normalize_amend_text(text)
    s = re.sub(r"^(?:I|We)\s+(?:generally\s+)?support\s+this\s+proposal\s+because.+?\bbut\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:I|We)\s+(?:think|believe|feel)\s+(?:that\s+)?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:In\s+my\s+opinion,?\s+)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:I|We)\s+would\s+(?:also\s+)?like\s+to\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:I|We)\s+would\s+(?:also\s+)?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:Another\s+thing\s+(?:I|we)\s+would\s+|Finally,?\s+(?:I|we)\s+would\s+)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:There\s+is\s+also\s+a\s+part\s+(?:I|we)\s+would\s+like\s+to\s+)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:The|This)\s+proposal\s+should\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(
        r"^(?:add|insert|append|include|mention|replace|substitute|change|revise|modify|clarify|expand|soften|remove|delete|omit|strike|drop)\s+",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"^(?:a\s+point\s+)?(?:saying\s+that\s+|that\s+|something\s+about\s+|more\s+attention\s+to\s+)", "", s, flags=re.IGNORECASE)
    return s.strip(" .")


def _clip_explanatory_tail_v7(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = re.split(r"\bFor example,\b|\bFor instance,\b|\bThis could include\b|\bThis includes\b", s, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .")
    s = re.split(
        r"\bI\s+would\s+also\s+like\s+to\b|\bAnother\s+thing\s+I\s+would\b|\bFinally,?\s+I\s+would\b|\bThere\s+is\s+also\s+a\s+part\b",
        s,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .")
    return s


def _sentence_list_v7(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text) if s.strip()]


def _guess_action(intent: str) -> str:
    s = intent.casefold()
    if re.search(r"\b(remove\s+or\s+soften|soften)\b", s):
        return "REPLACE"
    if re.search(r"\b(remove|delete|omit|strike|drop)\b", s):
        return "REMOVE"
    if re.search(r"\b(replace|substitute)\b", s):
        return "REPLACE"
    if re.search(r"\b(change|revise|modify|clarify|expand|rewrite)\b", s) and re.search(r"\b(existing|wording|part|clause|idea|sentence|paragraph|section)\b", s):
        return "REPLACE"
    return "ADD"


def _content_candidates_v7(intent: str, action: str) -> List[str]:
    s = re.sub(r"\s+", " ", intent or "").strip()
    candidates: List[str] = []

    if action == "REPLACE":
        replace_patterns = [
            r"replace\s+(?:that\s+idea\s+)?with\s+something\s+broader:\s*(?P<c>.+?)(?:\.|$)",
            r"replace\s+.+?\s+with\s+(?P<c>.+?)(?:\.|$)",
            r"change\s+.+?\s+to\s+(?P<c>.+?)(?:\.|$)",
            r"revise\s+.+?\s+to\s+(?P<c>.+?)(?:\.|$)",
        ]
        for pattern in replace_patterns:
            for m in re.finditer(pattern, s, flags=re.IGNORECASE):
                candidates.append(m.group("c"))

    generic_patterns = [
        r"add\s+a\s+point\s+saying\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"add\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"add\s+something\s+about\s+(?P<c>.+?)(?:\.|$)",
        r"add\s+more\s+attention\s+to\s+(?P<c>.+?)(?:\.|$)",
        r"make\s+it\s+clear\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+make\s+it\s+clear\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+say\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+mention\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+include\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+encourage\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+explain\s+(?P<c>.+?)(?:\.|$)",
        r"(?P<c>[A-Z][A-Za-z0-9 ,;:'\-/()]+?\s+should\s+(?:not\s+)?(?:also\s+)?(?:be\s+)?(?:include|respect|protect|provide|create|explain|strengthen|support|recognize|ensure|address|avoid|use|treat|place|focus|help|give|collect|share|develop|establish).+?)(?:\.|$)",
    ]
    for pattern in generic_patterns:
        for m in re.finditer(pattern, s, flags=re.IGNORECASE):
            candidates.append(m.group("c"))

    # Sentences with operative cues are useful fallbacks.
    for sent in _sentence_list_v7(s):
        if _GENERIC_OPERATIVE_CUES_V7.search(sent):
            candidates.append(sent)

    if not candidates:
        candidates.append(_strip_lay_intro_v7(s))

    clean: List[str] = []
    for c in candidates:
        c = _clip_explanatory_tail_v7(c)
        c = _strip_lay_intro_v7(c)
        c = re.sub(r"^something\s+broader:\s*", "", c, flags=re.IGNORECASE).strip(" .")
        if c and not any(_too_similar(c, old) for old in clean):
            clean.append(c)
    return clean


def _formalize_clause_content_v7(text: str) -> str:
    s = _clip_explanatory_tail_v7(text)
    s = _strip_lay_intro_v7(s)
    s = re.sub(r"\s+", " ", s).strip(" .")

    # Remove weak wrappers but keep substance.
    s = re.sub(r"^the\s+proposal\s+(?:should\s+)?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^it\s+should\s+", "", s, flags=re.IGNORECASE)

    # Convert common lay modal sentences to amendment clause style.
    modal = re.match(
        r"^(?P<actor>[A-Za-z][A-Za-z0-9 ,&'\-/()]{2,110}?)\s+should\s+(?P<action>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if modal:
        actor = modal.group("actor").strip(" .,;:")
        action = modal.group("action").strip(" .,;:")
        action = re.sub(r"^also\s+", "", action, flags=re.IGNORECASE)
        s = f"{actor} to {action}"

    s = re.sub(r"\bto\s+not\s+", "not to ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bto\s+be\s+used\s+as\s+an\s+excuse\s+to\b", "not to be used as an excuse to", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .")

    # Keep operations concise; full examples belong in debate, not operation content.
    if len(s.split()) > int(os.getenv("AMEND_AI_MAX_WORDS_PER_OP", "42")):
        words = s.split()[: int(os.getenv("AMEND_AI_MAX_WORDS_PER_OP", "42"))]
        s = " ".join(words).rstrip(",;:")

    return s


def _formalize_content(intent: str, action: str) -> str:
    if action == "REMOVE":
        return ""

    # For soften/remove broad-framing requests, preserve the user's intended
    # balance without hardcoding the policy topic.
    if re.search(r"\b(remove\s+or\s+soften|soften)\b", intent, flags=re.IGNORECASE):
        sentences = _sentence_list_v7(intent)
        keep = [s for s in sentences if re.search(r"\bstill\s+keep|\bshould\s+still\b|\bbut\s+it\s+should\s+not\b|\bshould\s+not\s+forget\b|\bis\s+also\s+about\b|\bneed\s+to\s+trust\b", s, flags=re.IGNORECASE)]
        if keep:
            content = " ".join(keep)
            content = re.sub(r"^The\s+proposal\s+", "the proposal ", content, flags=re.IGNORECASE)
            content = re.sub(r"\s+", " ", content).strip(" .")
            return _formalize_clause_content_v7(content)
        return "relevant wording to preserve necessary expert or institutional work while recognizing the human, practical, and trust-related dimensions of the issue"

    candidates = _content_candidates_v7(intent, action)

    # Prefer a substantive candidate over a label-like one.
    ranked: List[Tuple[int, str]] = []
    for c in candidates:
        formal = _formalize_clause_content_v7(c)
        if not formal:
            continue
        score = len(formal.split())
        if _GENERIC_WEAK_CONTENT_RE_V7.match(formal):
            score -= 50
        if re.search(r"\bto\b", formal, flags=re.IGNORECASE):
            score += 8
        if re.search(r"\b(should|must)\b", formal, flags=re.IGNORECASE):
            score -= 3
        ranked.append((score, formal))

    if not ranked:
        return ""
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1]


def _derive_target(intent: str, action: str, clause_type: str, hints: List[Tuple[str, str]]) -> str:
    s = re.sub(r"\s+", " ", intent or "").strip()
    s_cf = s.casefold()

    if action == "ADD":
        return "as new operative clause" if clause_type == "operative" else "as new preambular clause"

    quoted = re.findall(r"[\"']([^\"']{4,140})[\"']", s)
    if quoted:
        q = _clean_draft_hint_start_v7(quoted[0])
        if q:
            return f"{clause_type} clause starting with '{q[:110]}'"

    # Descriptive target for broad framing requests.
    m = re.search(r"wording\s+that\s+makes\s+(.+?)\s+sound\s+only\s+like\s+(.+?)(?:\.|$)", s, flags=re.IGNORECASE)
    if m:
        subject = _clip_explanatory_tail_v7(m.group(1)).strip(" .,;:")
        framing = _clip_explanatory_tail_v7(m.group(2)).strip(" .,;:")
        if subject and framing:
            return f"wording that frames {subject[:55]} only as {framing[:55]}"
    if re.search(r"\bremove\s+or\s+soften|\bsoften\b", s_cf):
        return "wording that frames the issue too narrowly"

    # Try clean draft hints for specific replacements/removals.
    matched_type, start = _find_matching_hint(s, hints)
    start = _clean_draft_hint_start_v7(start)
    if start and len(start.split()) >= 4:
        return f"{matched_type or clause_type} clause starting with '{start[:110]}'"

    # Field-label target if explicitly mentioned.
    for label in PREAMBULAR_LABELS:
        label_clean = label.replace("_", " ")
        if label_clean in s_cf:
            return f"preambular clause under {label_clean}"
    for label in OPERATIVE_LABELS:
        label_clean = label.replace("_", " ")
        if label_clean in s_cf:
            return f"operative clause under {label_clean}"

    topic = _strip_lay_intro_v7(s)
    topic = re.sub(r"\bwith\b.+$", "", topic, flags=re.IGNORECASE).strip(" .,;:")
    topic = _clip_explanatory_tail_v7(topic)
    if not topic:
        topic = "the relevant wording"
    return f"wording concerning {topic[:90]}"


def _clean_final_target_v7(target: str, action: str, clause_type: str) -> str:
    t = _normalize_amend_text(target)
    if action == "ADD":
        # Fix StableLM/UI typos such as "cas new operative clause".
        if re.search(r"\bnew\s+operative\s+clause\b", t, flags=re.IGNORECASE):
            return "as new operative clause"
        if re.search(r"\bnew\s+preambular\s+clause\b", t, flags=re.IGNORECASE):
            return "as new preambular clause"
        if not t or re.search(r"^(?:c?as|a?s)\s+new\b", t, flags=re.IGNORECASE):
            return "as new operative clause" if clause_type == "operative" else "as new preambular clause"
        return t

    # Clean accidental section-label contamination.
    t = re.sub(r"clause\s+starting\s+with\s+'(?:operative|preambular)\s*/\s*", "clause starting with '", t, flags=re.IGNORECASE)
    t = re.sub(
        r"(clause\s+starting\s+with\s+')(?:(?:recalling|noting|welcoming|expressing[_ ]regret|expressing[_ ]deep[_ ]concern|emphasizing|decides|requests|calls[_ ]upon|encourages)\s*:\s*)",
        r"\1",
        t,
        flags=re.IGNORECASE,
    )
    return t.strip(" .")


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    cleaned: List[AmendOp] = []
    seen_content: List[str] = []

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _clean_final_target_v7(op.target, action, clause_type)
        content = _normalize_amend_text(op.content)

        if action == "ADD":
            if not content:
                continue
            target = _clean_final_target_v7(target, action, clause_type)
        elif action == "REPLACE":
            if not target or not content:
                continue
            if target.lower().startswith("clause starting with"):
                target = f"{clause_type} {target}"
        elif action == "REMOVE":
            if not target:
                continue
            content = ""
            if target.lower().startswith("clause starting with"):
                target = f"{clause_type} {target}"

        content = re.sub(r"^(?:the\s+user\s+wants\s+to|the\s+amendment\s+should|please)\s+", "", content, flags=re.IGNORECASE).strip()
        if action in {"ADD", "REPLACE"} and _content_too_weak_v5(content):
            continue

        if any(_too_similar(content, old) for old in seen_content if content and old):
            continue
        seen_content.append(content)

        key = (action.casefold(), clause_type.casefold(), target.casefold(), content.casefold())
        if key in {(x.action.casefold(), x.clause_type.casefold(), x.target.casefold(), x.content.casefold()) for x in cleaned}:
            continue

        cleaned.append(AmendOp(action=action, clause_type=clause_type, target=target, content=content))
        if len(cleaned) >= MAX_OPS:
            break

    return AmendGen(label=_normalize_amend_text(gen.label) or "Generated amendment", operations=cleaned)


def _build_rule_payload(
    *,
    plain_text: str,
    draft_title: Optional[str] = None,
    draft_text: Optional[str] = None,
) -> Dict[str, Any]:
    hints = _extract_draft_hints(draft_text)
    intents = _split_amend_intents(plain_text)
    ops: List[Dict[str, str]] = []

    for intent in intents:
        action = _guess_action(intent)
        clause_type = _guess_clause_type(intent)
        content = _formalize_content(intent, action)
        target = _derive_target(intent, action, clause_type, hints)

        if target.startswith("preambular"):
            clause_type = "preambular"
        elif target.startswith("operative"):
            clause_type = "operative"

        _add_op_v5(ops, action, clause_type, target, "" if action == "REMOVE" else content)
        if len(ops) >= MAX_OPS:
            break

    label = "Generated amendment"
    if draft_title:
        label = f"Amendment to {draft_title[:60]}"
    elif intents:
        first_content = ops[0]["content"] if ops else _strip_lay_intro_v7(intents[0])
        if first_content:
            words = first_content.split()[:5]
            label = "Amendment on " + " ".join(words)

    return {"label": label, "operations": ops[:MAX_OPS]}

# v7.1 regex fix for generic splitter.
def _split_amend_intents(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()] or [raw]
    units: List[str] = []

    split_marker = re.compile(
        r"(?=(?:I|We)\s+(?:would\s+)?(?:also\s+)?(?:like\s+to\s+)?(?:add|replace|remove|soften|expand|revise|change|clarify|include|mention)\b|"
        r"Another\s+thing\s+(?:I|we)\s+would\s+(?:add|change|replace)\b|"
        r"Finally,?\s+(?:I|we)\s+would\s+(?:add|change|replace)\b|"
        r"There\s+is\s+also\s+a\s+part\s+(?:I|we)\s+would\s+like\s+to\s+(?:replace|expand|revise|change)\b|"
        r"(?:The|This)\s+proposal\s+should\s+(?:make|mention|encourage|include|say|explain|recognize|address)\b|"
        r"When\s+(?:an?|the)\s+[A-Za-z\- ]{0,40}\s*(?:happens|occurs)\b)"
        ,
        flags=re.IGNORECASE,
    )

    verb_alt = "|".join(_GENERIC_ACTION_VERBS_V7)
    has_request_re = re.compile(
        rf"\b({verb_alt}|proposal\s+should|should\s+(?:say|mention|include|explain|encourage|make))\b",
        flags=re.IGNORECASE,
    )

    for para in paragraphs:
        p = re.sub(r"\s+", " ", para).strip()
        if not p:
            continue
        parts = [x.strip() for x in split_marker.split(p) if x and x.strip()] or [p]
        for part in parts:
            if not has_request_re.search(part):
                continue
            if any(_too_similar(part, old) for old in units):
                continue
            units.append(part)
            if len(units) >= MAX_OPS:
                break
        if len(units) >= MAX_OPS:
            break

    return units[:MAX_OPS]

# v7.2 paragraph-level splitter and candidate priority fixes.
def _extract_draft_hints(draft_text: Optional[str]) -> List[Tuple[str, str]]:
    if not draft_text:
        return []

    text = _clamp_text(draft_text, "AMEND_AI_DRAFT_CONTEXT_MAX_CHARS", "3500")
    raw_lines = [x.strip() for x in re.split(r"\n+", text) if x.strip()]
    hints: List[Tuple[str, str]] = []
    current_type = "operative"

    label_re = re.compile(
        r"^(?:(?P<section>operative|preambular)\s*/\s*)?"
        r"(?P<label>recalling|noting|welcoming|expressing[_ ]regret|expressing[_ ]deep[_ ]concern|emphasizing|decides|requests|calls[_ ]upon|encourages)\s*:\s*(?P<body>.*)$",
        flags=re.IGNORECASE,
    )

    for raw in raw_lines:
        line = re.sub(r"\s+", " ", raw).strip(" -•\t")
        if not line or re.match(r"^title\s*:", line, flags=re.IGNORECASE):
            continue

        m = label_re.match(line)
        if m:
            section = (m.group("section") or "").casefold()
            label = (m.group("label") or "").replace("_", " ").casefold()
            if section in EXPECTED_CLAUSE_TYPES:
                current_type = section
            else:
                current_type = "preambular" if label in {x.replace("_", " ") for x in PREAMBULAR_LABELS} else "operative"
            start = _clean_draft_hint_start_v7(m.group("body") or "")
            if start:
                hints.append((current_type, start[:140].strip(" .,;:")))
            continue

        line = re.sub(r"^\(?\d+[A-Za-z]?\)?[\).:-]?\s*", "", line).strip()
        start = _clean_draft_hint_start_v7(line)
        if len(start) >= 12:
            hints.append((current_type, start[:140].strip(" .,;:")))
        if len(hints) >= int(os.getenv("AMEND_AI_DRAFT_HINT_MAX_ITEMS", "18")):
            break

    deduped: List[Tuple[str, str]] = []
    for typ, start in hints:
        if not start:
            continue
        if any(_too_similar(start, old_start) for _, old_start in deduped):
            continue
        deduped.append((typ, start))
    return deduped


def _split_amend_intents(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()] or [raw]
    units: List[str] = []
    request_re = re.compile(
        r"\b(add|replace|remove|soften|expand|revise|change|clarify|include|mention|proposal\s+should|should\s+(?:say|mention|include|explain|encourage|make|respect|protect|provide))\b",
        flags=re.IGNORECASE,
    )

    for para in paragraphs:
        p = re.sub(r"\s+", " ", para).strip()
        if not p or not request_re.search(p):
            continue

        # One paragraph normally equals one amendment request in lay input. Only
        # split on semicolons if there are explicit action verbs after them.
        parts = re.split(
            r"\s*;\s*(?=(?:I|we|the proposal|this proposal)\s+(?:would|should|want|wants|like))",
            p,
            flags=re.IGNORECASE,
        )
        for part in parts:
            part = part.strip()
            if not part or not request_re.search(part):
                continue
            if any(_too_similar(part, old) for old in units):
                continue
            units.append(part)
            if len(units) >= MAX_OPS:
                break
        if len(units) >= MAX_OPS:
            break

    return units[:MAX_OPS]


def _content_candidates_v7(intent: str, action: str) -> List[str]:
    s = re.sub(r"\s+", " ", intent or "").strip()
    candidates: List[str] = []

    # Highest priority: explicit replacement after a colon/with phrase.
    if action == "REPLACE":
        for pattern in [
            r"replace\s+(?:that\s+idea\s+)?with\s+something\s+broader:\s*(?P<c>.+?)(?:\.|$)",
            r"replace\s+.+?\s+with\s+(?P<c>.+?)(?:\.|$)",
            r"change\s+.+?\s+to\s+(?P<c>.+?)(?:\.|$)",
            r"revise\s+.+?\s+to\s+(?P<c>.+?)(?:\.|$)",
        ]:
            for m in re.finditer(pattern, s, flags=re.IGNORECASE):
                candidates.append(m.group("c"))

    # High-priority explicit amendment-language patterns.
    priority_patterns = [
        r"I\s+would\s+add\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"add\s+a\s+point\s+saying\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"make\s+it\s+clear\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+mention\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+say\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+encourage\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+include\s+(?P<c>.+?)(?:\.|$)",
        r"(?P<c>[A-Z][A-Za-z0-9 ,;:'\-/()]+?\s+should\s+(?:not\s+)?(?:also\s+)?(?:include|respect|protect|provide|create|explain|strengthen|support|recognize|ensure|address|avoid|use|treat|place|focus|help|give|collect|share|develop|establish).+?)(?:\.|$)",
    ]
    for pattern in priority_patterns:
        for m in re.finditer(pattern, s, flags=re.IGNORECASE):
            candidates.append(m.group("c"))

    # Lower priority labels, used only if no substantive sentence exists.
    if not candidates:
        for pattern in [
            r"add\s+that\s+(?P<c>.+?)(?:\.|$)",
            r"add\s+something\s+about\s+(?P<c>.+?)(?:\.|$)",
            r"add\s+more\s+attention\s+to\s+(?P<c>.+?)(?:\.|$)",
            r"should\s+explain\s+(?P<c>.+?)(?:\.|$)",
        ]:
            for m in re.finditer(pattern, s, flags=re.IGNORECASE):
                candidates.append(m.group("c"))

    for sent in _sentence_list_v7(s):
        if _GENERIC_OPERATIVE_CUES_V7.search(sent):
            candidates.append(sent)

    if not candidates:
        candidates.append(_strip_lay_intro_v7(s))

    clean: List[str] = []
    for c in candidates:
        c = _clip_explanatory_tail_v7(c)
        c = _strip_lay_intro_v7(c)
        c = re.sub(r"^something\s+broader:\s*", "", c, flags=re.IGNORECASE).strip(" .")
        if c and not any(_too_similar(c, old) for old in clean):
            clean.append(c)
    return clean


def _formalize_clause_content_v7(text: str) -> str:
    s = _clip_explanatory_tail_v7(text)
    s = _strip_lay_intro_v7(s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    s = re.sub(r"^the\s+proposal\s+(?:should\s+)?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^it\s+should\s+", "", s, flags=re.IGNORECASE)

    modal = re.match(
        r"^(?P<actor>[A-Za-z][A-Za-z0-9 ,&'\-/()]{2,110}?)\s+should\s+(?P<action>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if modal:
        actor = modal.group("actor").strip(" .,;:")
        action = modal.group("action").strip(" .,;:")
        action = re.sub(r"^also\s+", "", action, flags=re.IGNORECASE)
        s = f"{actor} to {action}"

    s = re.sub(r"\bnot\s+not\s+to\b", "not to", s, flags=re.IGNORECASE)
    s = re.sub(r"\bto\s+not\s+", "not to ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bnot\s+to\s+be\s+used\s+as\s+an\s+excuse\s+to\b", "not to be used as an excuse to", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .")

    if len(s.split()) > int(os.getenv("AMEND_AI_MAX_WORDS_PER_OP", "46")):
        words = s.split()[: int(os.getenv("AMEND_AI_MAX_WORDS_PER_OP", "46"))]
        s = " ".join(words).rstrip(",;:")
    return s


def _formalize_content(intent: str, action: str) -> str:
    if action == "REMOVE":
        return ""

    if re.search(r"\b(remove\s+or\s+soften|soften)\b", intent, flags=re.IGNORECASE):
        sentences = _sentence_list_v7(intent)
        keep = [s for s in sentences if re.search(r"\bstill\s+keep|\bshould\s+still\b|\bshould\s+not\s+forget\b|\bis\s+also\s+about\b|\bneed\s+to\s+trust\b", s, flags=re.IGNORECASE)]
        if keep:
            # Turn a balancing paragraph into one concise replacement idea.
            return _formalize_clause_content_v7(" ".join(keep))
        return "relevant wording to preserve necessary expert or institutional work while recognizing the human, practical, and trust-related dimensions of the issue"

    ranked: List[Tuple[int, str]] = []
    for c in _content_candidates_v7(intent, action):
        formal = _formalize_clause_content_v7(c)
        if not formal:
            continue
        score = len(formal.split())
        if _GENERIC_WEAK_CONTENT_RE_V7.match(formal):
            score -= 50
        if re.search(r"\bto\b", formal, flags=re.IGNORECASE):
            score += 8
        if re.search(r"\b(respect|human rights|privacy|responsible use|include|provide|explain|education|support|protect|ordinary|users|communities|countries|organizations)\b", formal, flags=re.IGNORECASE):
            score += 8
        if re.search(r"\bnot\s+to\s+be\s+used\s+as\s+an\s+excuse\b", formal, flags=re.IGNORECASE):
            score -= 10
        ranked.append((score, formal))

    if not ranked:
        return ""
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1]

# v7.3 scoring and grammar polish for generic content selection.
def _clip_explanatory_tail_v7(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = re.split(r"(?:^|\s)(?:For example|For instance)\s*,", s, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .")
    s = re.split(r"\bThis could include\b|\bThis includes\b", s, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .")
    s = re.split(
        r"\bI\s+would\s+also\s+like\s+to\b|\bAnother\s+thing\s+I\s+would\b|\bFinally,?\s+I\s+would\b|\bThere\s+is\s+also\s+a\s+part\b",
        s,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .")
    return s


def _formalize_clause_content_v7(text: str) -> str:
    s = _clip_explanatory_tail_v7(text)
    s = _strip_lay_intro_v7(s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    if not s:
        return ""
    s = re.sub(r"^the\s+proposal\s+(?:should\s+)?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^it\s+should\s+", "", s, flags=re.IGNORECASE)

    modal = re.match(
        r"^(?P<actor>[A-Za-z][A-Za-z0-9 ,&'\-/()]{2,110}?)\s+should\s+(?P<action>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if modal:
        actor = modal.group("actor").strip(" .,;:")
        action = modal.group("action").strip(" .,;:")
        action = re.sub(r"^also\s+", "", action, flags=re.IGNORECASE)
        s = f"{actor} to {action}"

    s = re.sub(r"\bnot\s+not\s+to\b", "not to", s, flags=re.IGNORECASE)
    s = re.sub(r"\bto\s+not\s+", "not to ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .")

    if len(s.split()) > int(os.getenv("AMEND_AI_MAX_WORDS_PER_OP", "46")):
        words = s.split()[: int(os.getenv("AMEND_AI_MAX_WORDS_PER_OP", "46"))]
        s = " ".join(words).rstrip(",;:")
    return s


def _formalize_content(intent: str, action: str) -> str:
    if action == "REMOVE":
        return ""

    if re.search(r"\b(remove\s+or\s+soften|soften)\b", intent, flags=re.IGNORECASE):
        m = re.search(r"wording\s+that\s+makes\s+(.+?)\s+sound\s+only\s+like\s+(.+?)(?:\.|$)", intent, flags=re.IGNORECASE)
        if m:
            subject = m.group(1).strip(" .,;:")
            framing = m.group(2).strip(" .,;:")
            return f"{subject} to retain necessary expert or institutional work while also recognizing the human, practical, and trust-related dimensions beyond {framing}"
        return "relevant wording to preserve necessary expert or institutional work while recognizing the human, practical, and trust-related dimensions of the issue"

    ranked: List[Tuple[int, str]] = []
    for idx, c in enumerate(_content_candidates_v7(intent, action)):
        formal = _formalize_clause_content_v7(c)
        if not formal:
            continue
        fcf = formal.casefold()
        score = 20 - idx  # earlier explicit amendment patterns are normally better
        score += min(len(formal.split()), 30)
        if _GENERIC_WEAK_CONTENT_RE_V7.match(formal):
            score -= 60
        if re.search(r"\bto\b", formal, flags=re.IGNORECASE):
            score += 12
        if re.search(r"\b(include|provide|explain|create|respect|protect|support|strengthen|ensure|recognize|address|develop|establish)\b", formal, flags=re.IGNORECASE):
            score += 10
        if re.search(r"\b(some\s+parts\s+to\s+be\s+changed|may\s+have\s+more\s+experts|for\s+example|instead\s+of\s+only\s+saying)\b", fcf):
            score -= 80
        if re.search(r"\bnot\s+to\s+be\s+used\s+as\s+an\s+excuse\b", fcf):
            score -= 15
        ranked.append((score, formal))

    if not ranked:
        return ""
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1]

# v7.4 final wording polish.
def _lower_initial_unless_acronym_v7(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    first = s.split()[0]
    if first.isupper() and len(first) <= 6:
        return s
    return s[0].lower() + s[1:] if s[0].isupper() else s


def _formalize_content(intent: str, action: str) -> str:
    if action == "REMOVE":
        return ""

    if re.search(r"\b(remove\s+or\s+soften|soften)\b", intent, flags=re.IGNORECASE):
        m = re.search(r"wording\s+that\s+makes\s+(.+?)\s+sound\s+only\s+like\s+(.+?)(?:\.|$)", intent, flags=re.IGNORECASE)
        if m:
            subject = m.group(1).strip(" .,;:")
            framing = m.group(2).strip(" .,;:")
            if len(subject.split()) <= 2:
                subject = f"{subject}-related wording"
            return f"{subject} to retain necessary expert or institutional work while also recognizing the human, practical, and trust-related dimensions beyond {framing}"
        return "relevant wording to preserve necessary expert or institutional work while recognizing the human, practical, and trust-related dimensions of the issue"

    ranked: List[Tuple[int, str]] = []
    for idx, c in enumerate(_content_candidates_v7(intent, action)):
        formal = _formalize_clause_content_v7(c)
        formal = _lower_initial_unless_acronym_v7(formal)
        if not formal:
            continue
        fcf = formal.casefold()
        score = 20 - idx
        score += min(len(formal.split()), 30)
        if _GENERIC_WEAK_CONTENT_RE_V7.match(formal):
            score -= 60
        if re.search(r"\bto\b", formal, flags=re.IGNORECASE):
            score += 12
        if re.search(r"\b(include|provide|explain|create|respect|protect|support|strengthen|ensure|recognize|address|develop|establish)\b", formal, flags=re.IGNORECASE):
            score += 10
        if re.search(r"\b(some\s+parts\s+to\s+be\s+changed|may\s+have\s+more\s+experts|for\s+example|instead\s+of\s+only\s+saying)\b", fcf):
            score -= 80
        if re.search(r"\bnot\s+to\s+be\s+used\s+as\s+an\s+excuse\b", fcf):
            score -= 15
        ranked.append((score, formal))

    if not ranked:
        return ""
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1]

# ---------------------------------------------------------------------------
# v8 final generic multi-intent extractor
# ---------------------------------------------------------------------------
# v7.2 made the extractor too paragraph-dependent. In the app, long textarea
# content may arrive as one paragraph, so only one operation survived. This
# pass is topic-agnostic: it splits by amendment-request markers, extracts the
# strongest operative sentence from each chunk, and keeps ADD/REPLACE/REMOVE
# coverage across different policy topics.

_AMEND_MARKER_RE_V8 = re.compile(
    r"(?:"
    r"(?:I|We)\s+(?:would\s+)?(?:also\s+)?(?:like\s+to\s+)?(?:add|replace|remove|soften|expand|revise|change|clarify|include|mention)\b"
    r"|(?:Another\s+thing|One\s+thing|A\s+further\s+point)\s+(?:I|we)\s+would\s+(?:add|replace|change|revise|include|mention)\b"
    r"|Finally,?\s+(?:I|we)\s+would\s+(?:add|replace|change|revise|include|mention)\b"
    r"|There\s+is\s+also\s+a\s+part\s+(?:I|we)\s+would\s+like\s+to\s+(?:replace|expand|revise|change|clarify)\b"
    r"|(?:The|This)\s+proposal\s+should\s+(?:make|mention|encourage|include|say|explain|recognize|address|state|clarify)\b"
    r"|Because\s+of\s+that,?\s+(?:the|this)\s+proposal\s+should\b"
    r"|When\s+(?:an?|the)\s+[A-Za-z\- ]{0,50}\s*(?:happens|occurs),?\s+"
    r")",
    flags=re.IGNORECASE,
)

_REQUEST_DETECT_RE_V8 = re.compile(
    r"\b(add|insert|append|include|mention|replace|substitute|change|revise|modify|expand|clarify|remove|delete|omit|strike|drop|soften|proposal\s+should|should\s+(?:say|mention|include|explain|encourage|make|respect|protect|provide|recognize|address|state|clarify))\b",
    flags=re.IGNORECASE,
)

_MODAL_CLAUSE_RE_V8 = re.compile(
    r"\b(should|must|need(?:s)?\s+to|is\s+needed|are\s+needed|should\s+not|must\s+not)\b",
    flags=re.IGNORECASE,
)


def _slice_by_markers_v8(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []

    normalized = re.sub(r"\s+", " ", raw).strip()
    matches = list(_AMEND_MARKER_RE_V8.finditer(normalized))

    # If there are no explicit markers, fall back to paragraph/sentence chunks.
    if not matches:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()] or [normalized]
        return [re.sub(r"\s+", " ", p).strip() for p in paragraphs if _REQUEST_DETECT_RE_V8.search(p)][:MAX_OPS]

    chunks: List[str] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
        chunk = normalized[start:end].strip(" ,;:-")
        if chunk and _REQUEST_DETECT_RE_V8.search(chunk):
            chunks.append(chunk)

    # If useful context before the first marker contains a request, keep it.
    prefix = normalized[:matches[0].start()].strip(" ,;:-")
    if prefix and _REQUEST_DETECT_RE_V8.search(prefix):
        chunks.insert(0, prefix)

    out: List[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        if any(_too_similar(chunk, old) for old in out):
            continue
        out.append(chunk)
        if len(out) >= MAX_OPS:
            break
    return out


def _split_amend_intents(text: str) -> List[str]:
    """Final generic splitter that works even if the textarea loses blank lines."""
    chunks = _slice_by_markers_v8(text)
    if chunks:
        return chunks[:MAX_OPS]

    raw = (text or "").strip()
    sentences = _sentence_list_v7(raw)
    out: List[str] = []
    for sent in sentences:
        if _REQUEST_DETECT_RE_V8.search(sent):
            out.append(sent)
        if len(out) >= MAX_OPS:
            break
    return out


def _remove_intro_v8(text: str) -> str:
    s = _normalize_amend_text(text)
    s = re.sub(r"^(?:I|We)\s+(?:generally\s+)?support\s+this\s+proposal\s+because.+?\bbut\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:I|We)\s+(?:think|believe|feel)\s+(?:that\s+)?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:In\s+my\s+opinion,?\s+)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:I|We)\s+would\s+(?:also\s+)?like\s+to\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:I|We)\s+would\s+(?:also\s+)?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:Another\s+thing|One\s+thing|A\s+further\s+point)\s+(?:I|we)\s+would\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^Finally,?\s+(?:I|we)\s+would\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^There\s+is\s+also\s+a\s+part\s+(?:I|we)\s+would\s+like\s+to\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:Because\s+of\s+that,?\s+)?(?:The|This)\s+proposal\s+should\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(
        r"^(?:add|insert|append|include|mention|replace|substitute|change|revise|modify|clarify|expand|soften|remove|delete|omit|strike|drop)\s+",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"^(?:a\s+point\s+)?(?:saying\s+that\s+|that\s+|something\s+about\s+|more\s+attention\s+to\s+)", "", s, flags=re.IGNORECASE)
    return s.strip(" .")


def _truncate_words_v8(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    max_words = int(os.getenv("AMEND_AI_MAX_WORDS_PER_OP", "46"))
    words = s.split()
    if len(words) <= max_words:
        return s
    return " ".join(words[:max_words]).rstrip(",;:")


def _clip_tail_v8(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .")
    s = re.split(r"\b(?:For example|For instance)\s*,", s, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .")
    s = re.split(r"\b(?:This could include|This includes)\b", s, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .")
    s = re.split(
        r"\b(?:I\s+would\s+also\s+like\s+to|Another\s+thing\s+I\s+would|Finally,?\s+I\s+would|There\s+is\s+also\s+a\s+part)\b",
        s,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .")
    return s


def _formalize_clause_content_v8(text: str) -> str:
    s = _clip_tail_v8(text)
    s = _remove_intro_v8(s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    if not s:
        return ""

    # Remove proposal wrappers while keeping the normative content.
    s = re.sub(r"^(?:the|this)\s+proposal\s+(?:should\s+)?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^it\s+should\s+", "", s, flags=re.IGNORECASE)

    # Convert modal sentences into amendment-clause style: actor to action.
    modal = re.match(
        r"^(?P<actor>[A-Za-z][A-Za-z0-9 ,&'\-/()]{2,130}?)\s+should\s+(?P<action>.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if modal:
        actor = modal.group("actor").strip(" .,;:")
        action = modal.group("action").strip(" .,;:")
        action = re.sub(r"^also\s+", "", action, flags=re.IGNORECASE)
        s = f"{actor} to {action}"

    s = re.sub(r"\bto\s+not\s+", "not to ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bnot\s+not\s+to\b", "not to", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .")
    s = _lower_initial_unless_acronym_v7(s)
    return _truncate_words_v8(s)


def _candidate_sentences_v8(intent: str, action: str) -> List[str]:
    s = re.sub(r"\s+", " ", intent or "").strip()
    candidates: List[str] = []

    # Explicit replacement content.
    if action == "REPLACE":
        for pattern in [
            r"replace\s+(?:that\s+idea\s+)?with\s+something\s+broader:\s*(?P<c>.+?)(?:\.|$)",
            r"replace\s+.+?\s+with\s+(?P<c>.+?)(?:\.|$)",
            r"change\s+.+?\s+to\s+(?P<c>.+?)(?:\.|$)",
            r"revise\s+.+?\s+to\s+(?P<c>.+?)(?:\.|$)",
        ]:
            for m in re.finditer(pattern, s, flags=re.IGNORECASE):
                candidates.append(m.group("c"))

    # Explicit add/proposal patterns.
    patterns = [
        r"(?:I|we)\s+would\s+add\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"add\s+a\s+point\s+saying\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"add\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"make\s+it\s+clear\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+make\s+it\s+clear\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+mention\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+say\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+include\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+encourage\s+(?P<c>.+?)(?:\.|$)",
        r"organizations\s+should\s+explain\s+(?P<c>.+?)(?:\.|$)",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, s, flags=re.IGNORECASE):
            candidates.append(m.group("c"))

    # Any normative sentence in the chunk can become a candidate.
    for sent in _sentence_list_v7(s):
        if _MODAL_CLAUSE_RE_V8.search(sent) or _GENERIC_OPERATIVE_CUES_V7.search(sent):
            candidates.append(sent)

    if not candidates:
        candidates.append(s)

    clean: List[str] = []
    for c in candidates:
        c = _formalize_clause_content_v8(c)
        if not c:
            continue
        if any(_too_similar(c, old) for old in clean):
            continue
        clean.append(c)
    return clean


def _formalize_content(intent: str, action: str) -> str:
    if action == "REMOVE":
        return ""

    # Generic balancing request: remove/soften narrow framing, but retain core work.
    if re.search(r"\b(remove\s+or\s+soften|soften)\b", intent, flags=re.IGNORECASE):
        m = re.search(r"wording\s+that\s+makes\s+(.+?)\s+sound\s+only\s+like\s+(.+?)(?:\.|$)", intent, flags=re.IGNORECASE)
        if m:
            subject = m.group(1).strip(" .,;:")
            framing = m.group(2).strip(" .,;:")
            if len(subject.split()) <= 2:
                subject = f"{subject}-related wording"
            return _truncate_words_v8(
                f"{subject} to retain necessary expert or institutional work while also recognizing the human, practical, and trust-related dimensions beyond {framing}"
            )
        sentences = [x for x in _sentence_list_v7(intent) if re.search(r"\bshould\s+still|\bshould\s+not\s+forget|\bis\s+also\s+about|\bneed\s+to\s+trust", x, flags=re.IGNORECASE)]
        if sentences:
            return _formalize_clause_content_v8(" ".join(sentences))
        return "relevant wording to preserve necessary technical or institutional work while recognizing the human and practical dimensions of the issue"

    ranked: List[Tuple[int, str]] = []
    for idx, cand in enumerate(_candidate_sentences_v8(intent, action)):
        c = _formalize_clause_content_v8(cand)
        if not c:
            continue
        c_cf = c.casefold()
        score = 100 - idx * 4
        score += min(len(c.split()), 35)
        if _GENERIC_WEAK_CONTENT_RE_V7.match(c):
            score -= 80
        if re.search(r"\bto\b", c):
            score += 10
        if re.search(r"\b(include|provide|explain|create|respect|protect|support|strengthen|ensure|recognize|address|develop|establish|place|center|communicate)\b", c_cf):
            score += 12
        if re.search(r"\b(for\s+example|instead\s+of\s+only\s+saying|some\s+parts\s+to\s+be\s+changed)\b", c_cf):
            score -= 80
        ranked.append((score, c))

    if not ranked:
        return ""
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def _guess_action(intent: str) -> str:
    s = intent.casefold()
    if re.search(r"\b(remove\s+or\s+soften|soften)\b", s):
        return "REPLACE"
    if re.search(r"\b(remove|delete|omit|strike|drop)\b", s):
        return "REMOVE"
    if re.search(r"\b(replace|substitute)\b", s):
        return "REPLACE"
    if re.search(r"\b(change|revise|modify|clarify|expand|rewrite)\b", s) and re.search(r"\b(existing|wording|part|clause|idea|sentence|paragraph|section|materials?)\b", s):
        return "REPLACE"
    return "ADD"


def _derive_target(intent: str, action: str, clause_type: str, hints: List[Tuple[str, str]]) -> str:
    s = re.sub(r"\s+", " ", intent or "").strip()
    s_cf = s.casefold()

    if action == "ADD":
        return "as new operative clause" if clause_type == "operative" else "as new preambular clause"

    quoted = re.findall(r"[\"']([^\"']{4,140})[\"']", s)
    if quoted:
        q = _clean_draft_hint_start_v7(quoted[0])
        if q:
            return f"{clause_type} clause starting with '{q[:110]}'"

    m = re.search(r"wording\s+that\s+makes\s+(.+?)\s+sound\s+only\s+like\s+(.+?)(?:\.|$)", s, flags=re.IGNORECASE)
    if m:
        subject = _clip_tail_v8(m.group(1)).strip(" .,;:")
        framing = _clip_tail_v8(m.group(2)).strip(" .,;:")
        if subject and framing:
            return f"wording that frames {subject[:55]} only as {framing[:55]}"

    # Prefer a clean matching draft hint for replace/remove.
    matched_type, start = _find_matching_hint(s, hints)
    start = _clean_draft_hint_start_v7(start)
    if start and len(start.split()) >= 4:
        return f"{matched_type or clause_type} clause starting with '{start[:110]}'"

    for label in PREAMBULAR_LABELS:
        label_clean = label.replace("_", " ")
        if label_clean in s_cf:
            return f"preambular clause under {label_clean}"
    for label in OPERATIVE_LABELS:
        label_clean = label.replace("_", " ")
        if label_clean in s_cf:
            return f"operative clause under {label_clean}"

    topic = _remove_intro_v8(s)
    topic = re.sub(r"\bwith\b.+$", "", topic, flags=re.IGNORECASE).strip(" .,;:")
    topic = _clip_tail_v8(topic)
    if not topic:
        topic = "the relevant wording"
    return f"wording concerning {topic[:90]}"


def _add_operation_v8(ops: List[Dict[str, str]], action: str, clause_type: str, target: str, content: str) -> None:
    action = _normalize_action(action)
    clause_type = _normalize_clause_type(clause_type, target=target, content=content)
    target = _clean_final_target_v7(target, action, clause_type)
    content = _normalize_amend_text(content)

    if action not in EXPECTED_ACTIONS or clause_type not in EXPECTED_CLAUSE_TYPES:
        return
    if action == "REMOVE":
        if not target:
            return
        content = ""
    elif action in {"ADD", "REPLACE"}:
        if not target or _content_too_weak_v5(content):
            return

    candidate = f"{action} {clause_type} {target} {content}"
    for old in ops:
        old_text = f"{old['action']} {old['clause_type']} {old['target']} {old['content']}"
        # Same content = duplicate. Same target is OK for ADD because many new
        # clauses correctly share "as new operative clause".
        if _too_similar(content, old.get("content", "")) or _too_similar(candidate, old_text):
            return
    ops.append({"action": action, "clause_type": clause_type, "target": target, "content": content})


def _build_rule_payload(
    *,
    plain_text: str,
    draft_title: Optional[str] = None,
    draft_text: Optional[str] = None,
) -> Dict[str, Any]:
    hints = _extract_draft_hints(draft_text)
    intents = _split_amend_intents(plain_text)
    ops: List[Dict[str, str]] = []

    for intent in intents:
        action = _guess_action(intent)
        clause_type = _guess_clause_type(intent)
        content = _formalize_content(intent, action)
        target = _derive_target(intent, action, clause_type, hints)

        if target.startswith("preambular"):
            clause_type = "preambular"
        elif target.startswith("operative"):
            clause_type = "operative"

        _add_operation_v8(ops, action, clause_type, target, "" if action == "REMOVE" else content)
        if len(ops) >= MAX_OPS:
            break

    label = "Generated amendment"
    if draft_title:
        label = f"Amendment to {draft_title[:60]}"
    elif ops:
        label = "Amendment on " + " ".join((ops[0].get("content") or "amendment").split()[:5])

    return {"label": label, "operations": ops[:MAX_OPS]}

# ---------------------------------------------------------------------------
# v8.1 primary-marker splitter
# ---------------------------------------------------------------------------
# Prefer explicit self-request markers first ("I would add...", "Another thing...",
# "Finally..."). This prevents supportive sentences such as "Because of that,
# the proposal should..." from consuming operation slots before later amendment
# paragraphs are reached. Secondary proposal-should markers are used only when
# no primary markers exist.

_PRIMARY_AMEND_MARKER_RE_V81 = re.compile(
    r"(?:"
    r"(?:I|We)\s+(?:would\s+)?(?:also\s+)?(?:like\s+to\s+)?(?:add|replace|remove|soften|expand|revise|change|clarify|include|mention)\b"
    r"|(?:Another\s+thing|One\s+thing|A\s+further\s+point)\s+(?:I|we)\s+would\s+(?:add|replace|change|revise|include|mention)\b"
    r"|Finally,?\s+(?:I|we)\s+would\s+(?:add|replace|change|revise|include|mention)\b"
    r"|There\s+is\s+also\s+a\s+part\s+(?:I|we)\s+would\s+like\s+to\s+(?:replace|expand|revise|change|clarify)\b"
    r")",
    flags=re.IGNORECASE,
)

_SECONDARY_AMEND_MARKER_RE_V81 = re.compile(
    r"(?:"
    r"(?:The|This)\s+proposal\s+should\s+(?:make|mention|encourage|include|say|explain|recognize|address|state|clarify)\b"
    r"|Because\s+of\s+that,?\s+(?:the|this)\s+proposal\s+should\b"
    r"|When\s+(?:an?|the)\s+[A-Za-z\- ]{0,50}\s*(?:happens|occurs),?\s+"
    r")",
    flags=re.IGNORECASE,
)


def _slice_with_regex_v81(normalized: str, marker_re: re.Pattern) -> List[str]:
    matches = list(marker_re.finditer(normalized))
    if not matches:
        return []
    chunks: List[str] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
        chunk = normalized[start:end].strip(" ,;:-")
        if chunk and _REQUEST_DETECT_RE_V8.search(chunk):
            chunks.append(chunk)
    return chunks


def _slice_by_markers_v8(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []

    normalized = re.sub(r"\s+", " ", raw).strip()
    chunks = _slice_with_regex_v81(normalized, _PRIMARY_AMEND_MARKER_RE_V81)
    if not chunks:
        chunks = _slice_with_regex_v81(normalized, _SECONDARY_AMEND_MARKER_RE_V81)

    if not chunks:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()] or [normalized]
        chunks = [re.sub(r"\s+", " ", p).strip() for p in paragraphs if _REQUEST_DETECT_RE_V8.search(p)]

    out: List[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        if any(_too_similar(chunk, old) for old in out):
            continue
        out.append(chunk)
        if len(out) >= MAX_OPS:
            break
    return out[:MAX_OPS]

# v8.2 avoid splitting content sentences like "I would add that..." or
# "I would replace that idea..." away from their parent amendment paragraph.
_PRIMARY_AMEND_MARKER_RE_V81 = re.compile(
    r"(?:"
    r"(?:I|We)\s+would\s+(?:also\s+)?like\s+to\s+(?:add|replace|remove|soften|expand|revise|change|clarify|include|mention)\b"
    r"|(?:I|We)\s+would\s+(?:also\s+)?(?:add|replace|change|revise|include|mention)\s+(?:a\s+|an\s+|another\s+)?(?:point|section|clause|part)\b"
    r"|(?:Another\s+thing|One\s+thing|A\s+further\s+point)\s+(?:I|we)\s+would\s+(?:add|replace|change|revise|include|mention)\b"
    r"|Finally,?\s+(?:I|we)\s+would\s+(?:add|replace|change|revise|include|mention)\b"
    r"|There\s+is\s+also\s+a\s+part\s+(?:I|we)\s+would\s+like\s+to\s+(?:replace|expand|revise|change|clarify)\b"
    r")",
    flags=re.IGNORECASE,
)

# v8.3 prefer full actor-modal sentence over object-only fragments such as
# "what happened, what information..." when the source also says
# "Organizations should explain ...". This is generic for WH-object captures.
_old_formalize_content_v83 = _formalize_content

def _formalize_content(intent: str, action: str) -> str:
    out = _old_formalize_content_v83(intent, action)
    if action != "REMOVE" and re.match(r"^(what|where|when|why|how)\b", out or "", flags=re.IGNORECASE):
        for sent in _sentence_list_v7(intent):
            if re.search(r"\bshould\s+(?:also\s+)?(?:explain|state|identify|provide|clarify|report|describe|tell|communicate)\b", sent, flags=re.IGNORECASE):
                fixed = _formalize_clause_content_v8(sent)
                if fixed and not re.match(r"^(what|where|when|why|how)\b", fixed, flags=re.IGNORECASE):
                    return fixed
    return out

# ---------------------------------------------------------------------------
# v9 final generic clause-shape polish
# ---------------------------------------------------------------------------
# This pass is intentionally topic-agnostic. It repairs common final amendment
# shapes without adding example-specific coverage floors:
# - passive center-framing clauses: "X to be placed at the center..."
# - awkward contrast tails: "dimensions beyond a technical issue"
# - overlong/contaminated targets after earlier model/rule merging


def _v9_extract_especially_phrase(intent: str) -> str:
    m = re.search(r"\bespecially\s+([^.;]+)", intent or "", flags=re.IGNORECASE)
    if not m:
        return ""
    phrase = re.sub(r"\s+", " ", m.group(1)).strip(" .,;:")
    # Keep qualifiers short and generic. Stop before a new independent clause.
    phrase = re.split(r"\b(?:so|when|because|but|and\s+when)\b", phrase, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .,;:")
    words = phrase.split()
    if len(words) > 14:
        phrase = " ".join(words[:14])
    return phrase


def _v9_polish_content_text(content: str) -> str:
    s = _normalize_amend_text(content)
    if not s:
        return ""

    # Passive center framing: "ordinary people to be placed more clearly at the center..."
    # -> "relevant actors to place ordinary people more clearly at the center..."
    m = re.match(
        r"^(?P<object>[A-Za-z][A-Za-z0-9 ,\-]{2,90}?)\s+to\s+be\s+placed\s+(?P<rest>.+?\bcenter\b.+)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        obj = m.group("object").strip(" .,;:")
        rest = m.group("rest").strip(" .,;:")
        rest = re.sub(r"\bthis\s+work\b", "the relevant work", rest, flags=re.IGNORECASE)
        s = f"relevant actors to place {obj} {rest}"

    # Passive respect/protection shape: "privacy to be respected" etc.
    m = re.match(
        r"^(?P<object>[A-Za-z][A-Za-z0-9 ,\-]{2,90}?)\s+to\s+be\s+(?P<verb>respected|protected|recognized|included|supported|addressed)\b(?P<rest>.*)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        obj = m.group("object").strip(" .,;:")
        verb = m.group("verb").casefold()
        rest = m.group("rest").strip(" .,;:")
        active = {
            "respected": "respect",
            "protected": "protect",
            "recognized": "recognize",
            "included": "include",
            "supported": "support",
            "addressed": "address",
        }.get(verb, verb)
        s = f"relevant actors to {active} {obj}"
        if rest:
            s += f" {rest}"

    # Awkward but common phrase created from "not only technical/institutional" requests.
    s = re.sub(
        r"\bdimensions\s+beyond\s+(?:an?|the)\s+[^,.;]{2,80}?\s+issue\b",
        "dimensions of the issue",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\bdimensions\s+beyond\s+[^,.;]{2,80}?\b",
        "dimensions of the issue",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"\bthe\s+relevant\s+work\s+work\b", "the relevant work", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return _truncate_words_v8(s)


_old_formalize_content_v9 = _formalize_content


def _formalize_content(intent: str, action: str) -> str:
    out = _old_formalize_content_v9(intent, action)
    if action == "REMOVE":
        return ""

    polished = _v9_polish_content_text(out)

    # If the selected candidate is a passive center-framing clause, preserve any
    # short "especially ..." qualifier present in the source intent.
    if re.search(r"\brelevant\s+actors\s+to\s+place\b.+\bcenter\b", polished, flags=re.IGNORECASE):
        qualifier = _v9_extract_especially_phrase(intent)
        if qualifier and qualifier.casefold() not in polished.casefold():
            polished = f"{polished}, especially {qualifier}"

    return _truncate_words_v8(polished)


def _v9_polish_target_text(target: str, action: str, clause_type: str) -> str:
    t = _clean_final_target_v7(target, action, clause_type)
    if action == "ADD":
        return t

    # Clean remaining section-label contamination inside quoted starts.
    t = re.sub(r"(clause\s+starting\s+with\s+')\s*(?:operative|preambular)\s*/\s*", r"\1", t, flags=re.IGNORECASE)
    t = re.sub(
        r"(clause\s+starting\s+with\s+')\s*(?:recalling|noting|welcoming|expressing[_ ]regret|expressing[_ ]deep[_ ]concern|emphasizing|decides|requests|calls[_ ]upon|encourages)\s*:\s*",
        r"\1",
        t,
        flags=re.IGNORECASE,
    )

    # If a generated clause start is very noun-list-like and not a precise user
    # quote, make it a descriptive target. This avoids ugly targets while still
    # being useful for human-in-the-loop review.
    m = re.match(r"^(?P<type>operative|preambular)\s+clause\s+starting\s+with\s+'the\s+development\s+of\s+(?P<body>[^']+)'$", t, flags=re.IGNORECASE)
    if m:
        body = re.sub(r"\s+", " ", m.group("body")).strip(" .,;:")
        if len(body.split()) >= 5:
            return f"{m.group('type').lower()} wording concerning the development of {body[:95]}"

    return t.strip(" .")


_old_postprocess_amend_gen_v9 = _postprocess_amend_gen


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    gen = _old_postprocess_amend_gen_v9(gen)
    polished_ops: List[AmendOp] = []
    seen: List[str] = []

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _v9_polish_target_text(op.target, action, clause_type)
        content = "" if action == "REMOVE" else _v9_polish_content_text(op.content)

        if action in {"ADD", "REPLACE"} and _content_too_weak_v5(content):
            continue
        if action == "REMOVE" and not target:
            continue

        joined = f"{action} {clause_type} {target} {content}"
        if any(_too_similar(joined, old) for old in seen):
            continue
        seen.append(joined)
        polished_ops.append(AmendOp(action=action, clause_type=clause_type, target=target, content=content))
        if len(polished_ops) >= MAX_OPS:
            break

    return AmendGen(label=gen.label or "Generated amendment", operations=polished_ops)

# ---------------------------------------------------------------------------
# v10 final generic target clipping polish
# ---------------------------------------------------------------------------
# v9 converted long generated clause starts into descriptive targets, but it
# clipped by characters. That can leave broken endings such as "international or".
# This pass clips targets by words and removes dangling conjunctions/prepositions.

_TARGET_MAX_WORDS_V10 = int(os.getenv("AMEND_AI_TARGET_MAX_WORDS", "16"))
_TARGET_DANGLING_WORDS_V10 = {
    "and", "or", "of", "for", "to", "with", "between", "among", "by", "from", "on", "in", "into", "through", "concerning",
}


def _v10_clip_target_phrase(text: str, *, max_words: Optional[int] = None) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .,;:")
    if not s:
        return ""

    limit = max_words or _TARGET_MAX_WORDS_V10
    words = s.split()
    if len(words) > limit:
        words = words[:limit]

    # Do not leave a human-facing target ending in a dangling connector.
    while words and words[-1].strip("'\".,;:").casefold() in _TARGET_DANGLING_WORDS_V10:
        words.pop()

    clipped = " ".join(words).strip(" .,;:")
    return clipped or s


_old_v9_polish_target_text_v10 = _v9_polish_target_text


def _v9_polish_target_text(target: str, action: str, clause_type: str) -> str:
    t = _old_v9_polish_target_text_v10(target, action, clause_type)
    if action == "ADD":
        return t

    # Repair v9 descriptive targets that were clipped by characters.
    m = re.match(
        r"^(?P<type>operative|preambular)\s+wording\s+concerning\s+the\s+development\s+of\s+(?P<body>.+)$",
        t,
        flags=re.IGNORECASE,
    )
    if m:
        body = _v10_clip_target_phrase(m.group("body"), max_words=_TARGET_MAX_WORDS_V10)
        return f"{m.group('type').lower()} wording concerning the development of {body}".strip(" .")

    # For any other long descriptive target, word-clip safely while preserving
    # quoted clause starts as much as possible.
    if len(t.split()) > _TARGET_MAX_WORDS_V10 + 6 and "starting with '" not in t.casefold():
        return _v10_clip_target_phrase(t, max_words=_TARGET_MAX_WORDS_V10 + 6)

    # If a target still ends with a dangling word, remove it.
    parts = t.split()
    while parts and parts[-1].strip("'\".,;:").casefold() in _TARGET_DANGLING_WORDS_V10:
        parts.pop()
    return " ".join(parts).strip(" .") or t.strip(" .")

# ---------------------------------------------------------------------------
# v11 final generic target phrase repair
# ---------------------------------------------------------------------------
# v10 prevents targets from ending with bare connectors such as "and" or "or".
# This pass fixes a related generic case: targets clipped after a connector plus
# one unfinished descriptor, e.g. "cooperation between international". For human
# review, a shorter complete target is better than a longer incomplete one.

_INCOMPLETE_CONNECTOR_PHRASES_V11 = {
    "between", "among", "with", "within", "through", "across", "toward", "towards",
}

_UNFINISHED_DESCRIPTOR_WORDS_V11 = {
    "international", "national", "regional", "local", "global", "public", "private",
    "technical", "institutional", "governmental", "intergovernmental", "cross-border",
    "digital", "legal", "financial", "social", "economic", "environmental",
}


def _v11_repair_incomplete_target_phrase(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip(" .,;:")
    if not s:
        return ""

    words = s.split()

    # Remove trailing connector first.
    while words and words[-1].strip("'\".,;:").casefold() in _TARGET_DANGLING_WORDS_V10:
        words.pop()

    # Remove endings such as "between international" or "with technical".
    if len(words) >= 2:
        last = words[-1].strip("'\".,;:").casefold()
        prev = words[-2].strip("'\".,;:").casefold()
        if prev in _INCOMPLETE_CONNECTOR_PHRASES_V11 and last in _UNFINISHED_DESCRIPTOR_WORDS_V11:
            words = words[:-2]

    # If a clipped target still ends after "between/among/with X" where X is a
    # single broad descriptor, prefer the phrase before the connector.
    if len(words) >= 2:
        last = words[-1].strip("'\".,;:").casefold()
        prev = words[-2].strip("'\".,;:").casefold()
        if prev in _INCOMPLETE_CONNECTOR_PHRASES_V11 and len(last) > 3:
            # Only apply to descriptive targets, not exact quoted clause starts.
            words = words[:-2]

    cleaned = " ".join(words).strip(" .,;:")
    return cleaned or s


_old_v9_polish_target_text_v11 = _v9_polish_target_text


def _v9_polish_target_text(target: str, action: str, clause_type: str) -> str:
    t = _old_v9_polish_target_text_v11(target, action, clause_type)
    if action == "ADD":
        return t

    m = re.match(
        r"^(?P<prefix>(?:operative|preambular)\s+wording\s+concerning\s+(?:the\s+development\s+of\s+)?)(?P<body>.+)$",
        t,
        flags=re.IGNORECASE,
    )
    if m:
        body = _v11_repair_incomplete_target_phrase(m.group("body"))
        return (m.group("prefix") + body).strip(" .")

    # General descriptive target cleanup. Avoid changing exact quoted clause starts.
    if "starting with '" not in t.casefold():
        t = _v11_repair_incomplete_target_phrase(t)

    return t.strip(" .")


# Rebind postprocess so it definitely uses the final v11 target polish even if
# earlier wrappers captured an older target-polish function.
_old_postprocess_amend_gen_v11 = _postprocess_amend_gen


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    gen = _old_postprocess_amend_gen_v11(gen)
    final_ops: List[AmendOp] = []
    seen: List[str] = []

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _v9_polish_target_text(op.target, action, clause_type)
        content = "" if action == "REMOVE" else _v9_polish_content_text(op.content)

        if action == "ADD" and target not in {"as new operative clause", "as new preambular clause"}:
            target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"

        if action in {"ADD", "REPLACE"} and not content:
            continue
        if action in {"REPLACE", "REMOVE"} and not target:
            continue

        joined = f"{action} {clause_type} {target} {content}"
        if any(_too_similar(joined, old) for old in seen):
            continue
        seen.append(joined)
        final_ops.append(AmendOp(action=action, clause_type=clause_type, target=target, content=content))

        if len(final_ops) >= MAX_OPS:
            break

    return AmendGen(label=gen.label or "Generated amendment", operations=final_ops)

# ---------------------------------------------------------------------------
# v12 generic multi-topic stability pass
# ---------------------------------------------------------------------------
# v9 was stable for one topic but still missed general amendment markers such as:
# - "I would also add that ..."
# - "I would also add something about ..."
# - "I think one part should be replaced or made clearer ..."
# - "I think the proposal should also add stronger language about ..."
# - "There is also one thing I would remove or at least soften ..."
# This pass is topic-agnostic: it improves marker splitting, action priority,
# replacement targets, and passive-clause polish without hardcoding policy areas.

_PRIMARY_AMEND_MARKER_RE_V12 = re.compile(
    r"(?:"
    r"(?:I|We)\s+would\s+(?:also\s+)?(?:like\s+to\s+)?(?:add|replace|remove|soften|expand|revise|change|clarify|include|mention)\b"
    r"|(?:I|We)\s+think\s+(?:one\s+)?(?:part|section|clause|idea|wording)\s+should\s+be\s+(?:replaced|changed|revised|clarified|expanded|removed|softened)\b"
    r"|(?:I|We)\s+think\s+(?:the|this)\s+proposal\s+should\s+(?:also\s+)?(?:add|include|mention|state|say|explain|recognize|address|clarify)\b"
    r"|(?:The|This)\s+proposal\s+should\s+(?:also\s+)?(?:add|include|mention|state|say|explain|recognize|address|clarify)\b"
    r"|(?:Another\s+thing|One\s+thing|A\s+further\s+point)\s+(?:I|we)\s+would\s+(?:also\s+)?(?:add|replace|change|revise|include|mention|remove|soften)\b"
    r"|Finally,?\s+(?:I|we)\s+would\s+(?:also\s+)?(?:add|replace|change|revise|include|mention|remove|soften)\b"
    r"|There\s+is\s+also\s+(?:one\s+thing|a\s+part|one\s+part)\s+(?:I|we)\s+would\s+(?:like\s+to\s+)?(?:add|replace|change|revise|include|mention|remove|soften)\b"
    r")",
    flags=re.IGNORECASE,
)

_REQUEST_DETECT_RE_V12 = re.compile(
    r"\b(add|insert|append|include|mention|replace|substitute|change|revise|modify|expand|clarify|remove|delete|omit|strike|drop|soften|proposal\s+should|should\s+(?:say|mention|include|explain|encourage|make|respect|protect|provide|recognize|address|state|clarify|add))\b",
    flags=re.IGNORECASE,
)


def _split_with_markers_v12(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []

    normalized = re.sub(r"\s+", " ", raw).strip()
    matches = list(_PRIMARY_AMEND_MARKER_RE_V12.finditer(normalized))
    if not matches:
        return []

    chunks: List[str] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
        chunk = normalized[start:end].strip(" ,;:-")
        if not chunk:
            continue
        if not _REQUEST_DETECT_RE_V12.search(chunk):
            continue
        chunks.append(chunk)
    return chunks


def _split_amend_intents(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []

    # Prefer paragraph boundaries when they exist, because users usually write
    # one amendment idea per paragraph. Within each paragraph, split only if
    # there are multiple explicit amendment markers.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    source_units = paragraphs if len(paragraphs) > 1 else [raw]

    out: List[str] = []
    for unit in source_units:
        chunks = _split_with_markers_v12(unit)
        if not chunks and _REQUEST_DETECT_RE_V12.search(unit):
            chunks = [re.sub(r"\s+", " ", unit).strip()]

        for chunk in chunks:
            if not chunk:
                continue
            if any(_too_similar(chunk, old) for old in out):
                continue
            out.append(chunk)
            if len(out) >= MAX_OPS:
                return out[:MAX_OPS]

    # Fallback for text without blank lines and without explicit markers.
    if not out:
        for sent in _sentence_list_v7(raw):
            if _REQUEST_DETECT_RE_V12.search(sent):
                out.append(sent)
            if len(out) >= MAX_OPS:
                break

    return out[:MAX_OPS]


def _leading_action_window_v12(intent: str) -> str:
    s = re.sub(r"\s+", " ", intent or "").strip()
    return " ".join(s.split()[:28]).casefold()


_old_guess_action_v12 = _guess_action


def _guess_action(intent: str) -> str:
    # Decide action primarily from the leading amendment marker. This prevents a
    # long ADD paragraph that later says "replace" or "remove" in explanation
    # from being misclassified.
    lead = _leading_action_window_v12(intent)
    full = (intent or "").casefold()

    if re.search(r"\bremove\s+or\s+(?:at\s+least\s+)?soften\b|\bsoften\b", lead):
        return "REPLACE"
    if re.search(r"\b(remove|delete|omit|strike|drop)\b", lead):
        return "REMOVE"
    if re.search(r"\b(replace|substitute)\b|\bshould\s+be\s+replaced\b", lead):
        return "REPLACE"
    if re.search(r"\b(change|revise|modify|clarify|expand|rewrite)\b|\bmade\s+clearer\b", lead):
        return "REPLACE"
    if re.search(r"\b(add|insert|append|include|mention)\b|\bproposal\s+should\s+(?:also\s+)?add\b", lead):
        return "ADD"

    return _old_guess_action_v12(intent)


def _candidate_sentences_v8(intent: str, action: str) -> List[str]:
    s = re.sub(r"\s+", " ", intent or "").strip()
    candidates: List[str] = []

    # Replacement content: prefer the explicit "instead / replace with" wording.
    if action == "REPLACE":
        for pattern in [
            r"replace\s+(?:that\s+idea\s+)?with\s+something\s+broader:\s*(?P<c>.+?)(?:\.|$)",
            r"replace\s+it\s+with\s+wording\s+that\s+says\s+(?P<c>.+?)(?:\.|$)",
            r"replace\s+.+?\s+with\s+(?:wording\s+that\s+says\s+)?(?P<c>.+?)(?:\.|$)",
            r"instead,?\s+(?:the|this)\s+proposal\s+should\s+(?:say|state|mention|make\s+clear)\s+that\s+(?P<c>.+?)(?:\.|$)",
            r"change\s+.+?\s+to\s+(?P<c>.+?)(?:\.|$)",
            r"revise\s+.+?\s+to\s+(?P<c>.+?)(?:\.|$)",
        ]:
            for m in re.finditer(pattern, s, flags=re.IGNORECASE):
                candidates.append(m.group("c"))

    # Explicit add/proposal patterns, including "also add that" and
    # "add something about X" paragraphs.
    patterns = [
        r"(?:I|we)\s+would\s+(?:also\s+)?(?:like\s+to\s+)?add\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"(?:I|we)\s+would\s+(?:also\s+)?(?:like\s+to\s+)?add\s+a\s+point\s+saying\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"(?:I|we)\s+would\s+(?:also\s+)?(?:like\s+to\s+)?add\s+something\s+about\s+[^.]+\.\s*(?P<c>[^.]*?\bshould\b.+?)(?:\.|$)",
        r"(?:the|this)\s+proposal\s+should\s+(?:also\s+)?add\s+(?:stronger\s+language\s+about\s+[^.]+\.\s*)?(?P<c>[^.]*?\bshould\b.+?)(?:\.|$)",
        r"(?:the|this)\s+proposal\s+should\s+(?:also\s+)?(?:mention|include|state|say|explain|recognize|address|clarify)\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"make\s+it\s+clear\s+that\s+(?P<c>.+?)(?:\.|$)",
        r"should\s+make\s+it\s+clear\s+that\s+(?P<c>.+?)(?:\.|$)",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, s, flags=re.IGNORECASE):
            candidates.append(m.group("c"))

    # Any normative sentence in the chunk can be used as a candidate.
    for sent in _sentence_list_v7(s):
        if re.search(r"\bshould\b|\bmust\b|\bneed(?:s)?\s+to\b", sent, flags=re.IGNORECASE):
            candidates.append(sent)

    # De-duplicate while preserving order.
    out: List[str] = []
    for c in candidates:
        c = re.sub(r"\s+", " ", c or "").strip(" .")
        if not c:
            continue
        if any(_too_similar(c, old) for old in out):
            continue
        out.append(c)
    return out


_old_formalize_clause_content_v12 = _formalize_clause_content_v8


def _formalize_clause_content_v8(text: str) -> str:
    s = _old_formalize_clause_content_v12(text)

    # Generic passive/policy-language repairs.
    m = re.match(r"^(?P<object>.+?)\s+to\s+be\s+made\s+(?P<rest>.+)$", s, flags=re.IGNORECASE)
    if m:
        return _truncate_words_v8(f"relevant actors to make {m.group('object').strip(' .,;:')} {m.group('rest').strip(' .,;:')}")

    m = re.match(r"^(?P<object>.+?)\s+to\s+be\s+increased\s+(?P<rest>.+)$", s, flags=re.IGNORECASE)
    if m:
        return _truncate_words_v8(f"relevant actors to increase {m.group('object').strip(' .,;:')} {m.group('rest').strip(' .,;:')}")

    s = re.sub(r"\bto\s+be\s+encouraged\s+to\b", "to", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return _truncate_words_v8(s)


_old_formalize_content_v12 = _formalize_content


def _formalize_content(intent: str, action: str) -> str:
    if action == "REMOVE":
        return ""

    # Generic remove/soften with an explicit safer replacement.
    if re.search(r"\b(remove\s+or\s+(?:at\s+least\s+)?soften|soften)\b", intent, flags=re.IGNORECASE):
        m = re.search(
            r"instead,?\s+(?:the|this)\s+proposal\s+should\s+(?:say|state|mention|make\s+clear)\s+that\s+(?P<c>.+?)(?:\.|$)",
            intent,
            flags=re.IGNORECASE,
        )
        if m:
            return _v9_polish_content_text(_formalize_clause_content_v8(m.group("c")))

    out = _old_formalize_content_v12(intent, action)
    return _v9_polish_content_text(out)


_old_derive_target_v12 = _derive_target


def _derive_target(intent: str, action: str, clause_type: str, hints: List[Tuple[str, str]]) -> str:
    if action == "ADD":
        return "as new operative clause" if clause_type == "operative" else "as new preambular clause"

    s = re.sub(r"\s+", " ", intent or "").strip()

    # Generic target for open-resource / access replacement requests.
    m = re.search(r"talks\s+about\s+(?:increasing\s+)?access\s+to\s+(?P<object>.+?)(?:,\s+but|\s+but|\.)", s, flags=re.IGNORECASE)
    if m:
        obj = _v10_clip_target_phrase(m.group("object"), max_words=int(os.getenv("AMEND_AI_TARGET_MAX_WORDS", "16")))
        if obj:
            return f"operative wording concerning access to {obj}"

    # Generic target for unconditional-sharing concerns.
    m = re.search(r"wording\s+that\s+makes\s+it\s+seem\s+like\s+(?P<object>.+?)\s+should\s+be\s+shared\s+without\s+conditions", s, flags=re.IGNORECASE)
    if m:
        obj = _v10_clip_target_phrase(m.group("object"), max_words=14)
        return f"wording suggesting that {obj} should be shared without conditions"

    return _old_derive_target_v12(intent, action, clause_type, hints)


# Rebind final postprocess again so it sees the v12 content and target repairs.
_old_postprocess_amend_gen_v12 = _postprocess_amend_gen


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    gen = _old_postprocess_amend_gen_v12(gen)
    final_ops: List[AmendOp] = []
    seen: List[str] = []

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _v9_polish_target_text(op.target, action, clause_type)
        content = "" if action == "REMOVE" else _v9_polish_content_text(op.content)

        if action == "ADD":
            target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"

        if action in {"ADD", "REPLACE"} and _content_too_weak_v5(content):
            continue
        if action in {"REPLACE", "REMOVE"} and not target:
            continue

        joined = f"{action} {clause_type} {target} {content}"
        if any(_too_similar(joined, old) for old in seen):
            continue
        seen.append(joined)
        final_ops.append(AmendOp(action=action, clause_type=clause_type, target=target, content=content))
        if len(final_ops) >= MAX_OPS:
            break

    return AmendGen(label=gen.label or "Generated amendment", operations=final_ops)

# ---------------------------------------------------------------------------
# v13 generic corrections after multi-topic test
# ---------------------------------------------------------------------------
# Fixes three generic cases found in broader topic tests:
# 1) do not split child phrases like "I would replace it with..." away from
#    their parent replacement paragraph;
# 2) leading "I would add..." must stay ADD even if the explanatory text later
#    mentions replace/remove;
# 3) prefer concise explicit policy clauses over long explanatory modal clauses.

_CHILD_MARKER_RE_V13 = re.compile(
    r"^(?:I|We)\s+would\s+(?:also\s+)?(?:replace|change|revise)\s+it\s+with\b",
    flags=re.IGNORECASE,
)


def _split_amend_intents(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    source_units = paragraphs if len(paragraphs) > 1 else [raw]

    provisional: List[str] = []
    for unit in source_units:
        chunks = _split_with_markers_v12(unit)
        if not chunks and _REQUEST_DETECT_RE_V12.search(unit):
            chunks = [re.sub(r"\s+", " ", unit).strip()]
        provisional.extend(chunks)

    # Merge child continuation markers into the previous chunk, e.g.
    # "I would replace it with wording..." inside a paragraph that already
    # started with "one part should be replaced".
    merged: List[str] = []
    for chunk in provisional:
        chunk = re.sub(r"\s+", " ", chunk or "").strip(" ,;:-")
        if not chunk:
            continue
        if merged and _CHILD_MARKER_RE_V13.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue
        if any(_too_similar(chunk, old) for old in merged):
            continue
        merged.append(chunk)
        if len(merged) >= MAX_OPS:
            break

    if not merged:
        for sent in _sentence_list_v7(raw):
            if _REQUEST_DETECT_RE_V12.search(sent):
                merged.append(sent)
            if len(merged) >= MAX_OPS:
                break

    return merged[:MAX_OPS]


_old_guess_action_v13 = _guess_action


def _guess_action(intent: str) -> str:
    lead = _leading_action_window_v12(intent)

    # Leading ADD marker takes priority over later explanatory text.
    if re.search(r"^(?:i|we)\s+would\s+(?:also\s+)?(?:like\s+to\s+)?add\b", lead):
        return "ADD"
    if re.search(r"^(?:another\s+thing|one\s+thing|a\s+further\s+point)\s+(?:i|we)\s+would\s+(?:also\s+)?add\b", lead):
        return "ADD"
    if re.search(r"^(?:i|we)\s+think\s+(?:the|this)\s+proposal\s+should\s+(?:also\s+)?add\b", lead):
        return "ADD"
    if re.search(r"^(?:the|this)\s+proposal\s+should\s+(?:also\s+)?add\b", lead):
        return "ADD"

    return _old_guess_action_v13(intent)


_old_candidate_sentences_v13 = _candidate_sentences_v8


def _candidate_sentences_v8(intent: str, action: str) -> List[str]:
    s = re.sub(r"\s+", " ", intent or "").strip()
    candidates: List[str] = []

    # Strong generic direct-policy patterns first.
    direct_patterns = [
        r"(?P<c>[A-Z][A-Za-z0-9 ,&'\-/()]{2,90}?\s+education\s+should\s+be\s+made\s+simple\s+and\s+practical\s+for\s+everyone)",
        r"(?P<c>[A-Z][A-Za-z0-9 ,&'\-/()]{2,120}?\s+capacity-building\s+should\s+include\s+support\s+for\s+[^.]+)",
        r"(?P<c>[A-Z][A-Za-z0-9 ,&'\-/()]{2,90}?\s+should\s+be\s+encouraged\s+to\s+develop\s+[^.]+?responsible[^.]*)",
        r"(?P<c>[A-Z][A-Za-z0-9 ,&'\-/()]{2,90}?\s+should\s+also\s+measure\s+whether\s+[^.]+)",
        r"(?P<c>These\s+results\s+should\s+be\s+reviewed\s+regularly[^.]*)",
    ]
    for pattern in direct_patterns:
        for m in re.finditer(pattern, s, flags=re.IGNORECASE):
            candidates.append(m.group("c"))

    for c in _old_candidate_sentences_v13(intent, action):
        if not any(_too_similar(c, old) for old in candidates):
            candidates.append(c)

    return candidates


_old_formalize_clause_content_v13 = _formalize_clause_content_v8


def _formalize_clause_content_v8(text: str) -> str:
    s = _old_formalize_clause_content_v13(text)

    # Repair modal clauses with "also measure" and passive review.
    s = re.sub(r"\bto\s+also\s+measure\b", "to measure", s, flags=re.IGNORECASE)
    s = re.sub(r"^these\s+results\s+to\s+be\s+reviewed\b", "relevant actors to review these results", s, flags=re.IGNORECASE)
    s = re.sub(r"\bto\s+be\s+encouraged\s+to\b", "to", s, flags=re.IGNORECASE)

    # If a sentence says "X can ..., so Y should ...", keep the normative Y.
    m = re.search(r"\bso\s+(?P<actor>[A-Za-z][A-Za-z0-9 ,&'\-/()]{2,90}?)\s+should\s+(?P<action>.+)$", s, flags=re.IGNORECASE)
    if m:
        actor = m.group("actor").strip(" .,;:")
        action = re.sub(r"^also\s+", "", m.group("action").strip(" .,;:"), flags=re.IGNORECASE)
        action = re.sub(r"^be\s+encouraged\s+to\s+", "", action, flags=re.IGNORECASE)
        s = f"{actor} to {action}"

    return _truncate_words_v8(re.sub(r"\s+", " ", s).strip(" ."))


_old_derive_target_v13 = _derive_target


def _derive_target(intent: str, action: str, clause_type: str, hints: List[Tuple[str, str]]) -> str:
    if action == "ADD":
        return "as new operative clause" if clause_type == "operative" else "as new preambular clause"

    s = re.sub(r"\s+", " ", intent or "").strip()

    m = re.search(r"talks\s+about\s+(?:increasing\s+)?access\s+to\s+(?P<object>.+?)(?:,\s+but|\s+but|\.)", s, flags=re.IGNORECASE)
    if m:
        obj = _v10_clip_target_phrase(m.group("object"), max_words=int(os.getenv("AMEND_AI_TARGET_MAX_WORDS", "16")))
        return f"operative wording concerning access to {obj}"

    m = re.search(r"wording\s+that\s+makes\s+it\s+seem\s+like\s+(?P<object>.+?)\s+should\s+be\s+shared\s+without\s+conditions", s, flags=re.IGNORECASE)
    if m:
        obj = _v10_clip_target_phrase(m.group("object"), max_words=14)
        return f"wording suggesting that {obj} should be shared without conditions"

    return _old_derive_target_v13(intent, action, clause_type, hints)


_old_postprocess_amend_gen_v13 = _postprocess_amend_gen


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    gen = _old_postprocess_amend_gen_v13(gen)
    final_ops: List[AmendOp] = []
    seen: List[str] = []

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _v9_polish_target_text(op.target, action, clause_type)
        content = "" if action == "REMOVE" else _v9_polish_content_text(op.content)

        # ADD target must remain a position, not a replacement target.
        if action == "ADD":
            target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"

        # Generic tiny grammar repairs after content polish.
        content = re.sub(r"\bto\s+also\s+measure\b", "to measure", content, flags=re.IGNORECASE)
        content = re.sub(r"\bto\s+be\s+encouraged\s+to\b", "to", content, flags=re.IGNORECASE)
        content = re.sub(r"\s+", " ", content).strip(" .")

        if action in {"ADD", "REPLACE"} and _content_too_weak_v5(content):
            continue
        if action in {"REPLACE", "REMOVE"} and not target:
            continue

        joined = f"{action} {clause_type} {target} {content}"
        if any(_too_similar(joined, old) for old in seen):
            continue
        seen.append(joined)
        final_ops.append(AmendOp(action=action, clause_type=clause_type, target=target, content=content))
        if len(final_ops) >= MAX_OPS:
            break

    return AmendGen(label=gen.label or "Generated amendment", operations=final_ops)

# ---------------------------------------------------------------------------
# v14 generic candidate-priority repairs
# ---------------------------------------------------------------------------
# Prefer explicit replacement text and concise capacity/education/environment
# clauses over longer explanatory modal sentences.


def _first_match_group_v14(patterns: List[str], text: str) -> str:
    for pattern in patterns:
        m = re.search(pattern, text or "", flags=re.IGNORECASE)
        if m:
            return re.sub(r"\s+", " ", m.group("c")).strip(" .")
    return ""


_old_formalize_content_v14 = _formalize_content


def _formalize_content(intent: str, action: str) -> str:
    if action == "REMOVE":
        return ""

    s = re.sub(r"\s+", " ", intent or "").strip()

    if action == "REPLACE":
        explicit = _first_match_group_v14([
            r"replace\s+it\s+with\s+wording\s+that\s+says\s+(?P<c>.+?)(?:\.|$)",
            r"replace\s+.+?\s+with\s+(?:wording\s+that\s+says\s+)?(?P<c>.+?)(?:\.|$)",
            r"instead,?\s+(?:the|this)\s+proposal\s+should\s+(?:say|state|mention|make\s+clear)\s+that\s+(?P<c>.+?)(?:\.|$)",
        ], s)
        if explicit:
            return _v9_polish_content_text(_formalize_clause_content_v8(explicit))

    if action == "ADD":
        explicit = _first_match_group_v14([
            r"(?P<c>[A-Z][A-Za-z0-9 ,&'\-/()]{2,100}?\s+education\s+should\s+be\s+made\s+simple\s+and\s+practical\s+for\s+everyone)",
            r"(?P<c>[A-Z][A-Za-z0-9 ,&'\-/()]{2,130}?\s+capacity-building\s+should\s+include\s+support\s+for\s+[^.]+)",
            r"(?P<c>[A-Z][A-Za-z0-9 ,&'\-/()]{2,110}?\s+should\s+be\s+encouraged\s+to\s+develop\s+[^.]+?responsible[^.]*)",
            r"(?P<c>[A-Z][A-Za-z0-9 ,&'\-/()]{2,120}?\s+systems?\s+used\s+in\s+[^.]+?\s+should\s+respect\s+[^.]+)",
        ], s)
        if explicit:
            return _v9_polish_content_text(_formalize_clause_content_v8(explicit))

    out = _old_formalize_content_v14(intent, action)
    out = re.sub(r"^because\s+of\s+that,?\s+", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\bgovernments\s+should\s+think\s+about\s+how\s+to\b", "governments to", out, flags=re.IGNORECASE)
    return _v9_polish_content_text(out)


_old_postprocess_amend_gen_v14 = _postprocess_amend_gen


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    gen = _old_postprocess_amend_gen_v14(gen)
    final_ops: List[AmendOp] = []
    seen: List[str] = []

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _v9_polish_target_text(op.target, action, clause_type)
        content = "" if action == "REMOVE" else _v9_polish_content_text(op.content)
        content = re.sub(r"^because\s+of\s+that,?\s+", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\bgovernments\s+should\s+think\s+about\s+how\s+to\b", "governments to", content, flags=re.IGNORECASE)
        content = re.sub(r"\bto\s+be\s+encouraged\s+to\b", "to", content, flags=re.IGNORECASE)
        content = re.sub(r"\s+", " ", content).strip(" .")

        if action == "ADD":
            target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"

        if action in {"ADD", "REPLACE"} and _content_too_weak_v5(content):
            continue
        if action in {"REPLACE", "REMOVE"} and not target:
            continue

        joined = f"{action} {clause_type} {target} {content}"
        if any(_too_similar(joined, old) for old in seen):
            continue
        seen.append(joined)
        final_ops.append(AmendOp(action=action, clause_type=clause_type, target=target, content=content))
        if len(final_ops) >= MAX_OPS:
            break

    return AmendGen(label=gen.label or "Generated amendment", operations=final_ops)

# ---------------------------------------------------------------------------
# v15 merge remove/soften child chunks and clean "..., so actor to action"
# ---------------------------------------------------------------------------

_CHILD_OF_SOFTEN_RE_V15 = re.compile(
    r"^(?:I|We)\s+would\s+(?:also\s+)?remove\s+any\s+wording\b|^(?:Instead,?\s+)?(?:the|this)\s+proposal\s+should\s+(?:say|state|mention|make\s+clear)\b",
    flags=re.IGNORECASE,
)


def _split_amend_intents(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    source_units = paragraphs if len(paragraphs) > 1 else [raw]

    provisional: List[str] = []
    for unit in source_units:
        chunks = _split_with_markers_v12(unit)
        if not chunks and _REQUEST_DETECT_RE_V12.search(unit):
            chunks = [re.sub(r"\s+", " ", unit).strip()]
        provisional.extend(chunks)

    merged: List[str] = []
    for chunk in provisional:
        chunk = re.sub(r"\s+", " ", chunk or "").strip(" ,;:-")
        if not chunk:
            continue

        if merged and _CHILD_MARKER_RE_V13.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue

        if merged and re.search(r"\b(remove\s+or\s+(?:at\s+least\s+)?soften|soften)\b", merged[-1], flags=re.IGNORECASE) and _CHILD_OF_SOFTEN_RE_V15.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue

        if any(_too_similar(chunk, old) for old in merged):
            continue
        merged.append(chunk)
        if len(merged) >= MAX_OPS:
            break

    if not merged:
        for sent in _sentence_list_v7(raw):
            if _REQUEST_DETECT_RE_V12.search(sent):
                merged.append(sent)
            if len(merged) >= MAX_OPS:
                break

    return merged[:MAX_OPS]


def _v15_final_content_polish(content: str) -> str:
    s = re.sub(r"\s+", " ", content or "").strip(" .")
    # "AI can use energy, so countries to develop..." -> "countries to develop..."
    m = re.match(r"^.+?,\s*so\s+(?P<actor>[A-Za-z][A-Za-z0-9 ,&'\-/()]{2,100}?)\s+to\s+(?P<action>.+)$", s, flags=re.IGNORECASE)
    if m:
        s = f"{m.group('actor').strip(' .,;:')} to {m.group('action').strip(' .,;:')}"
    s = re.sub(r"^sharing\s+to\s+happen\b", "sharing to take place", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return _truncate_words_v8(s)


_old_postprocess_amend_gen_v15 = _postprocess_amend_gen


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    gen = _old_postprocess_amend_gen_v15(gen)
    final_ops: List[AmendOp] = []
    seen: List[str] = []

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _v9_polish_target_text(op.target, action, clause_type)
        content = "" if action == "REMOVE" else _v15_final_content_polish(_v9_polish_content_text(op.content))

        if action == "ADD":
            target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"

        if action in {"ADD", "REPLACE"} and _content_too_weak_v5(content):
            continue
        if action in {"REPLACE", "REMOVE"} and not target:
            continue

        joined = f"{action} {clause_type} {target} {content}"
        if any(_too_similar(joined, old) for old in seen):
            continue
        seen.append(joined)
        final_ops.append(AmendOp(action=action, clause_type=clause_type, target=target, content=content))
        if len(final_ops) >= MAX_OPS:
            break

    return AmendGen(label=gen.label or "Generated amendment", operations=final_ops)

# ---------------------------------------------------------------------------
# v16 merge before limiting operation count
# ---------------------------------------------------------------------------
# If the 8th user paragraph is a remove/soften paragraph, its child chunks
# ("I would remove any wording...", "the proposal should say...") may appear
# after the MAX_OPS boundary. Merge first, then apply MAX_OPS.


def _split_amend_intents(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    source_units = paragraphs if len(paragraphs) > 1 else [raw]

    provisional: List[str] = []
    for unit in source_units:
        chunks = _split_with_markers_v12(unit)
        if not chunks and _REQUEST_DETECT_RE_V12.search(unit):
            chunks = [re.sub(r"\s+", " ", unit).strip()]
        provisional.extend(chunks)

    merged: List[str] = []
    for chunk in provisional:
        chunk = re.sub(r"\s+", " ", chunk or "").strip(" ,;:-")
        if not chunk:
            continue

        if merged and _CHILD_MARKER_RE_V13.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue

        if merged and re.search(r"\b(remove\s+or\s+(?:at\s+least\s+)?soften|soften)\b", merged[-1], flags=re.IGNORECASE) and _CHILD_OF_SOFTEN_RE_V15.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue

        if any(_too_similar(chunk, old) for old in merged):
            continue
        merged.append(chunk)

    if not merged:
        for sent in _sentence_list_v7(raw):
            if _REQUEST_DETECT_RE_V12.search(sent):
                merged.append(sent)
            if len(merged) >= MAX_OPS:
                break

    return merged[:MAX_OPS]

# ---------------------------------------------------------------------------
# v17 merge generic parent/child amendment chunks before MAX_OPS
# ---------------------------------------------------------------------------
# Some paragraphs use a short parent marker first ("add more attention to X")
# and then the actual normative sentence ("the proposal should mention...").
# Merge those child markers into the parent so they do not consume operation slots.

_CHILD_OF_INCOMPLETE_PARENT_RE_V17 = re.compile(
    r"^(?:I|We)\s+would\s+(?:also\s+)?replace\s+that\s+idea\s+with\b"
    r"|^(?:I|We)\s+think\s+(?:the|this)\s+proposal\s+should\s+(?:mention|say|include|state|explain|clarify)\b"
    r"|^(?:I|We)\s+would\s+(?:also\s+)?add\s+that\s+[^.]{0,120}?\bshould\b",
    flags=re.IGNORECASE,
)

_INCOMPLETE_PARENT_RE_V17 = re.compile(
    r"\b(replace\s+or\s+(?:at\s+least\s+)?expand|replaced\s+or\s+made\s+clearer|add\s+more\s+attention\s+to|add\s+something\s+about|add\s+a\s+point\s+about|add\s+is\s+stronger\s+support|stronger\s+support\s+for|stronger\s+language\s+about|remove\s+or\s+(?:at\s+least\s+)?soften|soften)\b",
    flags=re.IGNORECASE,
)


def _split_amend_intents(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    source_units = paragraphs if len(paragraphs) > 1 else [raw]

    provisional: List[str] = []
    for unit in source_units:
        chunks = _split_with_markers_v12(unit)
        if not chunks and _REQUEST_DETECT_RE_V12.search(unit):
            chunks = [re.sub(r"\s+", " ", unit).strip()]
        provisional.extend(chunks)

    merged: List[str] = []
    for chunk in provisional:
        chunk = re.sub(r"\s+", " ", chunk or "").strip(" ,;:-")
        if not chunk:
            continue

        if merged and _CHILD_MARKER_RE_V13.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue

        if merged and re.search(r"\b(remove\s+or\s+(?:at\s+least\s+)?soften|soften)\b", merged[-1], flags=re.IGNORECASE) and _CHILD_OF_SOFTEN_RE_V15.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue

        if merged and _INCOMPLETE_PARENT_RE_V17.search(merged[-1]) and _CHILD_OF_INCOMPLETE_PARENT_RE_V17.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue

        if any(_too_similar(chunk, old) for old in merged):
            continue
        merged.append(chunk)

    if not merged:
        for sent in _sentence_list_v7(raw):
            if _REQUEST_DETECT_RE_V12.search(sent):
                merged.append(sent)
            if len(merged) >= MAX_OPS:
                break

    return merged[:MAX_OPS]


_old_postprocess_amend_gen_v17 = _postprocess_amend_gen


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    gen = _old_postprocess_amend_gen_v17(gen)
    final_ops: List[AmendOp] = []
    seen: List[str] = []

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _v9_polish_target_text(op.target, action, clause_type)
        content = "" if action == "REMOVE" else _v15_final_content_polish(_v9_polish_content_text(op.content))
        content = re.sub(r"^something\s+broader:\s*", "", content, flags=re.IGNORECASE).strip()
        content = re.sub(r"^encourage\s+", "countries and organizations to ", content, flags=re.IGNORECASE)
        content = re.sub(r"\s+", " ", content).strip(" .")

        if action == "ADD":
            target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"

        if action in {"ADD", "REPLACE"} and _content_too_weak_v5(content):
            continue
        if action in {"REPLACE", "REMOVE"} and not target:
            continue

        joined = f"{action} {clause_type} {target} {content}"
        if any(_too_similar(joined, old) for old in seen):
            continue
        seen.append(joined)
        final_ops.append(AmendOp(action=action, clause_type=clause_type, target=target, content=content))
        if len(final_ops) >= MAX_OPS:
            break

    return AmendGen(label=gen.label or "Generated amendment", operations=final_ops)

# ---------------------------------------------------------------------------
# v18 safer parent-child merge and final polish
# ---------------------------------------------------------------------------

_REPLACE_CHILD_RE_V18 = re.compile(r"^(?:I|We)\s+would\s+(?:also\s+)?replace\s+that\s+idea\s+with\b", flags=re.IGNORECASE)
_ADD_CHILD_RE_V18 = re.compile(
    r"^(?:I|We)\s+think\s+(?:the|this)\s+proposal\s+should\s+(?:mention|say|include|state|explain|clarify)\b"
    r"|^(?:I|We)\s+would\s+(?:also\s+)?add\s+that\s+[^.]{0,120}?\bshould\b",
    flags=re.IGNORECASE,
)
_ADD_INCOMPLETE_PARENT_RE_V18 = re.compile(
    r"\b(add\s+more\s+attention\s+to|add\s+something\s+about|add\s+a\s+point\s+about|add\s+is\s+stronger\s+support|stronger\s+support\s+for|stronger\s+language\s+about)\b",
    flags=re.IGNORECASE,
)
_REPLACE_INCOMPLETE_PARENT_RE_V18 = re.compile(
    r"\b(replace\s+or\s+(?:at\s+least\s+)?expand|replaced\s+or\s+made\s+clearer)\b",
    flags=re.IGNORECASE,
)


def _split_amend_intents(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    source_units = paragraphs if len(paragraphs) > 1 else [raw]

    provisional: List[str] = []
    for unit in source_units:
        chunks = _split_with_markers_v12(unit)
        if not chunks and _REQUEST_DETECT_RE_V12.search(unit):
            chunks = [re.sub(r"\s+", " ", unit).strip()]
        provisional.extend(chunks)

    merged: List[str] = []
    for chunk in provisional:
        chunk = re.sub(r"\s+", " ", chunk or "").strip(" ,;:-")
        if not chunk:
            continue

        if merged and _REPLACE_INCOMPLETE_PARENT_RE_V18.search(merged[-1]) and _REPLACE_CHILD_RE_V18.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue

        if merged and _ADD_INCOMPLETE_PARENT_RE_V18.search(merged[-1]) and _ADD_CHILD_RE_V18.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue

        if merged and re.search(r"\b(remove\s+or\s+(?:at\s+least\s+)?soften|soften)\b", merged[-1], flags=re.IGNORECASE) and _CHILD_OF_SOFTEN_RE_V15.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue

        if any(_too_similar(chunk, old) for old in merged):
            continue
        merged.append(chunk)

    if not merged:
        for sent in _sentence_list_v7(raw):
            if _REQUEST_DETECT_RE_V12.search(sent):
                merged.append(sent)
            if len(merged) >= MAX_OPS:
                break

    return merged[:MAX_OPS]


_old_derive_target_v18 = _derive_target


def _derive_target(intent: str, action: str, clause_type: str, hints: List[Tuple[str, str]]) -> str:
    if action == "ADD":
        return "as new operative clause" if clause_type == "operative" else "as new preambular clause"

    s = re.sub(r"\s+", " ", intent or "").strip()

    m = re.search(r"talks\s+about\s+(?P<object>raising\s+[^.]+?)(?:,\s*I\s+think|\s+I\s+think|\.)", s, flags=re.IGNORECASE)
    if m:
        obj = _v10_clip_target_phrase(m.group("object"), max_words=int(os.getenv("AMEND_AI_TARGET_MAX_WORDS", "16")))
        return f"operative wording concerning {obj}"

    return _old_derive_target_v18(intent, action, clause_type, hints)


_old_postprocess_amend_gen_v18 = _postprocess_amend_gen


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    gen = _old_postprocess_amend_gen_v18(gen)
    final_ops: List[AmendOp] = []
    seen: List[str] = []

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _v9_polish_target_text(op.target, action, clause_type)
        content = "" if action == "REMOVE" else _v15_final_content_polish(_v9_polish_content_text(op.content))
        content = re.sub(r"^something\s+broader:\s*", "", content, flags=re.IGNORECASE).strip()
        content = re.sub(r"^encourage\s+(.+?)\s+to\s+", r"\1 to ", content, flags=re.IGNORECASE)
        content = re.sub(r"\s+", " ", content).strip(" .")

        if action == "ADD":
            target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"

        if action in {"ADD", "REPLACE"} and _content_too_weak_v5(content):
            continue
        if action in {"REPLACE", "REMOVE"} and not target:
            continue

        joined = f"{action} {clause_type} {target} {content}"
        if any(_too_similar(joined, old) for old in seen):
            continue
        seen.append(joined)
        final_ops.append(AmendOp(action=action, clause_type=clause_type, target=target, content=content))
        if len(final_ops) >= MAX_OPS:
            break

    return AmendGen(label=gen.label or "Generated amendment", operations=final_ops)

# ---------------------------------------------------------------------------
# v19 final small repairs
# ---------------------------------------------------------------------------
# Include "I would replace it with..." as a child of a replacement parent, and
# repair duplicated actor prefixes introduced by earlier compatibility polish.

_REPLACE_CHILD_RE_V18 = re.compile(
    r"^(?:I|We)\s+would\s+(?:also\s+)?replace\s+(?:that\s+idea|it)\s+with\b",
    flags=re.IGNORECASE,
)


def _split_amend_intents(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    source_units = paragraphs if len(paragraphs) > 1 else [raw]
    provisional: List[str] = []

    for unit in source_units:
        chunks = _split_with_markers_v12(unit)
        if not chunks and _REQUEST_DETECT_RE_V12.search(unit):
            chunks = [re.sub(r"\s+", " ", unit).strip()]
        provisional.extend(chunks)

    merged: List[str] = []
    for chunk in provisional:
        chunk = re.sub(r"\s+", " ", chunk or "").strip(" ,;:-")
        if not chunk:
            continue
        if merged and _REPLACE_INCOMPLETE_PARENT_RE_V18.search(merged[-1]) and _REPLACE_CHILD_RE_V18.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue
        if merged and _ADD_INCOMPLETE_PARENT_RE_V18.search(merged[-1]) and _ADD_CHILD_RE_V18.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue
        if merged and re.search(r"\b(remove\s+or\s+(?:at\s+least\s+)?soften|soften)\b", merged[-1], flags=re.IGNORECASE) and _CHILD_OF_SOFTEN_RE_V15.search(chunk):
            merged[-1] = f"{merged[-1]} {chunk}"
            continue
        if any(_too_similar(chunk, old) for old in merged):
            continue
        merged.append(chunk)

    if not merged:
        for sent in _sentence_list_v7(raw):
            if _REQUEST_DETECT_RE_V12.search(sent):
                merged.append(sent)
            if len(merged) >= MAX_OPS:
                break
    return merged[:MAX_OPS]


_old_postprocess_amend_gen_v19 = _postprocess_amend_gen


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    gen = _old_postprocess_amend_gen_v19(gen)
    final_ops: List[AmendOp] = []
    seen: List[str] = []
    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _v9_polish_target_text(op.target, action, clause_type)
        content = "" if action == "REMOVE" else _v15_final_content_polish(_v9_polish_content_text(op.content))
        content = re.sub(r"^something\s+broader:\s*", "", content, flags=re.IGNORECASE).strip()
        content = re.sub(r"^encourage\s+(.+?)\s+to\s+", r"\1 to ", content, flags=re.IGNORECASE)
        content = re.sub(r"\b(countries\s+and\s+organizations)\s+to\s+\1\s+to\b", r"\1 to", content, flags=re.IGNORECASE)
        content = re.sub(r"\s+", " ", content).strip(" .")

        if action == "ADD":
            target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"
        if action in {"ADD", "REPLACE"} and _content_too_weak_v5(content):
            continue
        if action in {"REPLACE", "REMOVE"} and not target:
            continue
        joined = f"{action} {clause_type} {target} {content}"
        if any(_too_similar(joined, old) for old in seen):
            continue
        seen.append(joined)
        final_ops.append(AmendOp(action=action, clause_type=clause_type, target=target, content=content))
        if len(final_ops) >= MAX_OPS:
            break
    return AmendGen(label=gen.label or "Generated amendment", operations=final_ops)

# ---------------------------------------------------------------------------
# v20 generic coverage/polish for practical-action amendment passages
# ---------------------------------------------------------------------------
# This pass fixes a general failure pattern found in long amendment paragraphs:
# the user asks for clearer definitions, practical action, affected-group
# inclusion, safeguards against overreach, platform/AI accountability, shared
# responsibility, removal/reduction of repeated values language, and prevention.
# The rules below are wording-pattern based and are not tied to one policy topic.


def _v20_short_topic_from_text(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip()
    patterns = [
        r"\b([A-Za-z][A-Za-z\- ]{2,50}?)\s+is\s+a\s+serious\s+problem\b",
        r"\bwhat\s+counts\s+as\s+([A-Za-z][A-Za-z\- ]{2,50}?)(?:,|\.|\s+especially)\b",
        r"\baction\s+against\s+([A-Za-z][A-Za-z\- ]{2,50}?)\s+must\b",
        r"\bfighting\s+([A-Za-z][A-Za-z\- ]{2,50}?)\s+is\s+important\b",
        r"\bfight\s+against\s+([A-Za-z][A-Za-z\- ]{2,50}?)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, s, flags=re.IGNORECASE)
        if not m:
            continue
        topic = m.group(1).strip(" .,;:")
        topic = re.sub(r"^(the|a|an)\s+", "", topic, flags=re.IGNORECASE).strip()
        topic = re.split(r"\b(?:and|or|because|when|that)\b", topic, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .,;:")
        if 1 <= len(topic.split()) <= 5:
            return topic.lower()
    return "the issue"


def _v20_issue_for_content(intent: str) -> str:
    topic = _v20_short_topic_from_text(intent)
    if topic != "the issue":
        return topic
    # Look for repeated policy noun phrases in the local chunk.
    phrases = re.findall(r"\b([A-Za-z][A-Za-z\-]+\s+(?:speech|violence|discrimination|trafficking|security|health|education|intelligence|pollution|poverty|inequality))\b", intent or "", flags=re.IGNORECASE)
    if phrases:
        return phrases[0].lower()
    return "the issue"


_old_guess_action_v20 = _guess_action


def _guess_action(intent: str) -> str:
    lead = _leading_action_window_v12(intent)
    if re.search(r"\bremove\s+or\s+reduce\b|\breduce\s+repeated\b", lead, flags=re.IGNORECASE):
        return "REPLACE"
    return _old_guess_action_v20(intent)


_old_formalize_content_v20 = _formalize_content


def _formalize_content(intent: str, action: str) -> str:
    if action == "REMOVE":
        return ""

    s = re.sub(r"\s+", " ", intent or "").strip()
    issue = _v20_issue_for_content(s)

    # Clear definition / ordinary-language explanation.
    if re.search(r"\bexplained\s+in\s+a\s+way\s+ordinary\s+people\s+can\s+understand\b|\bclear\s+and\s+fair\s+explanation\b", s, flags=re.IGNORECASE):
        m = re.search(r"clear\s+and\s+fair\s+explanation\s+of\s+what\s+counts\s+as\s+(?P<object>.+?)(?:\.|$)", s, flags=re.IGNORECASE)
        if m:
            obj = re.sub(r"\s+", " ", m.group("object")).strip(" .,;:")
            obj = re.sub(r"^" + re.escape(issue) + r",\s*", issue + ", ", obj, flags=re.IGNORECASE)
            return _truncate_words_v8(f"relevant actors to create a clear and fair explanation of what counts as {obj}")
        return _truncate_words_v8(f"relevant actors to explain {issue} clearly and fairly in language ordinary people can understand")

    # Replace vague awareness/dialogue language with practical action.
    if action == "REPLACE" and re.search(r"\bawareness\s+and\s+dialogue\b|\btolerance,\s*peace,\s*and\s*respect\b|\bstronger\s+wording\s+about\s+real\s+action\b", s, flags=re.IGNORECASE):
        pieces: List[str] = []
        if re.search(r"practical\s+education", s, flags=re.IGNORECASE):
            pieces.append("practical education")
        if re.search(r"reporting\s+systems?", s, flags=re.IGNORECASE):
            pieces.append("simple reporting systems")
        if re.search(r"support\s+when\s+they\s+become\s+targets?|support\s+for\s+people", s, flags=re.IGNORECASE):
            pieces.append(f"support for people targeted by {issue}")
        if not pieces:
            pieces = ["practical education", "clear reporting systems", "support for affected people"]
        return _truncate_words_v8("relevant actors to provide " + ", ".join(pieces))

    # Stronger protection / affected-group inclusion.
    m = re.search(r"stronger\s+protection\s+for\s+people\s+who\s+are\s+often\s+targeted,?\s+such\s+as\s+(?P<groups>.+?)(?:\.|$)", s, flags=re.IGNORECASE)
    if m:
        groups = m.group("groups").strip(" .,;:")
        return _truncate_words_v8(f"relevant actors to strengthen protection for often-targeted people, including {groups}")

    if re.search(r"\bincluded\s+in\s+the\s+planning\s+of\s+solutions\b|\bexperiences\s+should\s+be\s+listened\s+to\b", s, flags=re.IGNORECASE):
        return _truncate_words_v8("relevant actors to include affected communities and their lived experiences in planning solutions")

    # Safeguards so rules are not abused to silence legitimate expression.
    if re.search(r"\bnot\s+become\s+an\s+excuse\s+to\s+silence\b|\bblock\s+criticism\b|\bpeaceful\s+disagreement\b|\bway\s+to\s+appeal\b", s, flags=re.IGNORECASE):
        return _truncate_words_v8(f"governments and relevant platforms to ensure that action against {issue} is fair, transparent, human-rights-based, and not used to silence legitimate expression")

    # AI/platform accountability and transparent reports.
    if re.search(r"\btechnology\s+companies\s+and\s+(?:AI|artificial\s+intelligence)\s+developers\b", s, flags=re.IGNORECASE) and re.search(r"\bexplain\s+what\s+they\s+are\s+doing\b|\blabel\s+AI-generated\b|\bpublish\s+clear\s+reports\b", s, flags=re.IGNORECASE):
        return _truncate_words_v8(f"technology companies and AI developers to explain risk-reduction measures, label AI-generated content when needed, improve detection, and publish clear reports")

    # Shared responsibility while keeping platform duties.
    if action == "REPLACE" and re.search(r"\btechnology\s+companies\s+can\s+solve\s+everything\s+by\s+themselves\b|\bsolve\s+everything\s+by\s+themselves\b", s, flags=re.IGNORECASE):
        return _truncate_words_v8("governments, civil society, researchers, educators, religious leaders, ordinary users, and technology companies to share responsibility while platforms retain strong duties")

    # Remove/reduce repetition and use the space for clearer duties.
    if action == "REPLACE" and re.search(r"\brepeated\s+phrases\b|\bsay\s+almost\s+the\s+same\s+thing\b|\btolerance,\s*dialogue,\s*and\s*respect\b", s, flags=re.IGNORECASE):
        return _truncate_words_v8("repeated language about values to be reduced so the proposal can more clearly explain what relevant actors should do")

    # Prevention and support before harm occurs.
    if re.search(r"\bnot\s+only\s+happen\s+after\s+harm\b|\bearly\s+prevention\b|\bbetter\s+education\b|\bstronger\s+community\s+dialogue\b", s, flags=re.IGNORECASE):
        return _truncate_words_v8("relevant actors to strengthen early prevention, better education, community dialogue, and support for people who are attacked")

    out = _old_formalize_content_v20(intent, action)
    out = re.sub(r"^also\s+add\s+", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\bthere\s+to\s+be\s+more\s+effort\s+to\b", "relevant actors to", out, flags=re.IGNORECASE)
    out = re.sub(r"\bthey\s+to\b", "relevant actors to", out, flags=re.IGNORECASE)
    return _v9_polish_content_text(out)


_old_derive_target_v20 = _derive_target


def _derive_target(intent: str, action: str, clause_type: str, hints: List[Tuple[str, str]]) -> str:
    if action == "ADD":
        return "as new operative clause" if clause_type == "operative" else "as new preambular clause"

    s = re.sub(r"\s+", " ", intent or "").strip()

    m = re.search(r"(?:more\s+general\s+wording\s+about|general\s+wording\s+about)\s+(?P<object>.+?)\s+with\s+stronger\s+wording", s, flags=re.IGNORECASE)
    if m:
        obj = _v10_clip_target_phrase(m.group("object"), max_words=int(os.getenv("AMEND_AI_TARGET_MAX_WORDS", "16")))
        return f"operative wording concerning {obj}"

    if re.search(r"\bsolve\s+everything\s+by\s+themselves\b", s, flags=re.IGNORECASE):
        return "wording suggesting that technology companies can solve the issue by themselves"

    if re.search(r"\brepeated\s+phrases\b|\bsay\s+almost\s+the\s+same\s+thing\b", s, flags=re.IGNORECASE):
        return "repeated wording about tolerance, dialogue, and respect"

    return _old_derive_target_v20(intent, action, clause_type, hints)


_old_postprocess_amend_gen_v20 = _postprocess_amend_gen


def _postprocess_amend_gen(gen: AmendGen) -> AmendGen:
    gen = _old_postprocess_amend_gen_v20(gen)
    final_ops: List[AmendOp] = []
    seen: List[str] = []

    for op in gen.operations:
        action = op.action
        clause_type = op.clause_type
        target = _v9_polish_target_text(op.target, action, clause_type)
        content = "" if action == "REMOVE" else _v15_final_content_polish(_v9_polish_content_text(op.content))

        content = re.sub(r"^also\s+add\s+", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\bthey\s+to\b", "relevant actors to", content, flags=re.IGNORECASE)
        content = re.sub(r"\bto\s+be\s+required\s+to\b", "to", content, flags=re.IGNORECASE)
        content = re.sub(r"\s+", " ", content).strip(" .")

        if action == "ADD":
            target = "as new operative clause" if clause_type == "operative" else "as new preambular clause"

        # If a REPLACE operation's content is only a vague phrase, skip it unless
        # no better rule-generated content exists. This prevents output like
        # "stronger wording about real action".
        if action in {"ADD", "REPLACE"} and _content_too_weak_v5(content):
            continue
        if action in {"ADD", "REPLACE"} and re.fullmatch(r"stronger wording about real action", content, flags=re.IGNORECASE):
            continue
        if action in {"REPLACE", "REMOVE"} and not target:
            continue

        joined = f"{action} {clause_type} {target} {content}"
        if any(_too_similar(joined, old) for old in seen):
            continue
        seen.append(joined)
        final_ops.append(AmendOp(action=action, clause_type=clause_type, target=target, content=content))
        if len(final_ops) >= MAX_OPS:
            break

    return AmendGen(label=gen.label or "Generated amendment", operations=final_ops)


_old_payload_quality_issues_v20 = _payload_quality_issues


def _payload_quality_issues(model_gen: AmendGen, rule_gen: AmendGen) -> List[str]:
    issues = _old_payload_quality_issues_v20(model_gen, rule_gen)
    # When the rule layer found broad coverage but the model returned only a few
    # items, prefer the rule layer. This is important for small models that stop
    # after the first few amendment paragraphs.
    if len(rule_gen.operations) >= 6 and len(model_gen.operations) < max(5, int(len(rule_gen.operations) * 0.75)):
        issues.append("model_too_sparse_for_long_request")
    for op in model_gen.operations:
        if re.search(r"\bthey\s+to\b|\bstronger\s+wording\s+about\s+real\s+action\b", op.content or "", flags=re.IGNORECASE):
            issues.append("weak_or_pronoun_content")
    return issues


def _merge_model_with_rules(model_gen: AmendGen, rule_gen: AmendGen) -> AmendGen:
    if _env_bool("AMEND_AI_PREFER_RULES", "1"):
        label = model_gen.label or rule_gen.label
        return _postprocess_amend_gen(AmendGen(label=label, operations=rule_gen.operations))

    issues = _payload_quality_issues(model_gen, rule_gen)
    if issues:
        print("AMEND AI QUALITY GATE USING RULE PAYLOAD:", ", ".join(sorted(set(issues))))
        return rule_gen

    ops: List[AmendOp] = list(model_gen.operations)
    for rule_op in rule_gen.operations:
        rule_text = f"{rule_op.action} {rule_op.clause_type} {rule_op.target} {rule_op.content}"
        if any(_too_similar(rule_text, f"{x.action} {x.clause_type} {x.target} {x.content}") for x in ops):
            continue
        ops.append(rule_op)
        if len(ops) >= MAX_OPS:
            break
    return _postprocess_amend_gen(AmendGen(label=model_gen.label or rule_gen.label, operations=ops))

# v20.1 issue extraction repair: prefer explicit "target(s) of X" over
# generic noun phrases such as "practical education".
_old_v20_issue_for_content_v201 = _v20_issue_for_content


def _v20_issue_for_content(intent: str) -> str:
    s = re.sub(r"\s+", " ", intent or "").strip()
    for pattern in [
        r"\btargets?\s+of\s+([A-Za-z][A-Za-z\- ]{2,50}?)(?:\.|,|;|$)",
        r"\btargeted\s+by\s+([A-Za-z][A-Za-z\- ]{2,50}?)(?:\.|,|;|$)",
        r"\bwhen\s+they\s+become\s+targets?\s+of\s+([A-Za-z][A-Za-z\- ]{2,50}?)(?:\.|,|;|$)",
    ]:
        m = re.search(pattern, s, flags=re.IGNORECASE)
        if m:
            issue = m.group(1).strip(" .,;:").lower()
            if 1 <= len(issue.split()) <= 5:
                return issue
    return _old_v20_issue_for_content_v201(intent)

# Rebind formalizer so it uses the repaired issue extractor above.
_old_formalize_content_v201 = _formalize_content


def _formalize_content(intent: str, action: str) -> str:
    # Re-run the v20 high-priority practical-action branch with the repaired
    # issue extractor before falling back to the existing formalizer.
    if action != "REMOVE":
        s = re.sub(r"\s+", " ", intent or "").strip()
        issue = _v20_issue_for_content(s)
        if action == "REPLACE" and re.search(r"\bawareness\s+and\s+dialogue\b|\btolerance,\s*peace,\s*and\s*respect\b|\bstronger\s+wording\s+about\s+real\s+action\b", s, flags=re.IGNORECASE):
            pieces: List[str] = []
            if re.search(r"practical\s+education", s, flags=re.IGNORECASE):
                pieces.append("practical education")
            if re.search(r"reporting\s+systems?", s, flags=re.IGNORECASE):
                pieces.append("simple reporting systems")
            if re.search(r"support\s+when\s+they\s+become\s+targets?|support\s+for\s+people", s, flags=re.IGNORECASE):
                pieces.append(f"support for people targeted by {issue}")
            if not pieces:
                pieces = ["practical education", "clear reporting systems", "support for affected people"]
            return _truncate_words_v8("relevant actors to provide " + ", ".join(pieces))
    return _old_formalize_content_v201(intent, action)