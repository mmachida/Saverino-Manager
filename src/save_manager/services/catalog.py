from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from uuid import UUID

from ..domain.errors import DomainError, IntegrityError, OperationError, PathPolicyError
from ..domain.models import (
    Game,
    GameSettings,
    Profile,
    SourceType,
    StorageMode,
    normalize_name,
    utc_now,
    validate_folder_name,
    validate_name,
)
from ..filesystem.identity import filesystem_identity, visible_children
from ..filesystem.operations import remove_tree_safe
from ..filesystem.policy import INTERNAL_NAMESPACE_NAMES, PathPolicy, is_reparse, mark_hidden_windows
from ..services.snapshot import _OPERATION_LOCK
from ..storage.db import CatalogDB
from ..storage.manifests import read_manifest, write_manifest
from .game_settings import GameSettingsResult, GameSettingsService
from .synchronization import matching_payloads


class CatalogService:
    # Initialize the object.
    def __init__(self, db: CatalogDB, policy: PathPolicy):
        self.db, self.policy = db, policy
        self.game_settings = GameSettingsService(db, policy)

    # Create game.
    def create_game(self, name: str, settings: GameSettings | None = None) -> Game:
        game = Game(validate_folder_name(name, "Nome do jogo"))
        if self.game_name_exists(game.name):
            raise DomainError("Já existe um jogo com esse nome")
        if settings:
            settings = self.game_settings.validate(game.id, settings)
            self._create_game_backup_folder(game, settings)
        self.db.add_game(game, settings)
        return game

    # Check game name conflict.
    def game_name_exists(self, name: str, *, excluding: UUID | None = None) -> bool:
        candidate = Game(validate_folder_name(name, "Nome do jogo"))
        return any(
            game.id != excluding and game.normalized_name == candidate.normalized_name
            for game in self.db.list_games(include_archived=True)
        )

    # Create the game's backup folder.
    def _create_game_backup_folder(self, game: Game, settings: GameSettings) -> None:
        sample = Profile(
            game.id,
            "Perfil",
            settings.source_type,
            settings.source_path,
            settings.storage_mode,
            settings.backup_base_path,
        )
        base = self.policy.resolve_backup_base(sample)
        game_folder = self.policy.namespace_parent(sample, base, game.name, str(game.id))
        game_folder.mkdir(parents=True, exist_ok=True)

    # Configure game.
    def configure_game(self, game_id: UUID, settings: GameSettings, *, copy_backups: bool = True,
                       move_backups: bool = False, progress=None) -> GameSettingsResult:
        return self.game_settings.configure(
            game_id,
            settings,
            copy_backups=copy_backups,
            move_backups=move_backups,
            progress=progress,
        )

    # Rename game.
    def rename_game(self, game_id: UUID, name: str) -> Game:
        if not _OPERATION_LOCK.acquire(blocking=False):
            raise DomainError("Já existe uma operação em andamento")
        moved_paths: list[tuple[Path, Path]] = []
        created_destination_parents: list[Path] = []
        changed_manifests: list[tuple[Path, dict]] = []
        try:
            game = self.db.get_game(game_id)
            if not game:
                raise DomainError("Jogo não encontrado")
            candidate = Game(
                validate_folder_name(name, "Nome do jogo"),
                game.id,
                game.created_at,
                utc_now().isoformat(),
                game.archived_at,
            )
            if self.game_name_exists(candidate.name, excluding=game.id):
                raise DomainError("Já existe um jogo com esse nome")

            profiles = self.db.list_profiles(game_id, True)
            directory_moves: dict[str, tuple[Path, Path, bool]] = {}
            namespace_updates: dict[str, str] = {}
            for profile in profiles:
                paths = self.policy.resolve(
                    profile,
                    str(game_id),
                    game_name=game.name,
                    validate_origin=False,
                )
                if profile.storage_mode == StorageMode.APP:
                    continue
                new_root = self.policy.namespace_parent(
                    profile,
                    paths.backup_base,
                    candidate.name,
                    str(game_id),
                )
                old_namespace = paths.namespace
                if old_namespace.parent == paths.backup_base:
                    source = old_namespace
                    destination = new_root / old_namespace.name
                    profile_namespace = True
                    namespace_updates[str(profile.id)] = str(destination)
                else:
                    source = old_namespace.parent
                    destination = new_root
                    profile_namespace = False
                    namespace_updates[str(profile.id)] = str(
                        destination / old_namespace.relative_to(source)
                    )
                source_key = os.path.normcase(os.path.abspath(str(source)))
                move = (source, destination, profile_namespace)
                existing_move = directory_moves.get(source_key)
                if existing_move and os.path.normcase(os.path.abspath(str(existing_move[1]))) != os.path.normcase(os.path.abspath(str(destination))):
                    raise DomainError("Os perfis deste projeto usam pastas de backup incompatíveis")
                directory_moves[source_key] = move

            if not profiles:
                settings = self.db.get_game_settings(game_id)
                if settings and settings.storage_mode != StorageMode.APP:
                    sample = Profile(
                        game_id,
                        "Perfil",
                        settings.source_type,
                        settings.source_path,
                        settings.storage_mode,
                        settings.backup_base_path,
                    )
                    base = self.policy.resolve_backup_base(sample)
                    source = self.policy.namespace_parent(sample, base, game.name, str(game_id))
                    destination = self.policy.namespace_parent(sample, base, candidate.name, str(game_id))
                    directory_moves[os.path.normcase(os.path.abspath(str(source)))] = (source, destination, False)

            manifest_updates = []
            for profile in profiles:
                manifest_path = self.policy.metadata_dir(profile) / "profile.json"
                if not manifest_path.exists():
                    continue
                original = read_manifest(manifest_path)
                updated = dict(original)
                updated["game_name"] = candidate.name
                if str(profile.id) in namespace_updates:
                    updated["namespace"] = namespace_updates[str(profile.id)]
                manifest_updates.append((manifest_path, original, updated))

            cleanup_sources: dict[str, Path] = {}
            destination_groups: dict[tuple[str, bool], tuple[Path, bool, list[Path]]] = {}
            for source, destination, profile_namespace in directory_moves.values():
                source_key = os.path.normcase(os.path.abspath(str(source)))
                destination_key = os.path.normcase(os.path.abspath(str(destination)))
                if source_key == destination_key:
                    continue
                if is_reparse(source):
                    raise DomainError(f"O caminho de backup não pode ser um link: {source}")
                if not source.exists():
                    continue
                if not source.is_dir():
                    raise DomainError(f"A pasta de backup não é válida: {source}")
                group_key = (destination_key, profile_namespace)
                if group_key not in destination_groups:
                    destination_groups[group_key] = (destination, profile_namespace, [])
                destination_groups[group_key][2].append(source)

            for destination, profile_namespace, sources in destination_groups.values():
                destination_key = os.path.normcase(os.path.abspath(str(destination)))
                if is_reparse(destination):
                    raise DomainError(f"O destino de backup não é uma pasta segura: {destination}")
                if destination.exists():
                    if not destination.is_dir():
                        raise DomainError(f"Já existe um item no destino de backup: {destination}")
                    anchor = destination
                    pending_sources = [
                        source for source in sources
                        if os.path.normcase(os.path.abspath(str(source))) != destination_key
                    ]
                    move_anchor = False
                else:
                    anchor = sources[0]
                    pending_sources = sources[1:]
                    move_anchor = True

                for source in pending_sources:
                    source_moves = self._plan_backup_directory_moves(
                        source,
                        anchor,
                        profile_namespace=profile_namespace,
                    )
                    for source_item, destination_item in source_moves:
                        if not destination_item.parent.exists():
                            destination_item.parent.mkdir(parents=True, exist_ok=True)
                            created_destination_parents.append(destination_item.parent)
                        source_item.rename(destination_item)
                        moved_paths.append((source_item, destination_item))
                    cleanup_sources[os.path.normcase(os.path.abspath(str(source)))] = source

                if move_anchor:
                    if not destination.parent.exists():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        created_destination_parents.append(destination.parent)
                    anchor.rename(destination)
                    moved_paths.append((anchor, destination))

            for manifest_path, original, updated in manifest_updates:
                changed_manifests.append((manifest_path, original))
                write_manifest(manifest_path, updated)
            self.db.update_game(candidate)
            for source in cleanup_sources.values():
                try:
                    self._remove_empty_backup_folders(source)
                except OSError:
                    pass
            return candidate
        except Exception as exc:
            rollback_failed = False
            for manifest_path, original in reversed(changed_manifests):
                try:
                    write_manifest(manifest_path, original)
                except Exception:
                    rollback_failed = True
            for source, destination in reversed(moved_paths):
                try:
                    if destination.exists() and not source.exists():
                        destination.rename(source)
                except OSError:
                    rollback_failed = True
            for path in reversed(created_destination_parents):
                try:
                    if path.exists() and not any(path.iterdir()):
                        path.rmdir()
                except OSError:
                    rollback_failed = True
            if rollback_failed:
                raise OperationError(
                    "Falha ao concluir ou reverter a renomeação do projeto; confira as pastas de backup."
                ) from exc
            raise
        finally:
            _OPERATION_LOCK.release()

    # Plan backup folder moves.
    def _plan_backup_directory_moves(
        self,
        source: Path,
        destination: Path,
        *,
        profile_namespace: bool,
    ) -> list[tuple[Path, Path]]:
        if is_reparse(source):
            raise DomainError(f"O caminho de backup não pode ser um link: {source}")
        if not source.is_dir():
            raise DomainError(f"A pasta de backup não é válida: {source}")
        if is_reparse(destination):
            raise DomainError(f"O destino de backup não é uma pasta segura: {destination}")
        if not destination.exists():
            return [(source, destination)]
        if not destination.is_dir():
            raise DomainError(f"Já existe um item no destino de backup: {destination}")

        self._reject_reparse_tree(source)
        self._reject_reparse_tree(destination)
        moves = []
        if profile_namespace:
            for child in source.iterdir():
                target = destination / child.name
                if target.exists() or is_reparse(target):
                    raise DomainError(
                        f"Já existe um save com esse nome no destino: {target}. "
                        "Os arquivos não foram sobrescritos."
                    )
                moves.append((child, target))
            return moves

        for profile_folder in source.iterdir():
            target_profile = destination / profile_folder.name
            if is_reparse(target_profile):
                raise DomainError(f"O perfil no destino não é uma pasta segura: {target_profile}")
            if not target_profile.exists():
                moves.append((profile_folder, target_profile))
                continue
            if not profile_folder.is_dir() or not target_profile.is_dir():
                raise DomainError(f"Já existe um item com esse nome no destino: {target_profile}")
            for save in profile_folder.iterdir():
                target_save = target_profile / save.name
                if target_save.exists() or is_reparse(target_save):
                    raise DomainError(
                        f"Já existe um save com esse nome no destino: {target_save}. "
                        "Os arquivos não foram sobrescritos."
                    )
                moves.append((save, target_save))
        return moves

    # Reject backup links.
    @staticmethod
    def _reject_reparse_tree(root: Path) -> None:
        if is_reparse(root):
            raise DomainError(f"O caminho de backup contém um link não suportado: {root}")
        if root.is_dir():
            for child in root.iterdir():
                CatalogService._reject_reparse_tree(child)

    # Remove empty backup folders.
    @staticmethod
    def _remove_empty_backup_folders(root: Path) -> None:
        if not root.is_dir() or is_reparse(root):
            return
        for child in root.iterdir():
            CatalogService._remove_empty_backup_folders(child)
        if not any(root.iterdir()):
            root.rmdir()

    # Delete game.
    def delete_game(self, game_id: UUID) -> None:
        """Remove o jogo, seus perfis e os backups gerenciados após confirmação da UI."""
        if not _OPERATION_LOCK.acquire(blocking=False):
            raise DomainError("Já existe uma operação em andamento")
        try:
            game = self.db.get_game(game_id)
            if not game:
                raise DomainError("Jogo não encontrado")
            profiles = self.db.list_profiles(game_id, True)
            profile_ids = {str(profile.id) for profile in profiles}
            game_backup_roots: set[Path] = set()
            settings = self.db.get_game_settings(game_id)
            if settings:
                sample = Profile(
                    game.id,
                    "Perfil",
                    settings.source_type,
                    settings.source_path,
                    settings.storage_mode,
                    settings.backup_base_path,
                )
                base = self.policy.resolve_backup_base(sample)
                game_root = self.policy.namespace_parent(sample, base, game.name, str(game_id))
                if game_root != base:
                    game_backup_roots.add(game_root)
            for profile in profiles:
                paths = self.policy.resolve(profile, str(game_id), game_name=game.name)
                namespace = paths.namespace
                if namespace.parent != paths.backup_base:
                    game_backup_roots.add(namespace.parent)
                if namespace.exists():
                    remove_tree_safe(namespace, namespace.parent)
                for managed in (self.policy.profile_data_dir(profile), self.policy.trash_dir(profile)):
                    if managed.exists():
                        remove_tree_safe(managed, managed.parent)
                staging = self.policy.data_root / "operations" / "staging" / str(profile.id)
                if staging.exists():
                    remove_tree_safe(staging, staging.parent)
            for game_root in game_backup_roots:
                if game_root.exists():
                    remove_tree_safe(game_root, game_root.parent)
            operations = self.policy.data_root / "operations"
            for journal in operations.glob("*.json"):
                try:
                    if str(json.loads(journal.read_text(encoding="utf-8")).get("profile_id")) in profile_ids:
                        journal.unlink()
                except (OSError, ValueError, TypeError):
                    continue
            self.db.delete_game(game_id)
        finally:
            _OPERATION_LOCK.release()

    # Delete profile and backups.
    def delete_profile(self, profile_id: UUID) -> None:
        """Remove o perfil, seus saves e metadados após confirmação da UI."""
        if not _OPERATION_LOCK.acquire(blocking=False):
            raise DomainError("Já existe uma operação em andamento")
        try:
            profile = self.db.get_profile(profile_id)
            if not profile:
                raise DomainError("Perfil não encontrado")
            game = self.db.get_game(profile.game_id)
            game_name = game.name if game else None
            namespace = self.policy.resolve(
                profile,
                str(profile.game_id),
                game_name=game_name,
                validate_origin=False,
            ).namespace
            if namespace.exists():
                remove_tree_safe(namespace, namespace.parent)
            for managed in (self.policy.profile_data_dir(profile), self.policy.trash_dir(profile)):
                if managed.exists():
                    remove_tree_safe(managed, managed.parent)
            staging = self.policy.data_root / "operations" / "staging" / str(profile.id)
            if staging.exists():
                remove_tree_safe(staging, staging.parent)
            operations = self.policy.data_root / "operations"
            for journal in operations.glob("*.json"):
                try:
                    data = json.loads(journal.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    continue
                if str(data.get("profile_id")) == str(profile.id):
                    journal.unlink()
            self.db.delete_profile(profile_id)
        finally:
            _OPERATION_LOCK.release()

    # Archive game.
    def archive_game(self, game_id: UUID) -> None:
        game = self.db.get_game(game_id)
        if not game: raise DomainError("Jogo não encontrado")
        game.archived_at = utc_now().isoformat(); game.updated_at = utc_now().isoformat(); self.db.update_game(game)
        for profile in self.db.list_profiles(game_id, True):
            profile.archived_at = game.archived_at; profile.updated_at = game.updated_at; self.db.update_profile(profile)

    # Restore game.
    def restore_game(self, game_id: UUID) -> None:
        game = self.db.get_game(game_id)
        if not game: raise DomainError("Jogo não encontrado")
        game.archived_at = None; game.updated_at = utc_now().isoformat(); self.db.update_game(game)
        for profile in self.db.list_profiles(game_id, True):
            profile.archived_at = None; profile.updated_at = game.updated_at; self.db.update_profile(profile)

    # Create profile.
    def create_profile(self, game_id: UUID, name: str, *legacy_args, notes: str | None = None) -> Profile:
        """Cria somente a identidade do perfil.

        Os argumentos antigos continuam aceitos para abrir catálogos/testes
        criados antes da configuração passar a pertencer ao jogo.
        """
        game = self.db.get_game(game_id)
        if not game: raise DomainError("Jogo não encontrado")
        settings = self.db.get_game_settings(game_id)
        legacy_profile_settings = None
        if legacy_args:
            if len(legacy_args) == 1:
                if notes is not None:
                    raise TypeError("A nota foi informada duas vezes")
                notes = legacy_args[0]
                legacy_args = ()
            if not legacy_args:
                pass
            elif len(legacy_args) not in (4, 5):
                raise TypeError("create_profile aceita nome e nota; argumentos antigos: tipo, origem, modo e destino")
            elif legacy_args:
                legacy_type, legacy_source, legacy_mode, legacy_base = legacy_args[:4]
                legacy_notes = legacy_args[4] if len(legacy_args) == 5 else ""
                requested = GameSettings(legacy_type, str(legacy_source), legacy_mode, str(legacy_base))
                if settings is None:
                    # Compatibilidade para clientes antigos: chamadas com os
                    # argumentos de configuração continuam criando perfis
                    # independentes. A UI nova nunca usa esse caminho.
                    legacy_profile_settings = self.game_settings.validate(game_id, requested)
                elif settings != requested:
                    raise DomainError("A origem e o destino são configurações do jogo e devem ser iguais em todos os perfis")
                if notes is None:
                    notes = legacy_notes
        if not settings and not legacy_profile_settings:
            raise DomainError("Defina a origem e o destino em Configurações do jogo antes de criar um perfil")
        effective = legacy_profile_settings or settings
        profile = Profile(game_id, name, effective.source_type, effective.source_path, effective.storage_mode, effective.backup_base_path, notes or "")
        existing = self.db.list_profiles(game_id, True)
        if any(p.normalized_name == profile.normalized_name for p in existing): raise DomainError("Já existe um perfil com esse nome")
        repositories = []
        for old in existing:
            try:
                repositories.append(self.policy.resolve(old, str(game_id), game_name=game.name).namespace)
            except Exception:
                continue
        self.policy.validate_source(profile.source_path, profile.source_type, repositories)
        paths = self.policy.resolve(profile, str(game_id), game_name=game.name); paths.namespace.mkdir(parents=True, exist_ok=True)
        metadata = self.policy.metadata_dir(profile); metadata.mkdir(parents=True, exist_ok=True)
        mark_hidden_windows(metadata)
        write_manifest(metadata / "profile.json", {"format_version": 1, "profile_id": str(profile.id), "game_id": str(game_id), "game_name": game.name if game else "", "name": profile.name, "source_type": profile.source_type.value, "source_path": profile.source_path, "storage_mode": profile.storage_mode.value, "backup_base_path": profile.backup_base_path, "namespace": str(paths.namespace), "notes": profile.notes, "filesystem_identity": filesystem_identity(paths.namespace)})
        self.db.add_profile(profile); return profile

    # Discover profiles.
    def discover_profiles(self, game_id: UUID) -> list[Profile]:
        """Adota pastas de perfis já existentes na pasta do jogo."""
        game = self.db.get_game(game_id)
        settings = self.db.get_game_settings(game_id)
        if not game or not settings:
            return []
        sample = Profile(game_id, "Perfil", settings.source_type, settings.source_path,
                         settings.storage_mode, settings.backup_base_path)
        try:
            base = self.policy.resolve_backup_base(sample)
            profile_root = self.policy.namespace_parent(sample, base, game.name, str(game_id))
            if not profile_root.is_dir():
                return []
            candidates = visible_children(profile_root, "directory")
        except (OSError, DomainError, PathPolicyError):
            return []
        existing_names = {profile.normalized_name for profile in self.db.list_profiles(game_id, True)}
        discovered: list[Profile] = []
        for namespace in sorted(candidates, key=lambda item: item.name.casefold()):
            if namespace.name.casefold() in INTERNAL_NAMESPACE_NAMES:
                continue
            try:
                name = validate_folder_name(namespace.name, "Nome do perfil")
            except DomainError:
                continue
            if normalize_name(name) in existing_names:
                continue
            profile = Profile(game_id, name, settings.source_type, settings.source_path,
                              settings.storage_mode, settings.backup_base_path)
            metadata = self.policy.metadata_dir(profile)
            try:
                metadata.mkdir(parents=True, exist_ok=True)
                mark_hidden_windows(metadata)
                write_manifest(
                    metadata / "profile.json",
                    {
                        "format_version": 1,
                        "profile_id": str(profile.id),
                        "game_id": str(game_id),
                        "game_name": game.name,
                        "name": profile.name,
                        "source_type": profile.source_type.value,
                        "source_path": profile.source_path,
                        "storage_mode": profile.storage_mode.value,
                        "backup_base_path": profile.backup_base_path,
                        "namespace": str(namespace),
                        "notes": profile.notes,
                        "filesystem_identity": filesystem_identity(namespace),
                    },
                )
                self.db.add_profile(profile)
            except (OSError, DomainError, IntegrityError, PathPolicyError):
                try:
                    remove_tree_safe(metadata, metadata.parent)
                except (OSError, IntegrityError, OperationError):
                    pass
                continue
            existing_names.add(profile.normalized_name)
            discovered.append(profile)
        return discovered

    # Synchronize profile folder.
    def sync_profile_folder(self, profile_id: UUID) -> bool:
        """Segue a identidade da pasta; usa hashes apenas em metadados antigos."""
        profile = self.db.get_profile(profile_id)
        if not profile:
            return False
        try:
            game = self.db.get_game(profile.game_id)
            paths = self.policy.resolve(profile, str(profile.game_id), game_name=game.name if game else None, validate_origin=False)
        except Exception:
            return False
        old_namespace, base = paths.namespace, paths.backup_base
        if not base.is_dir():
            return False
        metadata = self.policy.metadata_dir(profile)
        manifest_path = metadata / "profile.json"
        manifest = read_manifest(manifest_path)
        identity = manifest.get("filesystem_identity")
        candidates: list[Path] = []
        try:
            # Destinos novos agrupam os perfis em ``Saverino - <jogo>``. A
            # varredura inclui a pasta de agrupamento e seu conteúdo imediato
            # para continuar reconhecendo também catálogos legados.
            children = visible_children(base, "directory")
            nested: list[Path] = []
            for group in children:
                try:
                    nested.extend(visible_children(group, "directory"))
                except OSError:
                    continue
            children = children + nested
        except OSError:
            return False
        # Sem mudança, registre a identidade também para instalações anteriores.
        existing = next((child for child in children if child == old_namespace), None)
        if existing is not None and existing.name == old_namespace.name:
            current_identity = filesystem_identity(existing)
            if not identity and current_identity:
                manifest["filesystem_identity"] = current_identity
                write_manifest(manifest_path, manifest)
            if not identity or identity == current_identity:
                return False
        known_namespaces: set[Path] = set()
        for game in self.db.list_games(True):
            for other in self.db.list_profiles(game.id, True):
                if other.id == profile.id:
                    continue
                try:
                    known_namespaces.add(self.policy.resolve(other, game_name=None, validate_origin=False).namespace)
                except Exception:
                    continue
        for child in children:
            if not child.is_dir() or child.name.casefold() in INTERNAL_NAMESPACE_NAMES:
                continue
            try:
                if is_reparse(child) or child.resolve() in known_namespaces:
                    continue
                if identity:
                    matches = filesystem_identity(child) == identity
                else:
                    matches = child == old_namespace or self._matches_profile_payload(child, profile, metadata)
                if not matches:
                    continue
            except OSError:
                continue
            candidates.append(child)
        if len(candidates) != 1:
            return False
        new_namespace = candidates[0]
        try:
            new_name = validate_name(new_namespace.name, "Nome do perfil")
        except DomainError:
            return False
        if any(
            other.id != profile.id and other.normalized_name == normalize_name(new_name)
            for other in self.db.list_profiles(profile.game_id, True)
        ):
            return False
        manifest_path = metadata / "profile.json"
        try:
            manifest = read_manifest(manifest_path) if manifest_path.exists() else {}
            manifest.update(
                format_version=1,
                profile_id=str(profile.id),
                game_id=str(profile.game_id),
                name=new_name,
                source_type=profile.source_type.value,
                source_path=profile.source_path,
                storage_mode=profile.storage_mode.value,
                backup_base_path=profile.backup_base_path,
                namespace=str(new_namespace),
                filesystem_identity=filesystem_identity(new_namespace),
                notes=profile.notes,
            )
            write_manifest(manifest_path, manifest)
            updated = Profile(
                profile.game_id,
                new_name,
                profile.source_type,
                profile.source_path,
                profile.storage_mode,
                profile.backup_base_path,
                profile.notes,
                profile.id,
                profile.created_at,
                utc_now().isoformat(),
                profile.archived_at,
            )
            self.db.update_profile(updated)
        except Exception:
            return False
        return True

    # Handle matches profile payload.
    def _matches_profile_payload(self, namespace: Path, profile: Profile, metadata: Path) -> bool:
        """Retorna verdadeiro quando a pasta contém um snapshot deste perfil."""
        try:
            manifests = [item for item in metadata.glob("*.json") if item.name != "profile.json"]
        except OSError:
            return False
        for manifest_path in manifests:
            try:
                data = read_manifest(manifest_path)
                if data.get("profile_id") != str(profile.id):
                    continue
                if matching_payloads(namespace, profile.source_type.value, data):
                    return True
            except Exception:
                continue
        return False

    # Migrate legacy storage.
    def migrate_legacy_storage(self) -> None:
        """Retira metadados e temporários legados das pastas visíveis de backup."""
        pending_profiles = {str(item.get("profile_id")) for item in self._pending_journals()}
        for game in self.db.list_games(include_archived=True):
            for profile in self.db.list_profiles(game.id, True):
                if str(profile.id) in pending_profiles:
                    continue
                try:
                    namespace = self.policy.resolve(profile, str(game.id), game_name=game.name).namespace
                    if not namespace.exists():
                        base = self.policy.resolve_backup_base(profile)
                        namespace = self._find_legacy_namespace(base, profile.id) or namespace
                except Exception:
                    continue
                legacy_metadata = next((namespace / name for name in (".save-manager", "save-manager") if (namespace / name).is_dir()), None)
                legacy_converted = False
                if legacy_metadata is not None:
                    metadata = self.policy.metadata_dir(profile)
                    metadata.mkdir(parents=True, exist_ok=True)
                    copied = True
                    for item in legacy_metadata.iterdir():
                        if item.name == "profile-id":
                            continue
                        target = metadata / item.name
                        try:
                            if item.is_dir():
                                shutil.copytree(item, target, dirs_exist_ok=True)
                            elif target.exists() and target.read_bytes() != item.read_bytes():
                                copied = False
                                break
                            else:
                                shutil.copy2(item, target)
                        except OSError:
                            copied = False
                            break
                    if copied:
                        profile_manifest = metadata / "profile.json"
                        if profile_manifest.exists():
                            try:
                                manifest = read_manifest(profile_manifest)
                            except Exception:
                                manifest = {}
                        else:
                            manifest = {}
                        manifest.update(format_version=1, profile_id=str(profile.id), game_id=str(game.id), game_name=game.name, name=profile.name, source_type=profile.source_type.value, source_path=profile.source_path, storage_mode=profile.storage_mode.value, backup_base_path=profile.backup_base_path, namespace=str(namespace), notes=profile.notes)
                        write_manifest(profile_manifest, manifest)
                        mark_hidden_windows(metadata)
                        try:
                            remove_tree_safe(legacy_metadata, namespace)
                            legacy_converted = True
                        except (OSError, IntegrityError, OperationError):
                            pass
                for legacy_staging in (namespace / ".staging", namespace / "staging"):
                    if legacy_staging.name == "staging" and not legacy_converted:
                        continue
                    if legacy_staging.is_dir():
                        try:
                            remove_tree_safe(legacy_staging, namespace)
                        except (OSError, IntegrityError, OperationError):
                            pass
                for legacy_trash in (namespace / ".trash", namespace / "trash"):
                    if legacy_trash.name == "trash" and not legacy_converted:
                        continue
                    if not legacy_trash.is_dir():
                        continue
                    trash = self.policy.trash_dir(profile)
                    trash.mkdir(parents=True, exist_ok=True)
                    for item in legacy_trash.iterdir():
                        target = trash / item.name
                        if not target.exists():
                            try:
                                item.rename(target)
                            except (OSError, IntegrityError, OperationError):
                                pass
                    try:
                        if not any(legacy_trash.iterdir()):
                            remove_tree_safe(legacy_trash, namespace)
                    except (OSError, IntegrityError, OperationError):
                        pass

    # Find pending journals.
    def _pending_journals(self) -> list[dict]:
        operations = self.policy.data_root / "operations"
        result = []
        for path in operations.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("phase") not in ("completed", "failed"):
                    result.append(data)
            except (OSError, ValueError, TypeError):
                continue
        return result

    @staticmethod
    # Find legacy namespace.
    def _find_legacy_namespace(base: Path, profile_id: UUID) -> Path | None:
        for folder in (".save-manager", "save-manager"):
            for marker in base.glob(f"*/{folder}/profile-id"):
                try:
                    if marker.read_text(encoding="ascii").strip() == str(profile_id):
                        return marker.parent.parent
                except OSError:
                    continue
            for manifest_path in base.glob(f"*/{folder}/profile.json"):
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if str(data.get("profile_id")) == str(profile_id):
                        return manifest_path.parent.parent
                except (OSError, ValueError, TypeError):
                    continue
        return None

    # Rename profile.
    def rename_profile(self, profile_id: UUID, name: str, notes: str | None = None) -> Profile:
        profile = self.db.get_profile(profile_id)
        if not profile: raise DomainError("Perfil não encontrado")
        candidate = Profile(profile.game_id, name, profile.source_type, profile.source_path, profile.storage_mode, profile.backup_base_path, profile.notes if notes is None else notes, profile.id, profile.created_at, utc_now().isoformat(), profile.archived_at)
        if any(p.id != profile.id and p.normalized_name == candidate.normalized_name for p in self.db.list_profiles(profile.game_id, True)):
            raise DomainError("Já existe um perfil com esse nome")

        game = self.db.get_game(profile.game_id)
        game_name = game.name if game else None
        old_namespace = self.policy.resolve(profile, str(profile.game_id), game_name=game_name).namespace
        new_namespace = self.policy.resolve(candidate, str(candidate.game_id), game_name=game_name, use_stored_namespace=False).namespace
        moved = old_namespace != new_namespace and old_namespace.exists()
        if moved:
            if new_namespace.exists():
                raise DomainError("O destino já contém este perfil")
            new_namespace.parent.mkdir(parents=True, exist_ok=True)
            old_namespace.rename(new_namespace)
        try:
            metadata = self.policy.metadata_dir(candidate)
            metadata.mkdir(parents=True, exist_ok=True)
            mark_hidden_windows(metadata)
            manifest_path = metadata / "profile.json"
            manifest = read_manifest(manifest_path) if manifest_path.exists() else {
                "format_version": 1,
                "profile_id": str(candidate.id),
                "game_id": str(candidate.game_id),
                "source_type": candidate.source_type.value,
                "source_path": candidate.source_path,
                "storage_mode": candidate.storage_mode.value,
                "backup_base_path": candidate.backup_base_path,
            }
            manifest.update(name=candidate.name, notes=candidate.notes)
            manifest["namespace"] = str(new_namespace)
            write_manifest(manifest_path, manifest)
            self.db.update_profile(candidate)
        except Exception:
            if moved and new_namespace.exists() and not old_namespace.exists():
                new_namespace.rename(old_namespace)
            raise
        return candidate

    # Update source.
    def update_source(self, profile_id: UUID, source_path: str) -> Profile:
        profile = self.db.get_profile(profile_id)
        if not profile: raise DomainError("Perfil não encontrado")
        self.configure_game(profile.game_id, GameSettings(profile.source_type, source_path, profile.storage_mode, profile.backup_base_path))
        return self.db.get_profile(profile_id)

    # Handle profile status.
    def profile_status(self, profile_id: UUID) -> dict[str, object]:
        profile = self.db.get_profile(profile_id)
        if not profile: raise DomainError("Perfil não encontrado")
        try:
            game = self.db.get_game(profile.game_id)
            paths = self.policy.resolve(profile, str(profile.game_id), game_name=game.name if game else None)
        except Exception as exc:
            return {"available": False, "error": str(exc)}
        return {"available": paths.source.exists(), "source": paths.source, "backup_base": paths.backup_base, "namespace": paths.namespace}

    # Archive profile.
    def archive_profile(self, profile_id: UUID) -> None:
        profile = self.db.get_profile(profile_id)
        if not profile: raise DomainError("Perfil não encontrado")
        profile.archived_at = utc_now().isoformat(); profile.updated_at = profile.archived_at; self.db.update_profile(profile)

    # Restore profile.
    def restore_profile(self, profile_id: UUID) -> None:
        profile = self.db.get_profile(profile_id)
        if not profile: raise DomainError("Perfil não encontrado")
        profile.archived_at = None; profile.updated_at = utc_now().isoformat(); self.db.update_profile(profile)

    # Migrate destination.
    def migrate_destination(self, profile_id: UUID, new_base_path: str) -> Profile:
        """Compatibilidade: a troca de destino agora abrange o jogo inteiro."""
        profile = self.db.get_profile(profile_id)
        if not profile: raise DomainError("Perfil não encontrado")
        self.configure_game(profile.game_id, GameSettings(profile.source_type, profile.source_path, StorageMode.CUSTOM, new_base_path))
        return self.db.get_profile(profile_id)

    # Reconnect.
    def reconnect(self, base_path: str) -> list[Profile]:
        """Reconcilia apenas profile.json nativos; não restaura saves."""
        result: list[Profile] = []
        base = Path(base_path).resolve()
        profiles_root = self.policy.data_root / "profiles"
        for path in profiles_root.glob("*/profile.json"):
            try: data = read_manifest(path)
            except Exception: continue
            namespace = Path(data.get("namespace", "")).resolve()
            if namespace.parent != base and namespace.parent.parent != base:
                continue
            try:
                game_id, profile_id = UUID(data["game_id"]), UUID(data["profile_id"])
                game = self.db.get_game(game_id)
                if not game:
                    game = Game(data.get("game_name") or data["name"]); game.id = game_id; self.db.add_game(game)
                profile = self.db.get_profile(profile_id)
                if profile: result.append(profile); continue
                profile = Profile(game_id, data["name"], SourceType(data["source_type"]), data["source_path"], StorageMode(data["storage_mode"]), data.get("backup_base_path", str(base)), data.get("notes", ""), profile_id)
                self.db.add_profile(profile); result.append(profile)
            except Exception:
                continue
        return result
