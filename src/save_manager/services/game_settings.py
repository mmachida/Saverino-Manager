"""Configuração única por jogo e migração verificada de todos os perfis."""

import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

from ..domain.errors import DomainError
from ..domain.models import GameSettings, Profile, StorageMode
from ..filesystem.identity import filesystem_identity
from ..filesystem.inventory import build_inventory
from ..filesystem.migration import MigrationProgress, copy_directory_verified
from ..filesystem.operations import remove_tree_safe
from ..filesystem.policy import PathPolicy, is_reparse, is_within
from ..storage.db import CatalogDB
from ..storage.manifests import read_manifest, write_manifest
from .snapshot import _OPERATION_LOCK

LOGGER = logging.getLogger("save_manager.game_settings")


@dataclass(frozen=True, slots=True)
class GameSettingsResult:
    settings: GameSettings
    transfer_action: str | None = None
    transfer_complete: bool = True
    files_transferred: bool = False


class GameSettingsService:
    # Initialize the object.
    def __init__(self, db: CatalogDB, policy: PathPolicy):
        self.db, self.policy = db, policy
        self.recovery_root = policy.data_root / "game-settings-transactions"
        self.recovery_root.mkdir(parents=True, exist_ok=True)

    # Validate.
    def validate(self, game_id: UUID, settings: GameSettings) -> GameSettings:
        profiles = self.db.list_profiles(game_id, True)
        game = self.db.get_game(game_id)
        current_settings = self.db.get_game_settings(game_id)
        reference_source_type = current_settings.source_type if current_settings else (
            profiles[0].source_type if profiles else None
        )
        reference_source_path = current_settings.source_path if current_settings else (
            profiles[0].source_path if profiles else None
        )
        source_unchanged = bool(
            reference_source_path
            and reference_source_type == settings.source_type
            and os.path.normcase(os.path.abspath(reference_source_path))
            == os.path.normcase(os.path.abspath(settings.source_path))
        )
        source = self.policy.validate_source(
            settings.source_path,
            settings.source_type,
            allow_missing=source_unchanged,
        )
        if not source.exists() and not source_unchanged:
            raise DomainError("Selecione um arquivo ou pasta de save existente")
        settings = replace(settings, source_path=str(source), backup_base_path=(
            str(Path(settings.backup_base_path).absolute()) if settings.storage_mode == StorageMode.CUSTOM else ""
        ))
        sample = Profile(game_id, "Perfil", settings.source_type, settings.source_path,
                         settings.storage_mode, settings.backup_base_path)
        base = self.policy.resolve_backup_base(sample)
        if (settings.storage_mode == StorageMode.APP_FOLDER
                and (current_settings is None or current_settings.storage_mode != StorageMode.APP_FOLDER)):
            writable_path = base if base.exists() else self.policy.app_root
            if not os.access(writable_path, os.W_OK):
                raise DomainError("A pasta do aplicativo não permite gravação. Escolha outro destino de backups.")
        for path in (source, base):
            if any(is_reparse(part) for part in (path, *path.parents)):
                raise DomainError("Links simbólicos/junctions não são suportados")
        all_profiles = [p for game in self.db.list_games(True) for p in self.db.list_profiles(game.id, True)]
        repositories = [self.policy.resolve(p, validate_origin=False).namespace for p in all_profiles]
        self.policy.validate_source(
            source,
            settings.source_type,
            repositories,
            allow_missing=source_unchanged,
        )
        for profile in profiles or [sample]:
            if profile.source_type != settings.source_type and self.db.list_snapshots(profile.id, True):
                raise DomainError("O tipo arquivo/pasta não pode mudar enquanto houver backups desse jogo")
            candidate = replace(profile, source_type=settings.source_type, source_path=settings.source_path,
                                storage_mode=settings.storage_mode, backup_base_path=settings.backup_base_path)
            namespace_parent = self.policy.namespace_parent(candidate, base, game.name if game else None, str(game_id))
            profile_name = self.policy.resolve(profile, validate_origin=False).namespace.name if profiles else "Perfil"
            namespace = namespace_parent / profile_name
            for origin in [source, *[Path(p.source_path) for p in all_profiles]]:
                if is_within(namespace, origin) or is_within(origin, namespace):
                    raise DomainError("O destino de backups colide com uma origem de save")
        return settings

    # Check backup namespace migration.
    def destination_migration_required(self, game_id: UUID, settings: GameSettings) -> bool:
        game = self.db.get_game(game_id)
        if not game:
            return False
        for profile in self.db.list_profiles(game_id, True):
            old_namespace = self.policy.resolve(
                profile,
                game_name=game.name,
                validate_origin=False,
            ).namespace
            candidate = replace(
                profile,
                source_type=settings.source_type,
                source_path=settings.source_path,
                storage_mode=settings.storage_mode,
                backup_base_path=settings.backup_base_path,
            )
            base = self.policy.resolve_backup_base(candidate)
            namespace = self.policy.namespace_parent(
                candidate,
                base,
                game.name,
                str(game_id),
            ) / old_namespace.name
            if namespace != old_namespace:
                return True
        return False

    # Configure.
    def configure(self, game_id: UUID, settings: GameSettings, *, copy_backups: bool = True,
                  move_backups: bool = False, progress=None) -> GameSettingsResult:
        if not _OPERATION_LOCK.acquire(blocking=False):
            raise DomainError("Já existe uma operação em andamento")
        staged: list[Path] = []
        moved: list[tuple[Path, Path]] = []
        journal = None
        try:
            game = self.db.get_game(game_id)
            if not game:
                raise DomainError("Jogo não encontrado")
            self.recover_pending()
            from ..storage.journal import JournalStore
            if JournalStore(self.policy.data_root / "operations").pending():
                raise DomainError("Resolva as operações interrompidas antes de alterar o jogo")
            settings = self.validate(game_id, settings)
            if (settings == self.db.get_game_settings(game_id)
                    and not self.destination_migration_required(game_id, settings)):
                return GameSettingsResult(settings)
            operation_id = str(uuid4())
            profiles = self.db.list_profiles(game_id, True)
            copy_profile_ids = set()
            copy_profile_weights = {}
            destination_changed = False
            for profile in profiles:
                old_namespace = self.policy.resolve(profile, game_name=game.name, validate_origin=False).namespace
                candidate = replace(profile, source_type=settings.source_type, source_path=settings.source_path,
                                    storage_mode=settings.storage_mode, backup_base_path=settings.backup_base_path)
                base = self.policy.resolve_backup_base(candidate)
                namespace = self.policy.namespace_parent(candidate, base, game.name, str(game_id)) / old_namespace.name
                destination_changed = destination_changed or namespace != old_namespace
                if copy_backups and namespace != old_namespace and not namespace.exists():
                    copy_profile_ids.add(profile.id)
                    if not old_namespace.is_dir():
                        raise DomainError(f"Reconecte a pasta de backups antes de mudar o destino: {old_namespace}")
                    inventory = build_inventory(old_namespace, "directory", hashes=False)
                    copy_profile_weights[profile.id] = max(
                        1,
                        sum(max(entry.size, 1) if entry.kind == "file" else 1 for entry in inventory.entries),
                    )
            total_copy_weight = sum(copy_profile_weights.values())
            completed_copy_weight = 0
            records = []
            copied_sources: list[Path] = []
            for profile in profiles:
                old_namespace = self.policy.resolve(profile, game_name=game.name, validate_origin=False).namespace
                old_base = self.policy.resolve_backup_base(profile)
                if (not is_within(old_namespace, old_base)
                        and profile.storage_mode == StorageMode.APP_FOLDER
                        and is_within(old_namespace, self.policy.legacy_app_folder_backup_root)):
                    old_base = self.policy.legacy_app_folder_backup_root
                candidate = replace(profile, source_type=settings.source_type, source_path=settings.source_path,
                                    storage_mode=settings.storage_mode, backup_base_path=settings.backup_base_path)
                base = self.policy.resolve_backup_base(candidate)
                namespace = self.policy.namespace_parent(candidate, base, game.name if game else None, str(game_id)) / old_namespace.name
                if namespace != old_namespace:
                    if namespace.exists():
                        if not namespace.is_dir() or is_reparse(namespace):
                            raise DomainError(f"Já existe um item incompatível no novo destino: {namespace}")
                        # A pasta pode ter sido criada anteriormente pelo
                        # usuário ou por outra instalação.  Adotá-la pelo
                        # nome do perfil preserva os saves que já estão lá.
                        adopted_namespace = True
                    else:
                        adopted_namespace = False
                    for other_game in self.db.list_games(True):
                        for other in self.db.list_profiles(other_game.id, True):
                            if other.id != profile.id and self.policy.resolve(other, game_name=other_game.name, validate_origin=False).namespace == namespace:
                                raise DomainError("O destino está reservado para outro perfil")
                    if adopted_namespace:
                        # O conteúdo existente já é o destino publicado; não
                        # o copie por cima nem remova o repositório anterior.
                        pass
                    elif not copy_backups:
                        # A configuração pode apontar para um destino novo sem
                        # levar os backups atuais.  Crie apenas o namespace
                        # vazio para que a sincronização preserve o perfil.
                        namespace.mkdir(parents=True, exist_ok=True)
                    else:
                        if not old_namespace.is_dir():
                            raise DomainError(f"Reconecte a pasta de backups antes de mudar o destino: {old_namespace}")
                        base.mkdir(parents=True, exist_ok=True)
                        stage = base / f".game-settings-{operation_id}-{profile.id}"
                        staged.append(stage)
                        # Aggregate profile progress.
                        def report_profile_progress(
                            item: MigrationProgress,
                            completed_before=completed_copy_weight,
                            profile_weight=copy_profile_weights.get(profile.id, 1),
                        ) -> None:
                            if not progress:
                                return
                            fraction = item.percent / 100
                            overall = round(
                                (completed_before + profile_weight * fraction)
                                * 100
                                / max(total_copy_weight, 1)
                            )
                            progress(MigrationProgress(overall, item.relative))

                        copy_directory_verified(old_namespace, stage, progress=report_profile_progress)
                        namespace.parent.mkdir(parents=True, exist_ok=True)
                        stage.rename(namespace)
                        moved.append((old_namespace, namespace))
                        copied_sources.append(old_namespace)
                        completed_copy_weight += copy_profile_weights.get(profile.id, 1)
                manifest_path = self.policy.metadata_dir(profile) / "profile.json"
                old = read_manifest(manifest_path)
                new = dict(old)
                new.update(source_type=settings.source_type.value, source_path=settings.source_path,
                           storage_mode=settings.storage_mode.value, backup_base_path=settings.backup_base_path,
                           namespace=str(namespace), filesystem_identity=filesystem_identity(namespace))
                records.append({"profile_id": str(profile.id), "old": old, "new": new,
                                "old_namespace": str(old_namespace), "new_namespace": str(namespace),
                                "old_base": str(old_base)})
            journal = self.recovery_root / f"{operation_id}.json"
            write_manifest(journal, {"operation_id": operation_id, "game_id": str(game_id), "profiles": records})
            for record in records:
                write_manifest(self.policy.data_root / "profiles" / record["profile_id"] / "profile.json", record["new"])
            self.db.set_game_settings(game_id, settings, operation_id)
            transfer_complete = True
            transfer_action = (
                "move" if move_backups else "copy"
            ) if destination_changed and (move_backups or copy_backups) else None
            if move_backups:
                copied_source_set = set(copied_sources)
                for record in records:
                    old_namespace = Path(record["old_namespace"])
                    new_namespace = Path(record["new_namespace"])
                    if old_namespace == new_namespace or not old_namespace.exists():
                        continue
                    if old_namespace not in copied_source_set:
                        # An existing destination is adopted without replacing
                        # either folder, so retain the original data safely.
                        transfer_complete = False
                        continue
                    try:
                        remove_tree_safe(old_namespace, old_namespace.parent)
                        old_base = Path(record.get("old_base") or old_namespace.parent)
                        if not self._remove_empty_source_parents(old_namespace, old_base):
                            LOGGER.warning("Could not remove empty source backup folders below %s", old_base)
                            transfer_complete = False
                    except Exception:
                        LOGGER.exception("Could not remove the old backup folder after a verified move: %s", old_namespace)
                        transfer_complete = False
            try:
                journal.unlink()
            except OSError:
                pass  # A recuperação repetirá a configuração já confirmada.
            return GameSettingsResult(settings, transfer_action, transfer_complete, bool(copied_sources))
        except Exception:
            if journal and journal.exists():
                self.recover_pending()
            for old_namespace, namespace in reversed(moved):
                try:
                    if namespace.exists() and not old_namespace.exists():
                        namespace.rename(old_namespace)
                except OSError:
                    # Preserve both locations if a concurrent process prevents
                    # rollback; the journal remains the recovery source.
                    pass
            raise
        finally:
            try:
                for path in staged:
                    if path.exists():
                        remove_tree_safe(path, path.parent)
            finally:
                _OPERATION_LOCK.release()

    # Remove empty source parents.
    @staticmethod
    def _remove_empty_source_parents(namespace: Path, boundary: Path) -> bool:
        """Remove empty ancestors without deleting the backup root."""
        if namespace == boundary or not is_within(namespace, boundary):
            return False
        current = namespace.parent
        while current != boundary:
            if not is_within(current, boundary) or is_reparse(current):
                return False
            try:
                current.rmdir()
            except FileNotFoundError:
                pass
            except OSError:
                try:
                    if current.is_dir() and any(current.iterdir()):
                        return True
                except OSError:
                    return False
                return False
            current = current.parent
        return True

    # Recover pending.
    def recover_pending(self) -> None:
        """SQLite confirma alterações antes da remoção das origens."""
        for path in self.recovery_root.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            game_id = UUID(data["game_id"])
            committed = self.db.get_setting(f"game-settings-commit:{game_id}") == data["operation_id"]
            for record in data["profiles"]:
                profile_id = UUID(record["profile_id"])
                profile = self.db.get_profile(profile_id)
                if not profile or profile.game_id != game_id:
                    raise DomainError("Perfil inválido no registro de alteração de configurações")
                write_manifest(self.policy.metadata_dir(profile) / "profile.json", record["new" if committed else "old"])
                if not committed:
                    old_namespace = Path(record.get("old_namespace", ""))
                    new_namespace = Path(record.get("new_namespace", ""))
                    if old_namespace and new_namespace and new_namespace.exists() and not old_namespace.exists():
                        new_namespace.rename(old_namespace)
            path.unlink()
