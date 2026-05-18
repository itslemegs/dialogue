# app/services/translate_ai.py
from typing import Literal

from app.services.local_translate import translate_en_to

Lang = Literal["en", "ar", "zh", "fr", "ru", "es"]

SUPPORTED: dict[str, dict[str, str]] = {
    "en": {"label": "EN", "name": "English", "dir": "ltr"},
    "ar": {"label": "AR", "name": "Arabic", "dir": "rtl"},
    "zh": {"label": "中文", "name": "Chinese", "dir": "ltr"},
    "fr": {"label": "FR", "name": "French", "dir": "ltr"},
    "ru": {"label": "RU", "name": "Russian", "dir": "ltr"},
    "es": {"label": "ES", "name": "Spanish", "dir": "ltr"},
}


def translate_text(text: str, lang: str) -> str:
    lang = (lang or "en").lower()

    if lang not in SUPPORTED:
        lang = "en"

    if lang == "en":
        return text

    return translate_en_to(text, lang)  # type: ignore[arg-type]


def translate_lang_meta(lang: str) -> dict[str, str]:
    lang = (lang or "en").lower()
    return SUPPORTED.get(lang, SUPPORTED["en"])


OFFICIAL_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "recalling": "Recalling",
        "noting": "Noting",
        "welcoming": "Welcoming",
        "expressing_regret": "Expressing regret",
        "expressing_deep_concern": "Deeply concerned",
        "emphasizing": "Emphasizing",
        "decides": "Decides",
        "requests": "Requests",
        "calls_upon": "Calls upon",
        "encourages": "Encourages",
    },
    "fr": {
        "recalling": "Rappelant",
        "noting": "Notant",
        "welcoming": "Se félicitant de",
        "expressing_regret": "Regrettant",
        "expressing_deep_concern": "Profondément préoccupé",
        "emphasizing": "Soulignant",
        "decides": "Décide",
        "requests": "Demande",
        "calls_upon": "Invite",
        "encourages": "Encourage",
    },
    "es": {
        "recalling": "Recordando",
        "noting": "Observando",
        "welcoming": "Acogiendo con beneplácito",
        "expressing_regret": "Lamentando",
        "expressing_deep_concern": "Profundamente preocupado",
        "emphasizing": "Destacando",
        "decides": "Decide",
        "requests": "Solicita",
        "calls_upon": "Exhorta",
        "encourages": "Alienta",
    },
    "ru": {
        "recalling": "Напоминая",
        "noting": "Отмечая",
        "welcoming": "Приветствуя",
        "expressing_regret": "Выражая сожаление",
        "expressing_deep_concern": "Будучи глубоко обеспокоенной",
        "emphasizing": "Подчеркивая",
        "decides": "Постановляет",
        "requests": "Просит",
        "calls_upon": "Призывает",
        "encourages": "Поощряет",
    },
    "zh": {
        "recalling": "回顾",
        "noting": "注意到",
        "welcoming": "欢迎",
        "expressing_regret": "表示遗憾",
        "expressing_deep_concern": "深表关切",
        "emphasizing": "强调",
        "decides": "决定",
        "requests": "请求",
        "calls_upon": "呼吁",
        "encourages": "鼓励",
    },
    "ar": {
        "recalling": "إذ تستذكر",
        "noting": "وإذ تلاحظ",
        "welcoming": "وإذ ترحب",
        "expressing_regret": "معربةً عن أسفها",
        "expressing_deep_concern": "وإذ يساورها بالغ القلق",
        "emphasizing": "وإذ تؤكد",
        "decides": "تقرر",
        "requests": "تطلب",
        "calls_upon": "تحث",
        "encourages": "تشجع",
    },
}


def labels_for(lang: str) -> dict:
    return OFFICIAL_LABELS.get(lang, OFFICIAL_LABELS["en"])