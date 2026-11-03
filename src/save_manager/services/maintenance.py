from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from ..domain.errors import DomainError, IntegrityError, OperationError, PathPolicyError
from ..domain.models import (
    Snapshot,
    SnapshotKind,
    SnapshotStatus,
    normalize_name,
    normalize_snapshot_name,
    validate_file_name,
    validate_notes,
)
from ..filesystem.identity import filesystem_identity, visible_children
from ..filesystem.inventory import build_inventory
from ..filesystem.operations import remove_tree_safe
from ..filesystem.policy import INTERNAL_NAMESPACE_NAMES, mark_hidden_windows, safe_component
from ..storage.db import CatalogDB
from ..storage.manifests import manifest_from_inventory, read_manifest, write_manifest
from .snapshot import SnapshotService
from .synchronization import inventory_matches_manifest, matching_payloads


class MaintenanceService:
    # Initialize the object.
    def __init__(self, db: CatalogDB, snapshots: SnapshotService): self.db, self.snapshots = db, snapshots

    # Remove profile.
    def remove_profile(self, profile_id: UUID) -> bool:
        """Esquece um perfil cuja pasta visível foi removida externamente."""
        profile = self.db.get_profile(profile_id)
        if not profile:
            return False
        policy = self.snapshots.policy
        for managed in (policy.profile_data_dir(profile), policy.trash_dir(profile)):
            if managed.exists():
                remove_tree_safe(managed, managed.parent)
        staging = policy.data_root / "operations" / "staging" / str(profile.id)
        if staging.exists():
            remove_tree_safe(staging, staging.parent)
        operations = policy.data_root / "operations"
        for journal in operations.glob("*.json"):
            try:
                data = json.loads(journal.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                continue
            if str(data.get("profile_id")) == str(profile.id):
                journal.unlink()
        self.db.delete_profile(profile_id)
        return True

    # Rename snapshot.
    def rename_snapshot(self, snapshot_id: UUID, name: str, notes: str | None = None):
        snap = self.db.get_snapshot(snapshot_id)
        if not snap: raise OperationError("Snapshot não encontrado")
        new_notes = snap.notes if notes is None else validate_notes(notes)
        profile = self.db.get_profile(snap.profile_id)
        new_name = normalize_snapshot_name(name, profile.source_type, profile.source_path) if profile else validate_file_name(name, "Nome do save")
        if profile:
            duplicate = any(
                item.id != snap.id
                and normalize_name(item.name) == normalize_name(new_name)
                for item in self.db.list_snapshots(snap.profile_id)
            )
        else:
            duplicate = any(
                item.id != snap.id and normalize_name(item.name) == normalize_name(new_name)
                for item in self.db.list_snapshots(snap.profile_id)
            )
        if duplicate:
            raise OperationError("Já existe um save com esse nome")
        if profile:
            old_path = self.snapshots.snapshot_path(profile, snap)
            new_relative = safe_component(new_name)
            if new_relative.casefold() in INTERNAL_NAMESPACE_NAMES:
                raise OperationError("Esse nome é reservado pela estrutura de backups")
            namespace = self.snapshots._paths(profile).namespace
            new_path = namespace / new_relative
            moved = snap.status != SnapshotStatus.TRASHED and old_path.exists() and old_path != new_path
            if new_path.exists() and old_path != new_path:
                raise OperationError("O nome do save já existe fisicamente no perfil")
            if moved:
                old_path.rename(new_path)
            path = self.snapshots.manifest_path(profile, snap)
            old_manifest = None
            try:
                manifest = read_manifest(path)
                old_manifest = dict(manifest)
                manifest["name"], manifest["notes"], manifest["relative_path"] = new_name, new_notes, new_relative
                write_manifest(path, manifest)
                snap.name, snap.notes, snap.relative_path = new_name, new_notes, new_relative
                self.db.update_snapshot(snap)
            except Exception:
                if old_manifest is not None:
                    try:
                        write_manifest(path, old_manifest)
                    except Exception:
                        pass
                if moved and new_path.exists() and not old_path.exists():
                    new_path.rename(old_path)
                raise OperationError("Não foi possível atualizar o manifesto do snapshot")
        else:
            snap.name, snap.notes = new_name, new_notes
            self.db.update_snapshot(snap)
        return snap

    # Trash snapshot.
    def trash_snapshot(self, snapshot_id: UUID):
        snap = self.db.get_snapshot(snapshot_id)
        if not snap: raise OperationError("Snapshot não encontrado")
        if snap.status == SnapshotStatus.TRASHED:
            return
        profile = self.db.get_profile(snap.profile_id)
        if not profile: raise OperationError("Perfil não encontrado")
        path = self.snapshots.snapshot_path(profile, snap); trash = self.snapshots.policy.trash_dir(profile) / str(snap.id)
        trash.parent.mkdir(parents=True, exist_ok=True)
        mark_hidden_windows(trash.parent)
        if path.exists(): path.rename(trash)
        snap.status = SnapshotStatus.TRASHED; self.db.update_snapshot(snap)

    # Refresh a profile from disk.
    def refresh_profile(self, profile_id: UUID) -> bool:
        profile = self.db.get_profile(profile_id)
        if not profile:
            raise OperationError("Perfil não encontrado")
        try:
            namespace = self.snapshots.policy.resolve(profile, validate_origin=False).namespace
            try:
                namespace.stat()
            except FileNotFoundError:
                return self.sync_profile(profile_id, strict=True)
            if not namespace.is_dir():
                raise OperationError("A pasta de backups não é válida")
            visible_children(namespace, profile.source_type.value)
        except OperationError:
            raise
        except (OSError, PathPolicyError) as exc:
            raise OperationError("Não foi possível ler a pasta de backups") from exc
        try:
            return self.sync_profile(profile_id, strict=True)
        except (OSError, PathPolicyError) as exc:
            raise OperationError("Não foi possível ler a pasta de backups") from exc

    # Synchronize profile.
    def sync_profile(self, profile_id: UUID, *, strict: bool = False) -> bool:
        """Reconcilia renomes/disponibilidade e importa payloads novos."""
        profile = self.db.get_profile(profile_id)
        if not profile:
            return False
        try:
            namespace = self.snapshots.policy.resolve(profile, validate_origin=False).namespace
            children = visible_children(namespace, profile.source_type.value) if namespace.is_dir() else []
        except (OSError, PathPolicyError):
            if strict:
                raise
            return False
        snapshots = self.db.list_snapshots(profile_id)
        snapshots, removed_incompatible = self._remove_incompatible_file_snapshots(profile, snapshots)
        by_name = {child.name.casefold(): child for child in children}
        identities = {child: filesystem_identity(child) for child in children}
        by_identity: dict[tuple[int, ...], list[Path]] = {}
        for child, identity in identities.items():
            if identity:
                by_identity.setdefault(tuple(identity), []).append(child)
        claimed = {
            snap.relative_path.casefold()
            for snap in snapshots
            if snap.relative_path.casefold() in by_name
        }
        changed = removed_incompatible
        for snap in snapshots:
            try:
                old_relative = self.snapshots.policy.validate_manifest_relative(snap.relative_path)
                if len(old_relative.parts) != 1:
                    continue
                manifest_path = self.snapshots.manifest_path(profile, snap)
                manifest = read_manifest(manifest_path)
                identity = manifest.get("filesystem_identity")
                identity_matches = by_identity.get(tuple(identity), []) if identity else []
                matches = [candidate for candidate in identity_matches if self._payload_allowed(profile, candidate)]
                candidate = matches[0] if len(matches) == 1 else None
                if candidate is None:
                    named = by_name.get(old_relative.name.casefold())
                    candidate = named if named is not None and self._payload_allowed(profile, named) else None
                if candidate is None and identity_matches and not matches and profile.source_type.value == "file":
                    if manifest_path.exists():
                        manifest_path.unlink()
                    self.db.delete_snapshot(snap.id)
                    changed = True
                    continue
                if candidate is None and not identity:
                    matches = [
                        candidate for candidate in matching_payloads(namespace, profile.source_type.value, manifest, claimed)
                        if self._payload_allowed(profile, candidate)
                    ]
                    if len(matches) == 1:
                        # Não atribuir um único arquivo a dois backups idênticos.
                        competitors = [other for other in snapshots if other.id != snap.id
                                       and other.relative_path.casefold() not in claimed]
                        ambiguous = any(
                            inventory_matches_manifest(matches[0], profile.source_type.value,
                                                       read_manifest(self.snapshots.manifest_path(profile, other)))
                            for other in competitors
                        )
                        if not ambiguous:
                            candidate = matches[0]
                if candidate is None:
                    if snap.status != SnapshotStatus.MISSING:
                        snap.status = SnapshotStatus.MISSING
                        self.db.update_snapshot(snap)
                        changed = True
                    continue
                display_name = self._payload_display_name(profile, candidate.name)
                renamed = candidate.name != snap.relative_path or snap.name != display_name
                if renamed:
                    new_name = validate_file_name(display_name, "Nome do save")
                    if any(other.id != snap.id and normalize_name(other.name) == normalize_name(new_name) for other in snapshots):
                        continue
                    snap.name, snap.relative_path = new_name, candidate.name
                    manifest["name"], manifest["relative_path"] = new_name, candidate.name
                new_identity = identities[candidate]
                stat = candidate.stat()
                stamp = [stat.st_mtime_ns, stat.st_size]
                old_status = snap.status
                if (snap.status == SnapshotStatus.MISSING
                        or (identity and identity != new_identity)
                        or (profile.source_type.value == "file" and manifest.get("filesystem_stamp", stamp) != stamp)):
                    valid = inventory_matches_manifest(candidate, profile.source_type.value, manifest)
                    snap.status = SnapshotStatus.READY if valid else SnapshotStatus.CORRUPT
                if renamed or identity != new_identity or manifest.get("filesystem_stamp") != stamp:
                    manifest["filesystem_identity"] = new_identity
                    manifest["filesystem_stamp"] = stamp
                    write_manifest(manifest_path, manifest)
                if renamed or snap.status != old_status:
                    self.db.update_snapshot(snap)
                    changed = True
                claimed.add(candidate.name.casefold())
            except (OSError, IntegrityError, PathPolicyError, ValueError, KeyError):
                continue
        # Tudo que foi colocado manualmente diretamente no namespace do
        # perfil passa a ser um save navegável. Para arquivos, cada arquivo é
        # um snapshot; para pastas, cada pasta é um snapshot independente.
        if self._import_unindexed(profile, children, snapshots, claimed):
            changed = True
        return changed

    # Import unindexed.
    def _import_unindexed(self, profile, children: list[Path], snapshots: list[Snapshot], claimed: set[str]) -> list[Snapshot]:
        """Cria metadados para payloads físicos ainda ausentes no catálogo."""
        active_names = {snap.relative_path.casefold() for snap in snapshots}
        imported: list[Snapshot] = []
        metadata = self.snapshots.policy.metadata_dir(profile)
        metadata.mkdir(parents=True, exist_ok=True)
        for child in sorted(children, key=lambda item: item.name.casefold()):
            folded = child.name.casefold()
            if folded in claimed or folded in active_names or child.name.casefold() in INTERNAL_NAMESPACE_NAMES:
                continue
            if not self._payload_allowed(profile, child):
                continue
            try:
                name = validate_file_name(self._payload_display_name(profile, child.name), "Nome do save")
                inventory = build_inventory(child, profile.source_type.value, hashes=True)
                snapshot = Snapshot(
                    profile.id,
                    name,
                    SnapshotKind.MANUAL,
                    "",
                    inventory.total_bytes,
                    len(inventory.files),
                    child.name,
                )
                manifest = manifest_from_inventory(
                    snapshot_id=str(snapshot.id),
                    profile_id=str(profile.id),
                    game_id=str(profile.game_id),
                    name=name,
                    notes="",
                    kind=SnapshotKind.MANUAL.value,
                    created_at=snapshot.created_at,
                    source_type=profile.source_type.value,
                    original_source_path=profile.source_path,
                    inventory=inventory,
                )
                manifest["relative_path"] = child.name
                manifest["filesystem_identity"] = filesystem_identity(child)
                stat = child.stat()
                manifest["filesystem_stamp"] = [stat.st_mtime_ns, stat.st_size]
                manifest_path = metadata / f"{snapshot.id}.json"
                write_manifest(manifest_path, manifest)
                try:
                    self.db.add_snapshot(snapshot)
                except Exception:
                    try:
                        manifest_path.unlink()
                    except OSError:
                        pass
                    continue
            except (OSError, IntegrityError, PathPolicyError, DomainError, ValueError, KeyError):
                continue
            snapshots.append(snapshot)
            active_names.add(folded)
            claimed.add(folded)
            imported.append(snapshot)
        return imported

    # Keep all file snapshots.
    def _remove_incompatible_file_snapshots(self, profile, snapshots: list[Snapshot]) -> tuple[list[Snapshot], bool]:
        """Retain every regular file regardless of its suffix."""
        return snapshots, False

    @staticmethod
    # Check payload type compatibility.
    def _payload_allowed(profile, path: Path) -> bool:
        if profile.source_type.value == "file":
            return path.is_file()
        return path.is_dir()

    @staticmethod
    # Get payload display name.
    def _payload_display_name(profile, filename: str) -> str:
        """Preserve the physical filename for manual snapshots."""
        return Path(filename).name

    # Restore from trash.
    def restore_from_trash(self, snapshot_id: UUID):
        snap = self.db.get_snapshot(snapshot_id); profile = self.db.get_profile(snap.profile_id) if snap else None
        if not snap or not profile: raise OperationError("Snapshot não encontrado")
        namespace = self.snapshots._paths(profile).namespace
        relative = self.snapshots.policy.validate_manifest_relative(snap.relative_path)
        if len(relative.parts) != 1 or relative.name.casefold() in INTERNAL_NAMESPACE_NAMES:
            raise OperationError("Caminho do snapshot inválido")
        trash = self.snapshots.policy.trash_dir(profile) / str(snap.id); target = namespace / relative
        if not trash.exists(): raise OperationError("Item não está na lixeira")
        if target.exists(): raise OperationError("Já existe um save com esse nome no perfil")
        target.parent.mkdir(parents=True, exist_ok=True); trash.rename(target); snap.status = SnapshotStatus.READY; self.db.update_snapshot(snap)

    # Reconcile.
    def reconcile(self, profile_id: UUID) -> list[str]:
        profile = self.db.get_profile(profile_id)
        if not profile: return []
        indexed = {s.id for s in self.db.list_snapshots(profile_id, True)}; found = []
        for indexed_snap in self.db.list_snapshots(profile_id, True):
            if indexed_snap.status == SnapshotStatus.TRASHED:
                continue
            if not self.snapshots.snapshot_path(profile, indexed_snap).exists():
                indexed_snap.status = SnapshotStatus.MISSING; self.db.update_snapshot(indexed_snap)
            else:
                try:
                    self.snapshots.verify(profile, indexed_snap)
                except (IntegrityError, OSError, KeyError, ValueError):
                    indexed_snap.status = SnapshotStatus.CORRUPT; self.db.update_snapshot(indexed_snap)
        for manifest in self.snapshots.policy.metadata_dir(profile).glob("*.json"):
            try:
                if manifest.name == "profile.json":
                    continue
                sid = UUID(manifest.stem)
                if sid not in indexed:
                    data = read_manifest(manifest)
                    relative = self.snapshots.policy.validate_manifest_relative(data.get("relative_path", data["name"]))
                    if len(relative.parts) != 1 or relative.name.casefold() in INTERNAL_NAMESPACE_NAMES:
                        continue
                    snap = Snapshot(profile_id, validate_file_name(data["name"], "Nome do save"), SnapshotKind(data.get("kind", "manual")), data.get("notes", ""), int(data.get("total_bytes", 0)), int(data.get("file_count", 0)), relative.as_posix())
                    snap.id = sid; self.db.add_snapshot(snap); found.append(str(sid))
            except (ValueError, IntegrityError, OSError, PathPolicyError): continue
        try:
            namespace = self.snapshots.policy.resolve(profile, validate_origin=False).namespace
            children = visible_children(namespace, profile.source_type.value) if namespace.is_dir() else []
            snapshots = self.db.list_snapshots(profile_id)
            claimed = {snap.relative_path.casefold() for snap in snapshots}
            imported = self._import_unindexed(profile, children, snapshots, claimed)
            found.extend(str(snap.id) for snap in imported)
        except (OSError, IntegrityError, PathPolicyError):
            pass
        return found

    # Verify integrity.
    def verify_integrity(self, snapshot_id: UUID) -> SnapshotStatus:
        snap = self.db.get_snapshot(snapshot_id)
        if not snap: raise OperationError("Snapshot não encontrado")
        profile = self.db.get_profile(snap.profile_id)
        if not profile: raise OperationError("Perfil não encontrado")
        try:
            self.snapshots.verify(profile, snap)
        except Exception:
            snap.status = SnapshotStatus.CORRUPT; self.db.update_snapshot(snap)
        return snap.status

    # Delete permanently.
    def delete_permanently(self, snapshot_id: UUID) -> None:
        snap = self.db.get_snapshot(snapshot_id)
        if not snap or snap.status != SnapshotStatus.TRASHED: raise OperationError("Mova o snapshot para a lixeira antes de excluir")
        profile = self.db.get_profile(snap.profile_id)
        if not profile: raise OperationError("Perfil não encontrado")
        trash = self.snapshots.policy.trash_dir(profile) / str(snap.id)
        if trash.exists():
            remove_tree_safe(trash, trash.parent.parent)
        manifest = self.snapshots.manifest_path(profile, snap)
        if manifest.exists():
            remove_tree_safe(manifest, manifest.parent)
        self.db.delete_snapshot(snapshot_id)

