from __future__ import annotations

from markus_dispatcher.config import FOOTER_PATH
from markus_dispatcher.core.instruction import DEFAULT_FOOTER


def load_footer() -> str:
    FOOTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not FOOTER_PATH.exists():
        return DEFAULT_FOOTER
    return FOOTER_PATH.read_text(encoding="utf-8")


def save_footer(text: str) -> None:
    FOOTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    FOOTER_PATH.write_text(text, encoding="utf-8")
