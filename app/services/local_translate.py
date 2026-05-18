# app/services/local_translate.py

import os

# Important: set these before torch/transformers do heavy initialization.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "2")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "2")

import threading
from collections import OrderedDict
from typing import Literal

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer


Lang = Literal["ar", "zh", "fr", "ru", "es"]


MODEL_IDS: dict[Lang, str] = {
    "ar": "Helsinki-NLP/opus-mt-en-ar",
    "zh": "Helsinki-NLP/opus-mt-en-zh",
    "fr": "Helsinki-NLP/opus-mt-en-fr",
    "ru": "Helsinki-NLP/opus-mt-en-ru",
    "es": "Helsinki-NLP/opus-mt-en-es",
}


def _configure_torch_threads() -> None:
    torch_threads = int(os.getenv("TORCH_NUM_THREADS", "2"))

    try:
        torch.set_num_threads(torch_threads)
    except RuntimeError:
        pass

    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass


_configure_torch_threads()


def _pick_device() -> str:
    # Long-term safe default: CPU.
    # Use TRANSLATE_DEVICE=mps or cuda manually only after CPU mode is stable.
    forced = os.getenv("TRANSLATE_DEVICE", "cpu").lower()

    if forced == "cuda":
        return "cuda" if torch.cuda.is_available() else "cpu"

    if forced == "mps":
        has_mps = hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
        return "mps" if has_mps else "cpu"

    if forced == "auto":
        if torch.cuda.is_available():
            return "cuda"

        has_mps = hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
        if has_mps:
            return "mps"

        return "cpu"

    return "cpu"


_DEVICE = _pick_device()

_BATCH_SIZE = int(os.getenv("TRANSLATE_BATCH_SIZE", "4"))
_MAX_INPUT_TOKENS = int(os.getenv("TRANSLATE_MAX_INPUT_TOKENS", "256"))
_MAX_NEW_TOKENS = int(os.getenv("TRANSLATE_MAX_NEW_TOKENS", "320"))
_NUM_BEAMS = int(os.getenv("TRANSLATE_NUM_BEAMS", "1"))
_MAX_CHARS_PER_CHUNK = int(os.getenv("TRANSLATE_MAX_CHARS_PER_CHUNK", "700"))
_SEGMENT_CACHE_MAX = int(os.getenv("TRANSLATE_SEGMENT_CACHE_MAX", "4096"))

_BUNDLES: dict[str, tuple] = {}
_BUNDLE_LOCK = threading.Lock()

# Critical: only one model.generate at a time.
_GENERATE_LOCK = threading.Lock()

_SEGMENT_CACHE: OrderedDict[tuple[str, str], str] = OrderedDict()
_SEGMENT_CACHE_LOCK = threading.Lock()


def _model_kwargs() -> dict:
    if _DEVICE == "cuda":
        return {"torch_dtype": torch.float16}

    return {}


def _load_bundle(lang: Lang):
    model_id = MODEL_IDS[lang]

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_id, **_model_kwargs())

    model.to(_DEVICE)
    model.eval()

    return tokenizer, model


def _get_bundle(lang: Lang):
    bundle = _BUNDLES.get(lang)

    if bundle is not None:
        return bundle

    with _BUNDLE_LOCK:
        bundle = _BUNDLES.get(lang)

        if bundle is None:
            bundle = _load_bundle(lang)
            _BUNDLES[lang] = bundle

    return bundle


def _split_long_line(text: str, max_chars: int = _MAX_CHARS_PER_CHUNK) -> list[str]:
    if not text:
        return [""]

    if len(text) <= max_chars:
        return [text]

    leading_ws = text[: len(text) - len(text.lstrip())]
    trailing_ws = text[len(text.rstrip()):]
    core = text.strip()

    if not core:
        return [text]

    parts: list[str] = []
    cur = ""

    for word in core.split():
        candidate = f"{cur} {word}".strip()

        if cur and len(candidate) > max_chars:
            parts.append(cur)
            cur = word
        else:
            cur = candidate

    if cur:
        parts.append(cur)

    if parts:
        parts[0] = leading_ws + parts[0]
        parts[-1] = parts[-1] + trailing_ws

    return parts or [text]


def _parts_for_translation(text: str) -> list[tuple[str, bool]]:
    """
    Returns [(segment, should_translate)] while preserving line endings.
    """
    if not text:
        return [("", False)]

    parts: list[tuple[str, bool]] = []

    for raw_line in text.splitlines(keepends=True):
        if raw_line.endswith("\r\n"):
            body, line_ending = raw_line[:-2], "\r\n"
        elif raw_line.endswith("\n"):
            body, line_ending = raw_line[:-1], "\n"
        elif raw_line.endswith("\r"):
            body, line_ending = raw_line[:-1], "\r"
        else:
            body, line_ending = raw_line, ""

        if not body.strip():
            parts.append((raw_line, False))
            continue

        chunks = _split_long_line(body)

        for i, chunk in enumerate(chunks):
            parts.append((chunk, True))

            if i < len(chunks) - 1:
                parts.append((" ", False))

        if line_ending:
            parts.append((line_ending, False))

    return parts


def _cache_get(lang: Lang, segment: str) -> str | None:
    key = (lang, segment)

    with _SEGMENT_CACHE_LOCK:
        value = _SEGMENT_CACHE.get(key)

        if value is not None:
            _SEGMENT_CACHE.move_to_end(key)

        return value


def _cache_put(lang: Lang, segment: str, translated: str) -> None:
    key = (lang, segment)

    with _SEGMENT_CACHE_LOCK:
        _SEGMENT_CACHE[key] = translated
        _SEGMENT_CACHE.move_to_end(key)

        while len(_SEGMENT_CACHE) > _SEGMENT_CACHE_MAX:
            _SEGMENT_CACHE.popitem(last=False)


@torch.inference_mode()
def _translate_batch_uncached(texts: list[str], lang: Lang) -> list[str]:
    if not texts:
        return []

    tokenizer, model = _get_bundle(lang)
    outputs: list[str] = []

    # Prevent concurrent generation from multiple requests.
    with _GENERATE_LOCK:
        for start in range(0, len(texts), _BATCH_SIZE):
            batch = texts[start : start + _BATCH_SIZE]

            encoded = tokenizer(
                batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=_MAX_INPUT_TOKENS,
            )

            encoded = {
                key: value.to(_DEVICE)
                for key, value in encoded.items()
            }

            generated = model.generate(
                **encoded,
                max_new_tokens=_MAX_NEW_TOKENS,
                num_beams=_NUM_BEAMS,
                early_stopping=True,
            )

            decoded = tokenizer.batch_decode(
                generated,
                skip_special_tokens=True,
            )

            outputs.extend(decoded)

    return outputs


def _translate_segments_cached_batched(segments: list[str], lang: Lang) -> list[str]:
    if not segments:
        return []

    results: list[str | None] = [None] * len(segments)

    missing: list[str] = []
    missing_indexes: list[int] = []

    for i, segment in enumerate(segments):
        cached = _cache_get(lang, segment)

        if cached is not None:
            results[i] = cached
        else:
            missing.append(segment)
            missing_indexes.append(i)

    if missing:
        translated_missing = _translate_batch_uncached(missing, lang)

        for idx, source, translated in zip(
            missing_indexes,
            missing,
            translated_missing,
        ):
            cleaned = translated.strip()
            results[idx] = cleaned
            _cache_put(lang, source, cleaned)

    return [item or "" for item in results]


def translate_many_en_to(texts: list[str], lang: Lang) -> list[str]:
    """
    Translate many text blocks in one controlled batched operation.
    """
    all_parts: list[list[tuple[str, bool]]] = []
    translatable_segments: list[str] = []

    for text in texts:
        parts = _parts_for_translation(text or "")
        all_parts.append(parts)

        for segment, should_translate in parts:
            if should_translate and segment.strip():
                translatable_segments.append(segment)

    translated_segments = _translate_segments_cached_batched(
        translatable_segments,
        lang,
    )

    translated_iter = iter(translated_segments)
    outputs: list[str] = []

    for parts in all_parts:
        rebuilt: list[str] = []

        for segment, should_translate in parts:
            if should_translate and segment.strip():
                rebuilt.append(next(translated_iter))
            else:
                rebuilt.append(segment)

        outputs.append("".join(rebuilt))

    return outputs


def translate_en_to(text: str, lang: Lang) -> str:
    if not text:
        return ""

    return translate_many_en_to([text], lang)[0]


def warm_model(lang: Lang) -> None:
    _get_bundle(lang)


def current_translate_device() -> str:
    return _DEVICE