from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID, uuid4

from ..domain.errors import IntegrityError, OperationError
from ..domain.models import Snapshot, SnapshotStatus, utc_now
from ..filesystem.identity import filesystem_identity
from ..filesystem.inventory import build_inventory
from ..filesystem.operations import copy_bytes, remove_tree_safe
from ..filesystem.policy import PathPolicy
from ..storage.db import CatalogDB
from ..storage.journal import JournalStore
from ..storage.manifests import write_manifest
from .snapshot import _OPERATION_LOCK, SnapshotService


class RestoreService:
    # Initialize the object.
    def __init__(self, db: CatalogDB, policy: PathPolicy, journals: JournalStore, snapshots: SnapshotService):
        self.db, self.policy, self.journals, self.snapshots = db, policy, journals, snapshots

    # Restore.
    def restore(self, profile_id: UUID, snapshot_id: UUID, *, progress=None) -> Snapshot | None:
        if not _OPERATION_LOCK.acquire(blocking=False): raise OperationError("Já existe uma operação em andamento")
        op = str(uuid4()); rollback = None; stage = None; source = None; old_exists = False
        try:
            profile = self.db.get_profile(profile_id); snapshot = self.db.get_snapshot(snapshot_id)
            if not profile or not snapshot or snapshot.profile_id != profile_id: raise OperationError("Snapshot não encontrado")
            # The user may have edited the backup payload manually.  Keep the
            # manifest identity/path checks, but accept its current contents
            # and refresh the manifest after the replacement succeeds.
            manifest = self.snapshots.verify(profile, snapshot, allow_modified=True)
            game = self.db.get_game(profile.game_id)
            paths = self.policy.resolve(profile, str(profile.game_id), game_name=game.name if game else None); source = paths.source; old_exists = source.exists()
            payload_path = self.snapshots.snapshot_path(profile, snapshot)
            payload_inventory = build_inventory(payload_path, profile.source_type.value, hashes=True)
            protected = build_inventory(source, profile.source_type.value, hashes=True) if old_exists else None
            required = payload_inventory.total_bytes + (protected.total_bytes if protected else 0)
            if shutil.disk_usage(source.parent).free < required:
                raise OperationError("Espaço insuficiente no volume da origem")
            stage = source.parent / f".save-manager-stage-{op}"
            rollback = source.parent / f".save-manager-rollback-{op}"
            if profile.source_type.value == "file":
                copy_bytes(payload_path, stage)
            else:
                stage.mkdir()
                shutil.copytree(payload_path, stage, dirs_exist_ok=True)
            self.journals.write(op, {"id": op, "profile_id": str(profile_id), "snapshot_id": str(snapshot_id), "phase": "staged", "source": str(source), "staging": str(stage), "rollback": str(rollback), "old_exists": old_exists})
            if old_exists:
                current = build_inventory(source, profile.source_type.value, hashes=True)
                if protected and {(e.relative, e.size, e.mtime_ns, e.sha256) for e in protected.entries} != {(e.relative, e.size, e.mtime_ns, e.sha256) for e in current.entries}:
                    raise OperationError("A origem mudou durante a preparação; restauração cancelada")
                self.journals.write(op, {"id": op, "profile_id": str(profile_id), "snapshot_id": str(snapshot_id), "phase": "old_moving", "source": str(source), "staging": str(stage), "rollback": str(rollback), "old_exists": old_exists})
                source.rename(rollback)
                self.journals.write(op, {"id": op, "profile_id": str(profile_id), "snapshot_id": str(snapshot_id), "phase": "old_moved", "source": str(source), "staging": str(stage), "rollback": str(rollback), "old_exists": old_exists})
            self.journals.write(op, {"id": op, "profile_id": str(profile_id), "snapshot_id": str(snapshot_id), "phase": "new_installing", "source": str(source), "staging": str(stage), "rollback": str(rollback), "old_exists": old_exists})
            stage.rename(source)
            self.journals.write(op, {"id": op, "profile_id": str(profile_id), "snapshot_id": str(snapshot_id), "phase": "new_installed", "source": str(source), "staging": str(stage), "rollback": str(rollback), "old_exists": old_exists})
            # Verify that the staged copy is exactly what was present in the
            # selected backup, without comparing it to the old manifest hash.
            installed = build_inventory(source, profile.source_type.value, hashes=True)
            if profile.source_type.value == "file":
                expected = payload_inventory.files[0] if payload_inventory.files else None
                got = installed.files[0] if installed.files else None
                if not expected or not got or expected.size != got.size or expected.sha256 != got.sha256:
                    raise IntegrityError("Arquivo restaurado não confere")
            else:
                expected = {(i.relative, i.kind, i.size, i.sha256) for i in payload_inventory.entries}
                actual = {(i.relative, i.kind, i.size, i.sha256) for i in installed.entries}
                if expected != actual:
                    raise IntegrityError("Pasta restaurada não confere")

            manifest["total_bytes"] = installed.total_bytes
            manifest["file_count"] = installed.file_count
            manifest["directories"] = [entry.relative for entry in installed.entries if entry.kind == "directory"]
            manifest["files"] = [{"path": entry.relative, "size": entry.size, "sha256": entry.sha256} for entry in installed.files]
            manifest["filesystem_identity"] = filesystem_identity(payload_path)
            payload_stat = payload_path.stat()
            manifest["filesystem_stamp"] = [payload_stat.st_mtime_ns, payload_stat.st_size]
            write_manifest(self.snapshots.manifest_path(profile, snapshot), manifest)
            snapshot.total_bytes = installed.total_bytes
            snapshot.file_count = installed.file_count
            snapshot.status = SnapshotStatus.READY
            self.db.update_snapshot(snapshot)
            self.journals.write(op, {"id": op, "profile_id": str(profile_id), "snapshot_id": str(snapshot_id), "phase": "verified", "source": str(source), "staging": str(stage), "rollback": str(rollback), "old_exists": old_exists})
            snapshot.last_loaded_at = utc_now().isoformat(); self.db.update_snapshot(snapshot)
            self.journals.complete(op)
            if rollback and rollback.exists(): remove_tree_safe(rollback, source.parent)
            return snapshot
        except Exception:
            # Restore prior state whenever it is unambiguous; leave artifacts for recovery otherwise.
            try:
                if rollback and rollback.exists() and source and source.exists(): remove_tree_safe(source, source.parent)
                if rollback and rollback.exists() and source and not source.exists(): rollback.rename(source)
                if stage and stage.exists(): remove_tree_safe(stage, stage.parent)
            except Exception:
                pass
            raise
        finally:
            _OPERATION_LOCK.release()

    # Find pending recovery.
    def pending_recovery(self) -> list[dict]:
        return self.journals.pending()

    # Recover.
    def recover(self, operation_id: str, keep_restored: bool = False) -> None:
        data = self.journals.read(operation_id)
        source, rollback, stage = Path(data["source"]), Path(data["rollback"]), Path(data["staging"])
        if keep_restored:
            if stage.exists() and not source.exists(): stage.rename(source)
        elif rollback.exists():
            if source.exists(): remove_tree_safe(source, source.parent)
            rollback.rename(source)
        if stage.exists(): remove_tree_safe(stage, stage.parent)
        self.journals.complete(operation_id)
