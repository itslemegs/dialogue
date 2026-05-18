# app/services/local_llm.py
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Union

import httpx

from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=True)

class LocalChatClient:
    def __init__(self):
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
        self.default_timeout_s = float(os.getenv("OLLAMA_TIMEOUT_S", "180"))
        self.keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "0")

        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                connect=30.0,
                read=self.default_timeout_s,
                write=60.0,
                pool=60.0,
            ),
        )

    def chat_completion(
        self,
        *,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 650,
        timeout_s: Optional[float] = None,
        response_format: Optional[Union[str, Dict[str, Any]]] = None,
        think: Optional[bool] = None,
    ):
        safe_max_tokens = min(
    int(max_tokens),
    int(os.getenv("OLLAMA_SAFE_NUM_PREDICT", "650")),
)

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,

            # Use 0 for safer local development so the model unloads after use.
            # This prevents it from staying resident and pressuring memory.
            "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "0"),

            "options": {
                "temperature": temperature,

                # num_predict caps generation length.
                # Ollama documents num_predict as the maximum number of tokens to predict.
                "num_predict": safe_max_tokens,

                # num_ctx controls context window size.
                # Smaller context means less memory/work.
                "num_ctx": int(os.getenv("OLLAMA_SAFE_NUM_CTX", "2048")),

                "top_k": int(os.getenv("OLLAMA_SAFE_TOP_K", "20")),
                "top_p": float(os.getenv("OLLAMA_SAFE_TOP_P", "0.8")),
                "seed": int(os.getenv("OLLAMA_SAFE_SEED", "42")),
            },
        }

        if response_format is not None:
            payload["format"] = response_format

        if think is not None:
            payload["think"] = think

        safe_timeout_s = min(
            float(timeout_s or self.default_timeout_s),
            float(os.getenv("OLLAMA_SAFE_TIMEOUT_S", "240")),
        )

        timeout = httpx.Timeout(
            connect=10.0,
            read=safe_timeout_s,
            write=30.0,
            pool=30.0,
        )

        try:
            r = self.client.post("/api/chat", json=payload, timeout=timeout)
            r.raise_for_status()
        except httpx.TimeoutException as e:
            raise TimeoutError(
                f"Ollama request stopped after safe timeout of {safe_timeout_s} seconds"
            ) from e
        except httpx.HTTPError as e:
            raise RuntimeError(f"Ollama HTTP error: {e}") from e

        data = r.json()
        text = ((data.get("message") or {}).get("content")) or ""

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=text)
                )
            ]
        )


_CLIENT = None


def get_chat_client():
    global _CLIENT

    backend = os.getenv("LLM_BACKEND", "ollama").lower()

    if backend == "ollama":
        if _CLIENT is None:
            _CLIENT = LocalChatClient()
        return _CLIENT

    raise RuntimeError(f"Unknown LLM_BACKEND={backend}. Supported: ollama")