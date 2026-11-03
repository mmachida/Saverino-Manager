"""Reprodução não bloqueante dos efeitos sonoros da interface."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import ClassVar

LOGGER = logging.getLogger("save_manager.audio")

try:
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtMultimedia import QSoundEffect

    class AudioManager(QObject):
        """Mantém os dois efeitos carregados e os toca sem bloquear a UI."""

        _FILES: ClassVar[dict[str, str]] = {
            "import": "sfx_keyboard.wav",
            "load": "sfx_mouse.wav",
            "auto_save": "sfx_keyboard.wav",
        }

        # Initialize the object.
        def __init__(self, parent=None):
            super().__init__(parent)
            self._enabled = {"import": True, "load": True, "auto_save": False}
            self._volume = 0.5
            self._effects: dict[str, QSoundEffect] = {}
            self._missing: set[str] = set()
            for kind, filename in self._FILES.items():
                try:
                    path = self.resolve_path(filename)
                except Exception:
                    LOGGER.exception("Não foi possível localizar o efeito sonoro %s", kind)
                    path = None
                if path is None:
                    self._missing.add(kind)
                    LOGGER.warning("Efeito sonoro ausente: %s", filename)
                    continue
                try:
                    effect = QSoundEffect(self)
                    effect.setLoopCount(1)
                    effect.setVolume(self._volume)
                    effect.setSource(QUrl.fromLocalFile(str(path)))
                    effect.statusChanged.connect(
                        lambda effect=effect, kind=kind: self._log_error_status(kind, effect),
                    )
                except Exception:
                    LOGGER.exception("Não foi possível preparar o efeito sonoro %s", kind)
                    continue
                self._effects[kind] = effect

        @staticmethod
        # Resolve path.
        def resolve_path(filename: str) -> Path | None:
            """Find an audio file in source, installed and PyInstaller layouts."""
            module_path = Path(__file__).resolve()
            candidates: list[Path] = []
            bundle_root = getattr(sys, "_MEIPASS", None)
            if bundle_root:
                candidates.append(Path(bundle_root) / "sfx" / filename)
            candidates.append(module_path.parent.parent / "sfx" / filename)
            # In a source checkout this is the project-root copy; keep this
            # lookup guarded because an installed module may have fewer parents.
            try:
                candidates.append(module_path.parents[3] / "sfx" / filename)
            except IndexError:
                pass
            candidates.extend(
                (
                    Path(sys.executable).resolve().parent / "sfx" / filename,
                    Path.cwd() / "sfx" / filename,
                )
            )
            for candidate in candidates:
                try:
                    if candidate.is_file():
                        return candidate
                except OSError:
                    continue
            return None

        # Configure.
        def configure(self, settings: dict[str, object]) -> None:
            self._enabled["import"] = bool(settings.get("sound_effect_import_enabled", True))
            self._enabled["load"] = bool(settings.get("sound_effect_load_enabled", True))
            self._enabled["auto_save"] = bool(settings.get("sound_effect_auto_save_enabled", False))
            try:
                value = int(settings.get("sound_effect_volume", 50))
            except (TypeError, ValueError):
                value = 50
            self._volume = max(0, min(100, value)) / 100.0
            for effect in self._effects.values():
                try:
                    effect.setVolume(self._volume)
                except Exception:
                    LOGGER.exception("Não foi possível ajustar o volume do efeito sonoro")

        # Play.
        def play(self, kind: str) -> None:
            """Play one configured effect; failures are logged and ignored."""
            if kind not in self._FILES or not self._enabled.get(kind, False) or self._volume <= 0:
                return
            effect = self._effects.get(kind)
            if effect is None:
                return
            try:
                effect.setVolume(self._volume)
                effect.play()
            except Exception:
                LOGGER.exception("Não foi possível reproduzir o efeito sonoro %s", kind)

        @staticmethod
        # Log error status.
        def _log_error_status(kind: str, effect: QSoundEffect) -> None:
            if effect.status() == QSoundEffect.Status.Error:
                LOGGER.warning("Não foi possível carregar o efeito sonoro %s", kind)

except ImportError:  # permite importar serviços sem Qt instalado

    class AudioManager:  # type: ignore[no-redef]
        """Fallback silencioso para ambientes sem PySide6/QtMultimedia."""

        # Initialize the object.
        def __init__(self, parent=None):
            del parent

        @staticmethod
        # Resolve path.
        def resolve_path(filename: str) -> Path | None:
            del filename
            return None

        # Configure.
        def configure(self, settings: dict[str, object]) -> None:
            del settings

        # Play.
        def play(self, kind: str) -> None:
            del kind
