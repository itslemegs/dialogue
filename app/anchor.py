import hashlib, json

def clean_text_from_clauses(clauses: list[dict]) -> str:
    greens = [c["text"] for c in clauses if c["status"] == "GREEN"]
    return "\n".join(greens).strip()

def hash_clean_text(clauses_json: str) -> str:
    clauses = json.loads(clauses_json)
    blob = clean_text_from_clauses(clauses).encode()
    return hashlib.sha256(blob).hexdigest()