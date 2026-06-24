import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).parent.parent
_CONFIG_PATH = _ROOT / "config" / "settings.yaml"

load_dotenv(_ROOT / ".env")


def load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # .env overrides yaml (keys never in source control)
    google_places = cfg.setdefault("google_places", {})
    google_places["api_key"] = (
        os.getenv("GOOGLE_PLACES_API_KEY") or google_places.get("api_key", "")
    )

    llm = cfg.setdefault("llm", {})
    llm["api_key"] = os.getenv("ANTHROPIC_API_KEY") or llm.get("api_key", "")

    return cfg


CONFIG = load_config()
