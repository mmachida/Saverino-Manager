"""Configuração de distribuição do atualizador."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass

UPDATE_REPOSITORY_OWNER = "mmachida"
UPDATE_REPOSITORY_NAME = "Saverino-Manager"
UPDATE_CHECK_INTERVAL_HOURS = 24
UPDATE_PACKAGE_PREFIX = "saverino-manager"
APP_EXECUTABLE_NAME = "Saverino Manager.exe"
UPDATER_EXECUTABLE_NAME = "Saverino Manager Updater.exe"
LEGACY_APP_EXECUTABLE_NAME = "SaveManager.exe"
LEGACY_UPDATER_EXECUTABLE_NAME = "SaveManagerUpdater.exe"


# Get platform package id.
def platform_package_id() -> str:
    """Return the exact release package identifier for this executable."""
    system = "windows" if sys.platform.startswith("win") else platform.system().lower()
    machine = platform.machine().lower()
    architecture = {
        "amd64": "x64",
        "x86_64": "x64",
        "x64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine, machine or "unknown")
    return f"{UPDATE_PACKAGE_PREFIX}-{system}-{architecture}"


@dataclass(frozen=True)
class UpdateConfig:
    """Immutable release settings used by both UI and background services."""

    owner: str = UPDATE_REPOSITORY_OWNER
    repository: str = UPDATE_REPOSITORY_NAME
    package_id: str = ""
    interval_hours: int = UPDATE_CHECK_INTERVAL_HOURS

    # Validate initialized fields.
    def __post_init__(self):
        if not self.package_id:
            object.__setattr__(self, "package_id", platform_package_id())

    @property
    # Check configuration state.
    def configured(self) -> bool:
        return bool(self.owner.strip() and self.repository.strip())

    @property
    # Build the API URL.
    def api_url(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repository}/releases/latest"

    @property
    # Build the repository URL.
    def repository_url(self) -> str:
        """Public project page used by the About dialog and documentation."""
        return f"https://github.com/{self.owner}/{self.repository}"

    @property
    # Build package filename.
    def package_name(self) -> str:
        return f"{self.package_id}.zip"

    @property
    # Build checksum filename.
    def checksum_name(self) -> str:
        return f"{self.package_id}.sha256"
