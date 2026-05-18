import os, json, re
from typing import Any, Dict, List
import httpx

from app.services.help_rag import retrieve, build_help_index, OLLAMA_URL

CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "mistral:7b-instruct")

SYSTEM_PROMPT = """You are the in-app copilot for a UN-style meeting platform.
Your job: explain what the user is looking at, and guide them to the next best actions.

Hard rules:
- Use ONLY the provided APP_STATE + ALLOWED_ACTIONS + RETRIEVED_DOCS.
- Never invent features, menus, or permissions.
- If info is missing, say so and give the safest next step (e.g., “open the overview”).
- Output MUST be valid JSON matching the schema below. No extra text.

JSON schema:
{
  "summary": string,
  "why_it_matters": string,
  "next_steps": [
    {"label": string, "why": string, "action": {"type": "NAV"|"OPEN_MODAL"|"CLICK"|"COPY", "to"?: string, "id"?: string, "text"?: string}}
  ],
  "glossary": [{"term": string, "plain": string}],
  "citations": [{"chunk_id": string, "doc": string}],
  "confidence": "high"|"medium"|"low"
}
"""

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()

async def ollama_chat_json(app_state: Dict[str, Any], allowed_actions: List[Dict[str, Any]], user_question: str) -> Dict[str, Any]:
    # retrieval query includes state so it fetches the right procedural chunk
    retrieval_query = f"""
User question: {user_question}

App state (route/stage/role): {json.dumps(app_state, ensure_ascii=False)}
""".strip()

    docs = await retrieve(retrieval_query, k=6)

    payload = {
        "model": CHAT_MODEL,
        "stream": False,
        # Ollama supports requesting JSON format; if the model still emits junk, we hard-parse below.
        "format": "json",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({
                "APP_STATE": app_state,
                "ALLOWED_ACTIONS": allowed_actions,
                "USER_QUESTION": user_question,
                "RETRIEVED_DOCS": [
                    {"chunk_id": d["chunk_id"], "doc": d["doc"], "text": d["text"], "score": d["score"]}
                    for d in docs
                ],
            }, ensure_ascii=False)}
        ],
        "options": {
            "temperature": 0.2,
        }
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        r.raise_for_status()
        content = r.json()["message"]["content"]

    content = _strip_fences(content)
    try:
        data = json.loads(content)
    except Exception:
        # last-resort: extract first JSON object
        m = re.search(r"\{.*\}", content, flags=re.S)
        if not m:
            return {
                "summary": "I couldn’t produce a structured help response.",
                "why_it_matters": "The assistant response wasn’t valid JSON.",
                "next_steps": [{"label": "Open Help Overview", "why": "Start from the main guide.", "action": {"type": "NAV", "to": "/help"}}],
                "glossary": [],
                "citations": [],
                "confidence": "low",
            }
        data = json.loads(m.group(0))

    return data
