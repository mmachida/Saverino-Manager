from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import sys
import tempfile
from pathlib import Path


# Resolve package root.
def app_root() -> Path:
    """Return the installed package directory, independent of cwd."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


APP_ROOT = app_root()


# Resolve user data root.
def user_data_root() -> Path:
    """Return the stable per-user application data directory."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Saverino Manager"
    if os.name == "nt":
        return Path.home() / "AppData" / "Roaming" / "Saverino Manager"
    return Path.home() / ".config" / "Saverino Manager"


# Migrate legacy data.
def _migrate_legacy_data(data: Path) -> None:
    """Copy the previous catalog into the stable data directory."""
    if (data / "catalog.sqlite3").is_file():
        return
    candidates = [app_root() / "data"]
    if os.name == "nt":
        candidates.append(Path(tempfile.gettempdir()) / "data")
    for legacy in candidates:
        try:
            if legacy.resolve() == data.resolve() or not (legacy / "catalog.sqlite3").is_file():
                continue
        except OSError:
            continue
        for name in (
            "catalog.sqlite3",
            "catalog.sqlite3.before-game-settings.bak",
            "profiles",
            "operations",
            "trash",
            "logs",
        ):
            source = legacy / name
            target = data / name
            if not source.exists() or (target.exists() and source.is_file()):
                continue
            try:
                if source.is_dir():
                    if target.exists() and not target.is_dir():
                        continue
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(source, target)
            except OSError:
                logging.getLogger("save_manager").warning("Não foi possível migrar %s", source, exc_info=True)
                return
        logging.getLogger("save_manager").info("Dados legados migrados de %s para %s", legacy, data)
        return


# Initialize storage.
def initialize(root: Path | None = None) -> dict[str, Path]:
    base = (root or user_data_root()).resolve()
    data = base / "data"
    logs = data / "logs"
    operations = data / "operations"
    profiles = data / "profiles"
    trash = data / "trash"
    try:
        for path in (data, logs, operations, profiles, trash, operations / "staging"):
            path.mkdir(parents=True, exist_ok=True)
        log_path = logs / "app.log"
        handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        root_logger = logging.getLogger("save_manager")
        root_logger.setLevel(logging.INFO)
        if not root_logger.handlers:
            root_logger.addHandler(handler)
        if root is None:
            _migrate_legacy_data(data)
        return {"root": base, "data": data, "logs": logs, "operations": operations, "profiles": profiles, "trash": trash, "backups": base / "Saverino Backup", "catalog": data / "catalog.sqlite3"}
    except OSError as exc:
        raise OSError(f"A pasta do aplicativo não é gravável: {base}") from exc
