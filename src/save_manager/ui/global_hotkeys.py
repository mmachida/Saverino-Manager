"""Non-blocking global hotkeys for Windows."""

from __future__ import annotations

import ctypes
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
from typing import ClassVar

from PySide6.QtCore import QAbstractNativeEventFilter, Qt, QTimer
from PySide6.QtGui import QKeySequence

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
# Kept for compatibility with existing callers and persisted behavior.
MOD_NOREPEAT = 0x4000
# Keeps repeated key presses from starting duplicate operations.
DISPATCH_DEBOUNCE_SECONDS = 1.0


# Get Qt key map.
def _qt_key_map() -> dict[int, int]:
    """Map Qt's non-printable keys to Windows virtual-key codes."""
    pairs = {
        Qt.Key.Key_Escape: 0x1B,
        Qt.Key.Key_Tab: 0x09,
        Qt.Key.Key_Backtab: 0x09,
        Qt.Key.Key_Backspace: 0x08,
        Qt.Key.Key_Return: 0x0D,
        Qt.Key.Key_Enter: 0x0D,
        Qt.Key.Key_Insert: 0x2D,
        Qt.Key.Key_Delete: 0x2E,
        Qt.Key.Key_Home: 0x24,
        Qt.Key.Key_End: 0x23,
        Qt.Key.Key_Left: 0x25,
        Qt.Key.Key_Up: 0x26,
        Qt.Key.Key_Right: 0x27,
        Qt.Key.Key_Down: 0x28,
        Qt.Key.Key_PageUp: 0x21,
        Qt.Key.Key_PageDown: 0x22,
        Qt.Key.Key_Space: 0x20,
        Qt.Key.Key_CapsLock: 0x14,
        Qt.Key.Key_NumLock: 0x90,
        Qt.Key.Key_ScrollLock: 0x91,
        Qt.Key.Key_Print: 0x2C,
        Qt.Key.Key_Pause: 0x13,
        Qt.Key.Key_Comma: 0xBC,
        Qt.Key.Key_Minus: 0xBD,
        Qt.Key.Key_Period: 0xBE,
        Qt.Key.Key_Slash: 0xBF,
        Qt.Key.Key_Semicolon: 0xBA,
        Qt.Key.Key_Equal: 0xBB,
        Qt.Key.Key_BracketLeft: 0xDB,
        Qt.Key.Key_Backslash: 0xDC,
        Qt.Key.Key_BracketRight: 0xDD,
        Qt.Key.Key_Apostrophe: 0xDE,
        Qt.Key.Key_QuoteLeft: 0xC0,
    }
    for offset in range(24):
        pairs[Qt.Key.Key_F1 + offset] = 0x70 + offset
    return {int(key): value for key, value in pairs.items()}


QT_KEY_MAP = _qt_key_map()


class GlobalHotkeyManager(QAbstractNativeEventFilter):
    """Observe global key presses without consuming them."""

    _MODIFIER_KEYS: ClassVar[dict[int, tuple[int, int]]] = {
        MOD_ALT: (0xA4, 0xA5),
        MOD_CONTROL: (0xA2, 0xA3),
        MOD_SHIFT: (0xA0, 0xA1),
        MOD_WIN: (0x5B, 0x5C),
    }

    # Initialize the object.
    def __init__(self, callback: Callable[[str], None], window=None):
        super().__init__()
        self._callback = callback
        self._window = window
        self._bindings: list[tuple[str, int, int]] = []
        self._key_states: dict[str, bool] = {}
        self._last_dispatch: dict[str, float] = {}
        self._dispatching: set[str] = set()
        self._user32 = None
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(15)
        self._poll_timer.timeout.connect(self._poll_hotkeys)
        if sys.platform == "win32":
            self._user32 = ctypes.windll.user32
            self._user32.GetAsyncKeyState.argtypes = [wintypes.INT]
            self._user32.GetAsyncKeyState.restype = ctypes.c_short

    @staticmethod
    # Parse sequence.
    def parse_sequence(text: str) -> tuple[int, int] | None:
        """Convert one QKeySequence into (Windows modifiers, virtual key)."""
        sequence = QKeySequence(str(text or "").strip())
        if sequence.isEmpty() or sequence.count() != 1:
            return None
        combination = sequence[0]
        qt_key = int(combination.key())
        virtual_key = QT_KEY_MAP.get(qt_key)
        if virtual_key is None and 0x30 <= qt_key <= 0x39:
            virtual_key = qt_key
        if virtual_key is None and 0x41 <= qt_key <= 0x5A:
            virtual_key = qt_key
        if virtual_key is None:
            return None

        qt_modifiers = combination.keyboardModifiers()
        modifiers = 0
        if qt_modifiers & Qt.KeyboardModifier.ControlModifier:
            modifiers |= MOD_CONTROL
        if qt_modifiers & Qt.KeyboardModifier.AltModifier:
            modifiers |= MOD_ALT
        if qt_modifiers & Qt.KeyboardModifier.ShiftModifier:
            modifiers |= MOD_SHIFT
        if qt_modifiers & Qt.KeyboardModifier.MetaModifier:
            modifiers |= MOD_WIN
        return modifiers, virtual_key

    # Apply.
    def apply(self, settings: dict[str, object]) -> list[tuple[str, str]]:
        """Replace bindings while leaving key input available to other apps."""
        self.clear()
        if not bool(settings.get("global_hotkeys_enabled", False)):
            return []
        if self._user32 is None:
            return [("Hotkeys globais", "disponíveis somente no Windows")]

        failures: list[tuple[str, str]] = []
        seen: set[tuple[int, int]] = set()
        for action, label, value in (
            ("create_save", "Criar save", settings.get("create_save_hotkey", "")),
            ("load_save", "Carregar Save", settings.get("load_save_hotkey", "")),
        ):
            text = str(value or "").strip()
            if not text:
                continue
            parsed = self.parse_sequence(text)
            if parsed is None:
                failures.append((label, "combinação não suportada"))
                continue
            modifiers, virtual_key = parsed
            if (modifiers, virtual_key) in seen:
                failures.append((label, "combinação repetida"))
                continue
            seen.add((modifiers, virtual_key))
            self._bindings.append((action, modifiers, virtual_key))

        # Start from the current state so enabling hotkeys does not trigger a
        # save merely because a configured key is already being held.
        self._key_states = {
            action: self._key_is_down(virtual_key)
            for action, _modifiers, virtual_key in self._bindings
        }
        if self._bindings:
            self._poll_timer.start()
        return failures

    # Clear.
    def clear(self):
        self._poll_timer.stop()
        self._bindings.clear()
        self._key_states.clear()
        self._last_dispatch.clear()
        self._dispatching.clear()

    # Poll global key state.
    def _poll_hotkeys(self) -> None:
        for action, modifiers, virtual_key in tuple(self._bindings):
            is_down = self._key_is_down(virtual_key)
            was_down = self._key_states.get(action, False)
            self._key_states[action] = is_down
            if is_down and not was_down and self._modifiers_are_down(modifiers):
                self._dispatch(action)

    # Read key state.
    def _key_is_down(self, virtual_key: int) -> bool:
        try:
            return bool(int(self._user32.GetAsyncKeyState(int(virtual_key))) & 0x8000)
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    # Check modifiers.
    def _modifiers_are_down(self, modifiers: int) -> bool:
        return all(
            bool(modifiers & flag) == any(self._key_is_down(virtual_key) for virtual_key in virtual_keys)
            for flag, virtual_keys in self._MODIFIER_KEYS.items()
        )

    # Dispatch an action.
    def _dispatch(self, action: str) -> None:
        if action in self._dispatching:
            return
        now = time.monotonic()
        if now - self._last_dispatch.get(action, 0.0) < DISPATCH_DEBOUNCE_SECONDS:
            return
        self._last_dispatch[action] = now
        self._dispatching.add(action)
        try:
            self._callback(action)
        finally:
            self._last_dispatch[action] = time.monotonic()
            self._dispatching.discard(action)

    # Ignore native messages.
    def nativeEventFilter(self, _event_type, _message):
        """Keep the old Qt filter entry point without consuming key events."""
        return False

    # Close.
    def close(self):
        self.clear()
