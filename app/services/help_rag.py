import os, json, math, re
from pathlib import Path
from typing import List, Dict, Any, Tuple

import numpy as np
import httpx

DOCS_DIR = Path("app/help_docs")
INDEX_DIR = Path("data/help_index")
INDEX_DIR.mkdir(parents=True, exist_ok=True)

EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")

def _chunk_text(text: str, max_chars: int = 900, overlap: int = 150) -> List[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    chunks = []
    i = 0
    while i < len(text):
        j = min(len(text), i + max_chars)
        chunk = text[i:j].strip()
        if chunk:
            chunks.append(chunk)
        i = j - overlap
        if i < 0:
            i = 0
        if i >= len(text):
            break
    return chunks

async def ollama_embed(text: str) -> np.ndarray:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
        )
        r.raise_for_status()
        vec = r.json()["embedding"]
    return np.array(vec, dtype=np.float32)

def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return -1.0
    return float(np.dot(a, b) / (na * nb))

def index_paths() -> Tuple[Path, Path]:
    return INDEX_DIR / "embeddings.npy", INDEX_DIR / "meta.json"

async def build_help_index(force: bool = False) -> None:
    emb_path, meta_path = index_paths()
    if not force and emb_path.exists() and meta_path.exists():
        return

    docs = sorted(DOCS_DIR.glob("*.md"))
    if not docs:
        raise RuntimeError(f"No help docs found in {DOCS_DIR}")

    meta: List[Dict[str, Any]] = []
    vectors: List[np.ndarray] = []

    for p in docs:
        text = p.read_text(encoding="utf-8")
        for ci, chunk in enumerate(_chunk_text(text)):
            vec = await ollama_embed(chunk)
            vectors.append(vec)
            meta.append({
                "doc": p.name,
                "chunk_id": f"{p.stem}:{ci}",
                "text": chunk,
            })

    embs = np.stack(vectors, axis=0)
    np.save(emb_path, embs)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

async def retrieve(query: str, k: int = 6) -> List[Dict[str, Any]]:
    emb_path, meta_path = index_paths()
    if not emb_path.exists() or not meta_path.exists():
        await build_help_index(force=True)

    embs = np.load(emb_path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    qv = await ollama_embed(query)

    # brute-force cosine (fine for a few thousand chunks)
    scores = []
    for i in range(embs.shape[0]):
        scores.append((_cosine(qv, embs[i]), i))
    scores.sort(reverse=True, key=lambda x: x[0])

    out = []
    for s, i in scores[:k]:
        m = meta[i]
        out.append({**m, "score": s})
    return out
