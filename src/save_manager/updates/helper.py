"""Atualizador auxiliar independente do processo principal."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from .config import UpdateConfig
from .installer import CONFIRMATION_ENV, finalize_transaction, install_archive, read_state, rollback_transaction

LOGGER = logging.getLogger("save_manager.updates.helper")
CONFIRMATION_TIMEOUT = 45.0


# Handle cleanup download.
def _cleanup_download(archive: Path, checksum: Path) -> None:
    """Remove only the temporary download folder created by the UI."""
    parent = Path(archive).resolve().parent
    if parent != Path(checksum).resolve().parent or not parent.name.startswith("save-manager-update-"):
        return
    shutil.rmtree(parent, ignore_errors=True)


# Wait for process exit.
def wait_for_process_exit(pid: int, timeout: float) -> bool:
    """Wait for exactly ``pid`` without terminating unrelated processes."""
    if pid <= 0:
        return True
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return True
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.1)
    return False


# Wait for confirmation.
def wait_for_confirmation(state_path: Path, timeout: float = CONFIRMATION_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = read_state(state_path)
        if state and state.get("status") == "confirmed":
            return True
        time.sleep(0.2)
    return False


# Run update.
def run_update(
    archive: Path,
    checksum: Path,
    install_root: Path,
    app: Path,
    pid: int,
    version: str,
    package_id: str,
    arguments: list[str] | None = None,
) -> int:
    state_path = install_root / ".save-manager-update-state.json"
    if not wait_for_process_exit(pid, 60.0):
        LOGGER.error("O aplicativo não encerrou no prazo esperado.")
        return 2
    config = UpdateConfig(package_id=package_id)
    transaction = None
    try:
        transaction = install_archive(archive, checksum, install_root, config, expected_version=version, state_path=state_path)
        environment = os.environ.copy()
        environment[CONFIRMATION_ENV] = str(state_path)
        process = subprocess.Popen([str(app), *(arguments or [])], cwd=str(install_root), env=environment)
        if wait_for_confirmation(state_path):
            finalize_transaction(transaction)
            _cleanup_download(archive, checksum)
            return 0
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                LOGGER.warning("Não foi possível encerrar o processo iniciado para confirmação")
        rollback_transaction(transaction)
        transaction = None
        _cleanup_download(archive, checksum)
        return 3
    except Exception:
        LOGGER.exception("Falha ao instalar a atualização")
        if transaction is not None:
            try:
                rollback_transaction(transaction)
                transaction = None
            except Exception:
                LOGGER.exception("Falha ao recuperar a versão anterior")
        _cleanup_download(archive, checksum)
        return 4


# Run.
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Saverino Manager update helper")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--arguments", default="[]")
    args = parser.parse_args(argv)
    try:
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, list) or any(not isinstance(item, str) for item in arguments):
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError):
        parser.error("--arguments deve ser uma lista JSON de strings")
    return run_update(args.archive, args.checksum, args.install_root, args.app, args.pid, args.version, args.package_id, arguments)


if __name__ == "__main__":
    raise SystemExit(main())
