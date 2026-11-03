"""Windows startup registration for Saverino Manager."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType

STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE = "Saverino Manager"


class WindowsStartupService:
    """Manage the current user's Windows startup entry."""

    # Initialize the service.
    def __init__(self, registry: ModuleType | None = None, *, platform_name: str | None = None):
        self._registry = registry
        self._platform_name = platform_name or sys.platform

    @property
    # Check Windows support.
    def supported(self) -> bool:
        return self._platform_name == "win32"

    # Build the startup command.
    @staticmethod
    def command() -> str:
        if getattr(sys, "frozen", False):
            arguments = [str(Path(sys.executable).resolve())]
        else:
            executable_path = Path(sys.executable).resolve()
            if executable_path.name.casefold() == "python.exe":
                pythonw = executable_path.with_name("pythonw.exe")
                if pythonw.is_file():
                    executable_path = pythonw
            entrypoint = Path(__file__).resolve().parents[3] / "__main__.py"
            arguments = [str(executable_path), str(entrypoint)]
        return subprocess.list2cmdline(arguments)

    # Resolve the Windows registry module.
    def _registry_module(self) -> ModuleType | None:
        if self._registry is not None:
            return self._registry
        if not self.supported:
            return None
        try:
            import winreg
        except ImportError:
            return None
        return winreg

    # Apply the startup preference.
    def set_enabled(self, enabled: bool) -> bool:
        registry = self._registry_module()
        if registry is None:
            return False
        key = registry.CreateKeyEx(
            registry.HKEY_CURRENT_USER,
            STARTUP_KEY,
            0,
            registry.KEY_SET_VALUE,
        )
        try:
            if enabled:
                registry.SetValueEx(key, STARTUP_VALUE, 0, registry.REG_SZ, self.command())
            else:
                try:
                    registry.DeleteValue(key, STARTUP_VALUE)
                except FileNotFoundError:
                    pass
        finally:
            key.Close()
        return True
