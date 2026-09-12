"""Master AI policy, captured once at process start from the environment.

Only the explicit value ``true`` (case-insensitive, whitespace stripped) enables
AI. Missing, false, and invalid values disable it. Restart to apply changes.
"""
import os

AI_FEATURES_ENABLED = os.environ.get("AI_FEATURES_ENABLED", "false").strip().lower() == "true"


class AIFeaturesDisabled(RuntimeError):
    """AI work was rejected before any resources were used."""


def require_ai_features() -> None:
    if not AI_FEATURES_ENABLED:
        raise AIFeaturesDisabled("AI features are currently disabled")
