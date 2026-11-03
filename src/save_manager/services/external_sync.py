"""Reconciliação de todos os perfis, independente da seleção e de Qt."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from ..filesystem.identity import filesystem_identity
from .snapshot import _OPERATION_LOCK


@dataclass
class SyncResult:
    profiles: set[UUID] = field(default_factory=set)
    removed_profiles: set[UUID] = field(default_factory=set)
    snapshots: set[UUID] = field(default_factory=set)
    watch_paths: set[str] = field(default_factory=set)
    availability: dict[UUID, tuple[bool, bool]] = field(default_factory=dict)
    imported_snapshots: set[UUID] = field(default_factory=set)


class ExternalSyncService:
    # Initialize the object.
    def __init__(self, catalog, maintenance):
        self.catalog, self.maintenance = catalog, maintenance

    # Scan.
    def scan(self) -> SyncResult | None:
        if not _OPERATION_LOCK.acquire(blocking=False):
            return None
        result = SyncResult()
        try:
            db, policy = self.catalog.db, self.catalog.policy
            for game in db.list_games(True):
                for discovered in self.catalog.discover_profiles(game.id):
                    result.profiles.add(discovered.id)
                for profile in db.list_profiles(game.id, True):
                    try:
                        if self.catalog.sync_profile_folder(profile.id):
                            result.profiles.add(profile.id)
                        profile = db.get_profile(profile.id)
                        if not profile:
                            continue
                        paths = policy.resolve(profile, game_name=game.name, validate_origin=False)
                        if self._profile_namespace_was_removed(paths):
                            if self.maintenance.remove_profile(profile.id):
                                result.profiles.add(profile.id)
                                result.removed_profiles.add(profile.id)
                            continue
                        before_snapshot_ids = {snap.id for snap in db.list_snapshots(profile.id)}
                        if self.maintenance.sync_profile(profile.id):
                            result.snapshots.add(profile.id)
                        after_snapshot_ids = {snap.id for snap in db.list_snapshots(profile.id)}
                        result.imported_snapshots.update(after_snapshot_ids - before_snapshot_ids)
                        profile = db.get_profile(profile.id)
                        if not profile:
                            continue
                        game_name = game.name
                        paths = policy.resolve(profile, game_name=game_name, validate_origin=False)
                        result.availability[profile.id] = (paths.source.exists(), paths.namespace.is_dir())
                        for path in (paths.namespace, paths.backup_base, paths.backup_base.parent, paths.source.parent):
                            self._watch_existing(result, path)
                        for snap in db.list_snapshots(profile.id):
                            relative = policy.validate_manifest_relative(snap.relative_path)
                            if len(relative.parts) == 1:
                                self._watch_existing(result, paths.namespace / relative)
                    except Exception:
                        logging.getLogger("save_manager").exception("Falha ao sincronizar perfil %s", profile.id)
            return result
        finally:
            _OPERATION_LOCK.release()

    @staticmethod
    # Handle profile namespace was removed.
    def _profile_namespace_was_removed(paths) -> bool:
        """Distingue pasta de perfil apagada de uma base temporariamente ausente."""
        if paths.namespace.is_dir():
            return False
        if paths.backup_base.is_dir():
            return True
        # Se a origem ainda está acessível e só o destino foi removido, a
        # ausência da base também representa uma exclusão feita pelo usuário.
        return paths.source.exists() and paths.backup_base.parent.is_dir()

    @staticmethod
    # Watch existing.
    def _watch_existing(result: SyncResult, path: Path) -> None:
        # Observar o pai permite retomar após remoção/reconexão do diretório.
        while not path.exists() and path.parent != path:
            path = path.parent
        if filesystem_identity(path):
            result.watch_paths.add(str(path))
