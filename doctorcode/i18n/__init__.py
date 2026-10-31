"""Runtime i18n: modular ru/en/de packs, auto-detection, no GUI dependency."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Mapping

from doctorcode.i18n import de, en, ru

LANGUAGES = ("ru", "en", "de")
DEFAULT_LANGUAGE = "ru"
FLAGS = {"ru": "🇷🇺", "en": "🇬🇧", "de": "🇩🇪"}
LANGUAGE_NAMES = {"ru": "Русский", "en": "English", "de": "Deutsch"}
PACKS: dict[str, Mapping[str, str]] = {
    "ru": ru.MESSAGES,
    "en": en.MESSAGES,
    "de": de.MESSAGES,
}


def detect_system_language() -> str:
    """Windows LANGID / POSIX locale environment; fail-safe to Russian.

    Deliberately does not use the deprecated ``locale`` module.  English maps to
    ``en`` and German to ``de``; everything else falls back to the owner's
    language, Russian, per v2.8 §5.
    """
    try:
        if sys.platform == "win32":
            import ctypes

            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = int(langid) & 0x3FF
            if primary == 0x09:
                return "en"
            if primary == 0x07:
                return "de"
            return "ru"
        raw = (
            os.environ.get("LC_ALL")
            or os.environ.get("LC_MESSAGES")
            or os.environ.get("LANG")
            or ""
        ).lower()
        if raw.startswith("en"):
            return "en"
        if raw.startswith("de"):
            return "de"
        return "ru"
    except Exception:
        return "ru"


def resolve_language(preference: str = "") -> str:
    """Explicit supported language wins; empty means system auto-detection."""
    if preference in PACKS:
        return preference
    return detect_system_language()


class Translator:
    """Thread-safe translator with a Russian fallback for every key."""

    def __init__(self, language: str = "") -> None:
        self._lock = threading.RLock()
        self._language = resolve_language(language)

    @property
    def language(self) -> str:
        with self._lock:
            return self._language

    @property
    def flag(self) -> str:
        return FLAGS.get(self.language, FLAGS[DEFAULT_LANGUAGE])

    def set_language(self, language: str) -> str:
        if language not in PACKS:
            language = DEFAULT_LANGUAGE
        with self._lock:
            self._language = language
        return language

    def t(self, key: str, **params: object) -> str:
        language = self.language
        text = PACKS.get(language, {}).get(key)
        if text is None:
            text = PACKS[DEFAULT_LANGUAGE].get(key, key)
        if not params:
            return text
        try:
            return text.format(**params)
        except (KeyError, IndexError, ValueError):
            return text
