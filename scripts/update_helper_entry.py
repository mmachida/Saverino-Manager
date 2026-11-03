"""PyInstaller entry point for the standalone update helper."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from save_manager.updates.helper import main

_DELEGATED_ENV = "SAVE_MANAGER_UPDATE_HELPER_CHILD"
_DELEGATED_ROOT_ENV = "SAVE_MANAGER_UPDATE_HELPER_ROOT"


# Schedule temporary helper cleanup.
def _schedule_temp_cleanup() -> None:
    temporary_root = os.environ.get(_DELEGATED_ROOT_ENV)
    if not temporary_root:
        return
    try:
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
            cleanup_script = Path(tempfile.gettempdir()) / f"save-manager-updater-cleanup-{os.getpid()}.cmd"
            cleanup_script.write_text(
                "@echo off\r\n"
                ":retry\r\n"
                f'rmdir /s /q "{temporary_root}" >nul 2>&1\r\n'
                f'if exist "{temporary_root}" (\r\n'
                "  ping 127.0.0.1 -n 2 >nul\r\n"
                "  goto retry\r\n"
                ")\r\n"
                'del "%~f0" >nul 2>&1\r\n',
                encoding="ascii",
            )
            subprocess.Popen(
                ["cmd.exe", "/d", "/c", str(cleanup_script)],
                creationflags=flags,
                close_fds=True,
            )
        else:
            shutil.rmtree(temporary_root, ignore_errors=True)
    except OSError:
        pass


# Launch an unlocked helper copy.
def _delegate_frozen_helper() -> bool:
    """Run a temporary helper copy so its installed file can be replaced."""
    if not getattr(sys, "frozen", False) or os.environ.get(_DELEGATED_ENV) == "1":
        return False
    source = Path(sys.executable).resolve()
    temporary_root = Path(tempfile.mkdtemp(prefix="save-manager-updater-"))
    delegated = temporary_root / source.name
    try:
        shutil.copy2(source, delegated)
        environment = os.environ.copy()
        environment[_DELEGATED_ENV] = "1"
        environment[_DELEGATED_ROOT_ENV] = str(temporary_root)
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
        subprocess.Popen(
            [str(delegated), *sys.argv[1:]],
            cwd=os.getcwd(),
            env=environment,
            creationflags=flags,
            close_fds=True,
        )
    except OSError:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise
    return True

if __name__ == "__main__":
    if _delegate_frozen_helper():
        raise SystemExit(0)
    result = main()
    _schedule_temp_cleanup()
    raise SystemExit(result)
