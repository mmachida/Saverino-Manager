"""Centralized application localization for the supported UI languages."""

from __future__ import annotations

import json
import locale
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QLocale, QObject, Signal

LOGGER = logging.getLogger("save_manager.i18n")
SUPPORTED_LANGUAGES = ("en-US", "pt-BR")


class LanguageManager(QObject):
    """Loads JSON catalogs and notifies open widgets when the language changes."""

    language_changed = Signal(str)

    # Initialize the object.
    def __init__(self, language: str | None = None, parent=None):
        super().__init__(parent)
        self._catalogs = {code: self._load_catalog(code) for code in SUPPORTED_LANGUAGES}
        self._language = self.normalize(language) or self.detect_system_language()

    @staticmethod
    # Normalize.
    def normalize(language: object) -> str | None:
        if not language:
            return None
        value = str(language).replace("_", "-").strip()
        for code in SUPPORTED_LANGUAGES:
            if value.casefold() == code.casefold():
                return code
        return None

    @staticmethod
    # Detect system language.
    def detect_system_language() -> str:
        candidates = []
        try:
            candidates.append(QLocale.system().name())
        except Exception:
            pass
        try:
            candidates.append(locale.getlocale()[0] or "")
        except Exception:
            pass
        if any(str(value).casefold().startswith("pt") for value in candidates):
            return "pt-BR"
        return "en-US"

    @property
    # Return active language.
    def language(self) -> str:
        return self._language

    # Set language.
    def set_language(self, language: str) -> str:
        normalized = self.normalize(language) or "en-US"
        if normalized != self._language:
            self._language = normalized
            self.language_changed.emit(normalized)
        return normalized

    # Translate a message.
    def tr(self, key: str, **params: object) -> str:
        catalog = self._catalogs.get(self._language, {})
        english = self._catalogs.get("en-US", {})
        template = catalog.get(key)
        if template is None:
            template = english.get(key)
        if template is None:
            LOGGER.warning("Missing translation: %s (%s)", key, self._language)
            template = key
        try:
            return str(template).format(**params)
        except (KeyError, IndexError, ValueError):
            LOGGER.warning("Invalid translation parameters: %s", key)
            return str(template)

    # Return language label.
    def language_name(self, language: str) -> str:
        catalog = self._catalogs.get(self._language, {})
        return catalog.get(f"language.{language}", language)

    # Find language flag.
    def flag_path(self, language: str) -> Path:
        filename = f"{language}.svg"
        candidates = []
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            candidates.append(Path(bundle_root) / "save_manager" / "assets" / "flags" / filename)
        module_path = Path(__file__).resolve()
        candidates.append(module_path.parent / "assets" / "flags" / filename)
        candidates.append(Path(sys.executable).resolve().parent / "save_manager" / "assets" / "flags" / filename)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    @staticmethod
    # Load catalog.
    def _load_catalog(language: str) -> dict[str, str]:
        path = Path(__file__).resolve().parent / "locales" / f"{language}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            LOGGER.exception("Could not load translation catalog %s", language)
            return {}
