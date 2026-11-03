from __future__ import annotations

import copy
import shutil
import threading
from pathlib import Path
from uuid import UUID, uuid4

from ..domain.errors import IntegrityError, OperationError, PathPolicyError
from ..domain.models import (
    Profile,
    Snapshot,
    SnapshotKind,
    SnapshotStatus,
    normalize_name,
    normalize_snapshot_name,
    validate_file_name,
    utc_now,
)
from ..filesystem.identity import filesystem_identity
from ..filesystem.inventory import build_inventory
from ..filesystem.operations import copy_bytes, copy_snapshot_payload, remove_tree_safe
from ..filesystem.policy import INTERNAL_NAMESPACE_NAMES, PathPolicy, mark_hidden_windows, safe_component
from ..storage.db import CatalogDB
from ..storage.journal import JournalStore
from ..storage.manifests import manifest_from_inventory, read_manifest, write_manifest

_OPERATION_LOCK = threading.Lock()


class SnapshotService:
    # Initialize the object.
    def __init__(self, db: CatalogDB, policy: PathPolicy, journals: JournalStore):
        self.db, self.policy, self.journals = db, policy, journals

    # Resolve paths.
    def _paths(self, profile: Profile):
        game = self.db.get_game(profile.game_id)
        return self.policy.resolve(profile, str(profile.game_id), game_name=game.name if game else None)

    # Create.
    def create(self, profile_id: UUID, name: str, notes: str = "", *, progress=None, cancel=None, _acquire_lock: bool = True, _kind: SnapshotKind = SnapshotKind.MANUAL) -> Snapshot:
        acquired = _OPERATION_LOCK.acquire(blocking=False) if _acquire_lock else True
        if _acquire_lock and not acquired: raise OperationError("Já existe uma operação em andamento")
        operation_id = str(uuid4())
        target: Path | None = None
        published = False
        final_manifest_path: Path | None = None
        try:
            profile = self.db.get_profile(profile_id)
            if not profile: raise OperationError("Perfil não encontrado")
            paths = self._paths(profile); source = paths.source
            if not source.exists(): raise OperationError("A origem não existe")
            name = normalize_snapshot_name(name, profile.source_type, profile.source_path)
            if any(normalize_name(s.name) == normalize_name(name) for s in self.db.list_snapshots(profile_id)):
                raise OperationError("Já existe um save com esse nome")
            initial = build_inventory(source, profile.source_type.value, hashes=True)
            disk_root = paths.backup_base if paths.backup_base.exists() else paths.backup_base.parent
            if shutil.disk_usage(disk_root).free < initial.total_bytes:
                raise OperationError("Espaço insuficiente no destino de backup")
            stage = self.policy.staging_dir(profile, operation_id)
            stage.parent.mkdir(parents=True, exist_ok=True)
            self.journals.write(operation_id, {"id": operation_id, "profile_id": str(profile_id), "kind": "snapshot", "phase": "prepared", "staging": str(stage), "source": str(source)})
            copied = copy_snapshot_payload(source, profile.source_type.value, stage / "payload", progress=progress, cancel=cancel)
            if profile.source_type.value == "file":
                if not copied.files or not initial.files or copied.files[0].size != initial.files[0].size or copied.files[0].sha256 != initial.files[0].sha256:
                    raise IntegrityError("Payload copiado não confere com a origem")
            elif {(e.relative, e.kind, e.size, e.sha256) for e in copied.entries} != {(e.relative, e.kind, e.size, e.sha256) for e in initial.entries}:
                raise IntegrityError("Payload copiado não confere com a origem")
            final = build_inventory(source, profile.source_type.value, hashes=True)
            if {(e.relative,e.size,e.mtime_ns,e.sha256) for e in initial.entries} != {(e.relative,e.size,e.mtime_ns,e.sha256) for e in final.entries}:
                raise OperationError("O save mudou durante a cópia; feche o jogo e tente novamente")
            # copied paths are normalized to save.bin/tree; compare byte inventory by relative source names.
            manifest = manifest_from_inventory(snapshot_id=operation_id, profile_id=str(profile.id), game_id=str(profile.game_id), name=name, notes=notes, kind=_kind.value, created_at=utc_now().isoformat(), source_type=profile.source_type.value, original_source_path=str(source), inventory=initial)
            write_manifest(stage / "manifest.json", manifest)
            display_name = safe_component(name)
            if display_name.casefold() in INTERNAL_NAMESPACE_NAMES:
                raise OperationError("Esse nome é reservado pela estrutura de backups")
            if cancel and cancel():
                raise OperationError("Operação cancelada")
            target = paths.namespace / display_name
            if target.exists():
                raise OperationError("O nome do save já existe fisicamente no perfil")
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = stage / "payload" / ("save.bin" if profile.source_type.value == "file" else "tree")
            payload.rename(target)
            published = True
            metadata = self.policy.metadata_dir(profile); metadata.mkdir(parents=True, exist_ok=True)
            mark_hidden_windows(metadata)
            manifest["relative_path"] = target.name
            manifest["filesystem_identity"] = filesystem_identity(target)
            final_manifest_path = metadata / f"{operation_id}.json"
            write_manifest(final_manifest_path, manifest)
            snapshot = Snapshot(profile_id, name, _kind, notes, initial.total_bytes, len(initial.files), target.name)
            # Keep ID aligned with physical directory/manifest operation id.
            snapshot.id = UUID(operation_id)
            remove_tree_safe(stage, stage.parent)
            self.db.add_snapshot(snapshot)
            self.journals.complete(operation_id)
            return snapshot
        except Exception:
            if published and target is not None and target.exists():
                try:
                    remove_tree_safe(target, target.parent)
                except Exception:
                    pass
            if final_manifest_path is not None and final_manifest_path.exists():
                try:
                    final_manifest_path.unlink()
                except OSError:
                    pass
            try:
                stage = locals().get("stage")
                if stage and stage.exists(): remove_tree_safe(stage, stage.parent.parent)
            except Exception:
                pass
            try:
                self.journals.write(operation_id, {"id": operation_id, "profile_id": str(profile_id), "kind": "snapshot", "phase": "failed"})
                self.journals.complete(operation_id)
            except Exception:
                pass
            raise
        finally:
            if _acquire_lock:
                _OPERATION_LOCK.release()

    # Overwrite.
    def overwrite(self, profile_id: UUID, snapshot_id: UUID, *, progress=None, cancel=None) -> Snapshot:
        """Substitui o payload de um snapshot pelo estado atual da origem."""
        if not _OPERATION_LOCK.acquire(blocking=False):
            raise OperationError("Já existe uma operação em andamento")
        operation_id = str(uuid4())
        target: Path | None = None
        stage: Path | None = None
        rollback: Path | None = None
        manifest_path: Path | None = None
        old_manifest: dict | None = None
        snapshot_before: Snapshot | None = None
        catalog_updated = False
        installed = False
        try:
            profile = self.db.get_profile(profile_id)
            snapshot = self.db.get_snapshot(snapshot_id)
            if not profile or not snapshot or snapshot.profile_id != profile_id:
                raise OperationError("Snapshot não encontrado")
            snapshot_before = copy.copy(snapshot)
            if snapshot.status == SnapshotStatus.TRASHED:
                raise OperationError("Não é possível sobrescrever um save na lixeira")
            paths = self._paths(profile)
            source = paths.source
            if not source.exists():
                raise OperationError("A origem não existe")
            target = self.snapshot_path(profile, snapshot)
            manifest_path = self.manifest_path(profile, snapshot)
            old_manifest = read_manifest(manifest_path)
            if (old_manifest.get("snapshot_id") != str(snapshot.id)
                    or old_manifest.get("profile_id") != str(profile.id)
                    or old_manifest.get("source_type") != profile.source_type.value):
                raise IntegrityError("Identidade ou tipo do snapshot não confere")
            source_inventory = build_inventory(source, profile.source_type.value, hashes=True)
            previous_inventory = build_inventory(target, profile.source_type.value, hashes=True) if target.exists() else None
            retained_bytes = previous_inventory.total_bytes if previous_inventory else 0
            target.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(target.parent).free < source_inventory.total_bytes + retained_bytes:
                raise OperationError("Espaço insuficiente no destino de backup")
            stage = target.parent / f".save-manager-overwrite-{operation_id}"
            rollback = target.parent / f".save-manager-overwrite-rollback-{operation_id}"
            if profile.source_type.value == "file":
                copy_bytes(source, stage, cancel=cancel)
                shutil.copystat(source, stage, follow_symlinks=False)
            else:
                if cancel and cancel():
                    raise OperationError("Operação cancelada")
                stage.mkdir()
                shutil.copytree(source, stage, dirs_exist_ok=True)
                if cancel and cancel():
                    raise OperationError("Operação cancelada")
            staged_inventory = build_inventory(stage, profile.source_type.value, hashes=True)
            if profile.source_type.value == "file":
                expected_file = next(iter(source_inventory.files), None)
                staged_file = next(iter(staged_inventory.files), None)
                if (not expected_file or not staged_file
                        or expected_file.size != staged_file.size
                        or expected_file.sha256 != staged_file.sha256):
                    raise IntegrityError("Payload copiado não confere com a origem")
            else:
                expected = {(entry.relative, entry.kind, entry.size, entry.sha256) for entry in source_inventory.entries}
                actual = {(entry.relative, entry.kind, entry.size, entry.sha256) for entry in staged_inventory.entries}
                if expected != actual:
                    raise IntegrityError("Payload copiado não confere com a origem")
            final_inventory = build_inventory(source, profile.source_type.value, hashes=True)
            if {(entry.relative, entry.size, entry.mtime_ns, entry.sha256) for entry in source_inventory.entries} != {(entry.relative, entry.size, entry.mtime_ns, entry.sha256) for entry in final_inventory.entries}:
                raise OperationError("O save mudou durante a cópia; feche o jogo e tente novamente")
            self.journals.write(operation_id, {"id": operation_id, "profile_id": str(profile_id), "snapshot_id": str(snapshot_id), "kind": "overwrite", "phase": "staged", "source": str(target), "staging": str(stage), "rollback": str(rollback), "old_exists": target.exists()})
            if target.exists():
                self.journals.write(operation_id, {"id": operation_id, "profile_id": str(profile_id), "snapshot_id": str(snapshot_id), "kind": "overwrite", "phase": "old_moving", "source": str(target), "staging": str(stage), "rollback": str(rollback), "old_exists": True})
                target.rename(rollback)
                self.journals.write(operation_id, {"id": operation_id, "profile_id": str(profile_id), "snapshot_id": str(snapshot_id), "kind": "overwrite", "phase": "old_moved", "source": str(target), "staging": str(stage), "rollback": str(rollback), "old_exists": True})
            self.journals.write(operation_id, {"id": operation_id, "profile_id": str(profile_id), "snapshot_id": str(snapshot_id), "kind": "overwrite", "phase": "new_installing", "source": str(target), "staging": str(stage), "rollback": str(rollback), "old_exists": target.exists() or rollback.exists()})
            stage.rename(target)
            installed = True
            self.journals.write(operation_id, {"id": operation_id, "profile_id": str(profile_id), "snapshot_id": str(snapshot_id), "kind": "overwrite", "phase": "new_installed", "source": str(target), "staging": str(stage), "rollback": str(rollback), "old_exists": rollback.exists()})
            installed_inventory = build_inventory(target, profile.source_type.value, hashes=True)
            if profile.source_type.value == "file":
                installed_file = next(iter(installed_inventory.files), None)
                if (not expected_file or not installed_file
                        or expected_file.size != installed_file.size
                        or expected_file.sha256 != installed_file.sha256):
                    raise IntegrityError("Arquivo sobrescrito não confere")
            else:
                installed_entries = {(entry.relative, entry.kind, entry.size, entry.sha256) for entry in installed_inventory.entries}
                if installed_entries != expected:
                    raise IntegrityError("Pasta sobrescrita não confere")
            overwritten_at = utc_now().isoformat()
            manifest = dict(old_manifest)
            manifest.update(
                name=snapshot.name,
                notes=snapshot.notes,
                kind=snapshot.kind.value,
                created_at=overwritten_at,
                relative_path=target.name,
                total_bytes=installed_inventory.total_bytes,
                file_count=installed_inventory.file_count,
                directories=[entry.relative for entry in installed_inventory.entries if entry.kind == "directory"],
                files=[{"path": entry.relative, "size": entry.size, "sha256": entry.sha256} for entry in installed_inventory.files],
                filesystem_identity=filesystem_identity(target),
            )
            target_stat = target.stat()
            manifest["filesystem_stamp"] = [target_stat.st_mtime_ns, target_stat.st_size]
            write_manifest(manifest_path, manifest)
            snapshot.total_bytes = installed_inventory.total_bytes
            snapshot.file_count = installed_inventory.file_count
            snapshot.status = SnapshotStatus.READY
            snapshot.created_at = overwritten_at
            self.db.update_snapshot(snapshot)
            catalog_updated = True
            self.journals.write(operation_id, {"id": operation_id, "profile_id": str(profile_id), "snapshot_id": str(snapshot_id), "kind": "overwrite", "phase": "verified", "source": str(target), "staging": str(stage), "rollback": str(rollback), "old_exists": rollback.exists()})
            self.journals.complete(operation_id)
            if rollback.exists():
                remove_tree_safe(rollback, target.parent)
            return snapshot
        except Exception:
            try:
                if rollback and rollback.exists():
                    if target and target.exists():
                        remove_tree_safe(target, target.parent)
                    rollback.rename(target)
                    if manifest_path and old_manifest is not None:
                        write_manifest(manifest_path, old_manifest)
                elif installed and target and target.exists():
                    remove_tree_safe(target, target.parent)
                    if manifest_path and old_manifest is not None:
                        write_manifest(manifest_path, old_manifest)
                elif stage and stage.exists():
                    remove_tree_safe(stage, stage.parent)
            except Exception:
                pass
            if catalog_updated and snapshot_before is not None:
                try:
                    self.db.update_snapshot(snapshot_before)
                except Exception:
                    pass
            raise
        finally:
            _OPERATION_LOCK.release()

    # Handle snapshot path.
    def snapshot_path(self, profile: Profile, snapshot: Snapshot) -> Path:
        namespace = self._paths(profile).namespace
        if snapshot.status == SnapshotStatus.TRASHED:
            return self.policy.trash_dir(profile) / str(snapshot.id)
        relative = self.policy.validate_manifest_relative(snapshot.relative_path)
        if len(relative.parts) != 1 or relative.name.casefold() in INTERNAL_NAMESPACE_NAMES:
            raise PathPolicyError("Caminho do snapshot inválido")
        return namespace / relative

    # Build path.
    def manifest_path(self, profile: Profile, snapshot: Snapshot) -> Path:
        return self.policy.metadata_dir(profile) / f"{snapshot.id}.json"

    # Verify.
    def verify(self, profile: Profile, snapshot: Snapshot, *, allow_modified: bool = False) -> dict:
        path = self.snapshot_path(profile, snapshot)
        data = read_manifest(self.manifest_path(profile, snapshot))
        if data["snapshot_id"] != str(snapshot.id) or data["profile_id"] != str(profile.id) or data.get("source_type") != profile.source_type.value:
            raise IntegrityError("Identidade ou tipo do snapshot não confere")
        expected_files = {str(item["path"]).replace("\\", "/"): item for item in data["files"]}
        payload_root = path
        if profile.source_type.value == "file":
            if not path.is_file():
                raise IntegrityError("Arquivo do snapshot está ausente")
            actual = build_inventory(payload_root, profile.source_type.value, hashes=not allow_modified)
            expected = next(iter(data["files"]), None)
            got = next(iter(actual.files), None)
            if not allow_modified and (expected is None or got is None or expected["size"] != got.size or expected["sha256"] != got.sha256):
                raise IntegrityError("Hash inválido no snapshot")
        if profile.source_type.value == "directory":
            if not path.is_dir():
                raise IntegrityError("Pasta do snapshot está ausente")
            actual = build_inventory(payload_root, profile.source_type.value, hashes=not allow_modified)
            actual_files = {entry.relative: entry for entry in actual.files}
            if not allow_modified:
                if set(expected_files) != set(actual_files): raise IntegrityError("Arquivos faltantes ou extras no snapshot")
                expected_dirs = {str(item).replace("\\", "/") for item in data["directories"]}
                actual_dirs = {entry.relative for entry in actual.entries if entry.kind == "directory"}
                if expected_dirs != actual_dirs: raise IntegrityError("Diretórios faltantes ou extras no snapshot")
                for rel, item in expected_files.items():
                    got = actual_files[rel]
                    if item["size"] != got.size or item["sha256"] != got.sha256: raise IntegrityError("Hash inválido no snapshot")
        snapshot.status = SnapshotStatus.READY; self.db.update_snapshot(snapshot)
        return data

    # Search.
    def search(self, profile_id: UUID, query: str = "", include_safety: bool = True) -> list[Snapshot]:
        q = query.casefold().strip()
        return [s for s in self.db.list_snapshots(profile_id) if (include_safety or s.kind != SnapshotKind.SAFETY) and (not q or q in s.name.casefold() or q in s.notes.casefold())]

    # Reconcile pending publications.
    def reconcile_pending_publications(self) -> list[str]:
        """Recupera publicação concluída antes da indexação SQLite."""
        recovered: list[str] = []
        for journal in self.journals.pending():
            if journal.get("kind") != "snapshot":
                continue
            operation_id = journal.get("id")
            try:
                profile = self.db.get_profile(UUID(journal["profile_id"]))
                if not profile or not operation_id:
                    continue
                manifest_path = self.policy.metadata_dir(profile) / f"{operation_id}.json"
                manifest = read_manifest(manifest_path)
                if not self.db.get_snapshot(UUID(operation_id)):
                    relative = self.policy.validate_manifest_relative(manifest.get("relative_path", safe_component(manifest["name"])))
                    if len(relative.parts) != 1 or relative.name.casefold() in INTERNAL_NAMESPACE_NAMES:
                        continue
                    snap = Snapshot(profile.id, validate_file_name(manifest["name"], "Nome do save"), SnapshotKind(manifest.get("kind", "manual")), manifest.get("notes", ""), int(manifest.get("total_bytes", 0)), int(manifest.get("file_count", 0)), relative.as_posix())
                    snap.id = UUID(operation_id); self.db.add_snapshot(snap)
                self.journals.complete(operation_id); recovered.append(operation_id)
            except (OSError, IntegrityError, PathPolicyError, ValueError, KeyError):
                continue
        return recovered
