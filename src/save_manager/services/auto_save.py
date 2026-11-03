"""Persist and validate automatic save schedules."""

from __future__ import annotations

from uuid import UUID, uuid4

from ..domain.errors import DomainError
from ..domain.models import SnapshotStatus, SourceType
from ..storage.db import CatalogDB

AUTO_SAVE_SETTING_KEY = "auto_saves"
MIN_INTERVAL_SECONDS = 15
MAX_INTERVAL_SECONDS = 7_200


class AutoSaveService:
    # Initialize service.
    def __init__(self, db: CatalogDB):
        self.db = db

    # Read all schedules.
    def list_all(self) -> list[dict[str, object]]:
        raw = self.db.get_setting(AUTO_SAVE_SETTING_KEY, [])
        if not isinstance(raw, list):
            return []
        result = []
        for item in raw:
            try:
                entry = self._normalize(item)
            except (DomainError, KeyError, TypeError, ValueError):
                continue
            if entry["target_type"] in ("file", "folder"):
                profile = self.db.get_profile(UUID(str(entry["profile_id"])))
                if profile:
                    entry["target_type"] = (
                        "folder" if profile.source_type == SourceType.DIRECTORY else "file"
                    )
            result.append(entry)
        return result

    # List project schedules.
    def list_project(self, project_id: UUID | str) -> list[dict[str, object]]:
        project_id = str(project_id)
        return [item for item in self.list_all() if item["project_id"] == project_id]

    # List enabled schedules.
    def list_enabled(self) -> list[dict[str, object]]:
        return [item for item in self.list_all() if item["enabled"]]

    # Find profile schedule.
    def for_profile(self, profile_id: UUID | str) -> dict[str, object] | None:
        profile_id = str(profile_id)
        return next((item for item in self.list_all()
                     if item["target_type"] == "profile" and item["profile_id"] == profile_id), None)

    # Find snapshot schedule.
    def for_snapshot(self, snapshot_id: UUID | str) -> dict[str, object] | None:
        snapshot_id = str(snapshot_id)
        return next((item for item in self.list_all()
                     if item["target_type"] in ("file", "folder")
                     and item["snapshot_id"] == snapshot_id), None)

    # Check project activity.
    def project_is_active(self, project_id: UUID | str) -> bool:
        return any(item["enabled"] for item in self.list_project(project_id))

    # Add profile schedule.
    def add_profile(self, profile_id: UUID | str, interval_seconds: int) -> dict[str, object]:
        profile = self.db.get_profile(UUID(str(profile_id)))
        if not profile or profile.archived_at:
            raise DomainError("Perfil não encontrado ou arquivado")
        if self.for_profile(profile.id):
            raise DomainError("Este perfil já possui uma configuração de Auto-Save")
        entry = self._new_entry(profile.game_id, profile.id, "profile", None, profile.name, interval_seconds)
        self._store([*self.list_all(), entry])
        return entry

    # Add snapshot schedule.
    def add_snapshot(self, snapshot_id: UUID | str, interval_seconds: int) -> dict[str, object]:
        snapshot = self.db.get_snapshot(UUID(str(snapshot_id)))
        profile = self.db.get_profile(snapshot.profile_id) if snapshot else None
        if (not snapshot or not profile or profile.archived_at
                or snapshot.status in (SnapshotStatus.TRASHED, SnapshotStatus.MISSING)):
            raise DomainError("Save não encontrado para o Auto-Save")
        if self.for_snapshot(snapshot.id):
            raise DomainError("Este save já possui uma configuração de Auto-Save")
        target_type = "folder" if profile.source_type == SourceType.DIRECTORY else "file"
        entry = self._new_entry(profile.game_id, profile.id, target_type, snapshot.id, snapshot.name, interval_seconds)
        self._store([*self.list_all(), entry])
        return entry

    # Configure profile schedule.
    def configure_profile(self, profile_id: UUID | str, interval_seconds: int, *, enabled: bool = True) -> dict[str, object]:
        current = self.for_profile(profile_id)
        if current is None:
            return self.add_profile(profile_id, interval_seconds)
        updated = dict(current)
        updated["interval_seconds"] = self._interval(interval_seconds)
        updated["enabled"] = bool(enabled)
        self._replace_entry(updated)
        return updated

    # Enable or pause schedule.
    def set_enabled(self, entry_id: str, enabled: bool) -> dict[str, object] | None:
        current = next((item for item in self.list_all() if item["id"] == str(entry_id)), None)
        if current is None:
            return None
        updated = dict(current)
        updated["enabled"] = bool(enabled)
        self._replace_entry(updated)
        return updated

    # Update project schedules.
    def update_project(self, project_id: UUID | str, updated: list[dict[str, object]]) -> None:
        project_id = str(project_id)
        existing = {str(item["id"]): item for item in self.list_project(project_id)}
        normalized = []
        for candidate in updated:
            entry_id = str(candidate.get("id", ""))
            original = existing.get(entry_id)
            if original is None:
                raise DomainError("Configuração de Auto-Save inválida")
            if (candidate.get("profile_id") != original["profile_id"]
                    or candidate.get("snapshot_id") != original["snapshot_id"]
                    or candidate.get("target_type") != original["target_type"]):
                raise DomainError("O destino do Auto-Save não pode ser alterado nesta tela")
            entry = dict(original)
            entry["interval_seconds"] = self._interval(candidate.get("interval_seconds"))
            entry["enabled"] = bool(candidate.get("enabled"))
            normalized.append(entry)
        other_projects = [item for item in self.list_all() if item["project_id"] != project_id]
        self._store([*other_projects, *normalized])

    # Remove schedule.
    def remove(self, entry_id: str) -> None:
        self._store([item for item in self.list_all() if item["id"] != str(entry_id)])

    # Remove project schedules.
    def remove_project(self, project_id: UUID | str) -> None:
        project_id = str(project_id)
        self._store([item for item in self.list_all() if item["project_id"] != project_id])

    # Remove profile schedules.
    def remove_profile(self, profile_id: UUID | str) -> None:
        profile_id = str(profile_id)
        self._store([item for item in self.list_all() if item["profile_id"] != profile_id])

    # Remove snapshot schedules.
    def remove_snapshot(self, snapshot_id: UUID | str) -> None:
        snapshot_id = str(snapshot_id)
        self._store([item for item in self.list_all() if item["snapshot_id"] != snapshot_id])

    # Create a schedule record.
    def _new_entry(self, project_id, profile_id, target_type, snapshot_id, target_name, interval_seconds):
        return {
            "id": str(uuid4()),
            "project_id": str(project_id),
            "profile_id": str(profile_id),
            "target_type": target_type,
            "snapshot_id": str(snapshot_id) if snapshot_id else None,
            "target_name": str(target_name),
            "interval_seconds": self._interval(interval_seconds),
            "enabled": True,
        }

    # Validate schedule record.
    @classmethod
    def _normalize(cls, item):
        if not isinstance(item, dict):
            raise TypeError("Invalid auto-save record")
        target_type = str(item["target_type"])
        if target_type not in ("file", "folder", "profile"):
            raise ValueError("Invalid auto-save type")
        project_id, profile_id = str(UUID(str(item["project_id"]))), str(UUID(str(item["profile_id"])))
        snapshot_id = str(UUID(str(item["snapshot_id"]))) if item.get("snapshot_id") else None
        if (target_type == "profile") != (snapshot_id is None):
            raise ValueError("Invalid auto-save target")
        try:
            stored_interval = int(item["interval_seconds"])
        except (OverflowError, TypeError, ValueError) as exc:
            raise DomainError("Invalid stored Auto-Save interval") from exc
        if 1 <= stored_interval < MIN_INTERVAL_SECONDS:
            stored_interval = MIN_INTERVAL_SECONDS
        if stored_interval > MAX_INTERVAL_SECONDS:
            stored_interval = MAX_INTERVAL_SECONDS
        interval = cls._interval(stored_interval)
        return {
            "id": str(UUID(str(item["id"]))),
            "project_id": project_id,
            "profile_id": profile_id,
            "target_type": target_type,
            "snapshot_id": snapshot_id,
            "target_name": str(item.get("target_name", "")),
            "interval_seconds": interval,
            "enabled": bool(item.get("enabled", False)),
        }

    # Validate interval seconds.
    @staticmethod
    def _interval(value) -> int:
        try:
            interval = int(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise DomainError("O intervalo precisa ser informado em segundos") from exc
        if interval < MIN_INTERVAL_SECONDS:
            raise DomainError(f"O intervalo mínimo é de {MIN_INTERVAL_SECONDS} segundos")
        if interval > MAX_INTERVAL_SECONDS:
            raise DomainError(f"O intervalo máximo é de {MAX_INTERVAL_SECONDS} segundos")
        return interval

    # Replace one schedule.
    def _replace_entry(self, replacement):
        entries = self.list_all()
        entries = [replacement if item["id"] == replacement["id"] else item for item in entries]
        self._store(entries)

    # Save all schedules.
    def _store(self, entries):
        self.db.set_setting(AUTO_SAVE_SETTING_KEY, entries)
