import pytest

from save_manager.domain.errors import DomainError, OperationError
from save_manager.domain.models import SnapshotStatus, SourceType, StorageMode
from save_manager.filesystem.policy import PathPolicy
from save_manager.services.catalog import CatalogService
from save_manager.services.maintenance import MaintenanceService
from save_manager.services.restore import RestoreService
from save_manager.services.snapshot import SnapshotService
from save_manager.storage.db import CatalogDB
from save_manager.storage.journal import JournalStore
from save_manager.storage.manifests import read_manifest


def make_services(tmp_path):
    root = tmp_path / "app"
    db = CatalogDB(root / "data" / "catalog.sqlite3")
    policy = PathPolicy(root)
    journals = JournalStore(root / "data" / "operations")
    catalog = CatalogService(db, policy)
    snapshots = SnapshotService(db, policy, journals)
    restore = RestoreService(db, policy, journals, snapshots)
    return db, catalog, snapshots, restore


def test_file_snapshot_is_independent_and_restore_does_not_create_safety(tmp_path):
    source = tmp_path / "save.dat"
    source.write_bytes(b"A")
    db, catalog, snapshots, restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.FILE, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "Estado A")
    source.write_bytes(b"B")
    restore.restore(profile.id, snap.id)
    assert source.read_bytes() == b"A"
    assert not any(item.kind.value == "safety" for item in db.list_snapshots(profile.id))


def test_manual_refresh_reconciles_external_additions_and_deletions(tmp_path):
    source = tmp_path / "save.sav"
    source.write_bytes(b"original")
    db, catalog, snapshots, _restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.FILE, str(source), StorageMode.APP, "")
    existing = snapshots.create(profile.id, "Existing")
    snapshots.snapshot_path(profile, existing).unlink()
    namespace = catalog.policy.resolve(profile).namespace
    manual = namespace / "manual.sav"
    manual.write_bytes(b"manual")

    assert MaintenanceService(db, snapshots).refresh_profile(profile.id) is True

    assert db.get_snapshot(existing.id).status == SnapshotStatus.MISSING
    imported = [item for item in db.list_snapshots(profile.id) if item.relative_path == "manual.sav"]
    assert len(imported) == 1
    assert imported[0].name == "manual.sav"
    assert manual.read_bytes() == b"manual"


def test_manual_refresh_keeps_catalog_when_backup_folder_cannot_be_read(tmp_path, monkeypatch):
    source = tmp_path / "save.sav"
    source.write_bytes(b"original")
    db, catalog, snapshots, _restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.FILE, str(source), StorageMode.APP, "")
    existing = snapshots.create(profile.id, "Existing")

    def deny_access(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr("save_manager.services.maintenance.visible_children", deny_access)
    with pytest.raises(OperationError):
        MaintenanceService(db, snapshots).refresh_profile(profile.id)

    assert db.get_snapshot(existing.id).status == SnapshotStatus.READY


def test_restore_accepts_manually_edited_file_payload(tmp_path):
    source = tmp_path / "save.png"
    source.write_bytes(b"original")
    db, catalog, snapshots, restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.FILE, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "Estado A")
    snapshots.snapshot_path(profile, snap).write_bytes(b"ajustado manualmente")

    restore.restore(profile.id, snap.id)

    assert source.read_bytes() == b"ajustado manualmente"
    assert snapshots.verify(profile, db.get_snapshot(snap.id))
    manifest = read_manifest(snapshots.manifest_path(profile, snap))
    assert manifest["files"][0]["size"] == len(b"ajustado manualmente")


def test_restore_accepts_manually_edited_directory_payload(tmp_path):
    source = tmp_path / "SaveData"
    source.mkdir()
    (source / "slot.dat").write_bytes(b"original")
    db, catalog, snapshots, restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.DIRECTORY, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "Estado A")
    payload = snapshots.snapshot_path(profile, snap)
    (payload / "slot.dat").write_bytes(b"ajustado manualmente")
    (payload / "extra.dat").write_bytes(b"novo")

    restore.restore(profile.id, snap.id)

    assert (source / "slot.dat").read_bytes() == b"ajustado manualmente"
    assert (source / "extra.dat").read_bytes() == b"novo"
    assert snapshots.verify(profile, db.get_snapshot(snap.id))


def test_file_snapshot_names_preserve_their_extension(tmp_path):
    source = tmp_path / "save.sav"
    source.write_bytes(b"original")
    db, catalog, snapshots, restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.FILE, str(source), StorageMode.APP, "")

    snap = snapshots.create(profile.id, "slot.sav")

    assert snap.name == "slot.sav"
    assert snap.relative_path == "slot.sav"
    assert snapshots.snapshot_path(profile, snap).name == "slot.sav"
    assert snapshots.snapshot_path(profile, snap).suffix == ".sav"
    source.write_bytes(b"alterado")
    restore.restore(profile.id, snap.id)
    assert source.name == "save.sav"
    assert source.read_bytes() == b"original"

    renamed = MaintenanceService(db, snapshots).rename_snapshot(snap.id, "renamed.sav")
    assert renamed.name == "renamed.sav"
    assert renamed.relative_path == "renamed.sav"
    assert snapshots.snapshot_path(profile, renamed).suffix == ".sav"


def test_overwrite_replaces_existing_file_payload_and_refreshes_created_at(tmp_path, monkeypatch):
    source = tmp_path / "save.dat"
    source.write_bytes(b"original")
    db, catalog, snapshots, _restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.FILE, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "Estado A", "nota")
    created_at = snap.created_at
    source.write_bytes(b"novo estado")
    from datetime import UTC, datetime

    overwrite_time = datetime(2099, 1, 1, tzinfo=UTC).isoformat()
    monkeypatch.setattr("save_manager.services.snapshot.utc_now", lambda: datetime(2099, 1, 1, tzinfo=UTC))

    result = snapshots.overwrite(profile.id, snap.id)

    assert result.id == snap.id
    assert result.name == "Estado A"
    assert result.notes == "nota"
    assert result.created_at != created_at
    assert result.created_at == overwrite_time
    assert db.get_snapshot(snap.id).created_at == overwrite_time
    assert read_manifest(snapshots.manifest_path(profile, result))["created_at"] == overwrite_time
    assert snapshots.snapshot_path(profile, result).read_bytes() == b"novo estado"
    assert len(db.list_snapshots(profile.id)) == 1
    assert snapshots.verify(profile, db.get_snapshot(snap.id))


def test_overwrite_replaces_existing_directory_payload(tmp_path):
    source = tmp_path / "SaveData"
    source.mkdir()
    (source / "old.dat").write_bytes(b"original")
    db, catalog, snapshots, _restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.DIRECTORY, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "Estado A")
    source.joinpath("old.dat").unlink()
    (source / "new.dat").write_bytes(b"novo")

    snapshots.overwrite(profile.id, snap.id)

    payload = snapshots.snapshot_path(profile, snap)
    assert not (payload / "old.dat").exists()
    assert (payload / "new.dat").read_bytes() == b"novo"
    assert snapshots.verify(profile, db.get_snapshot(snap.id))


def test_app_backup_layout_uses_profile_folder_and_direct_file(tmp_path):
    source = tmp_path / "save.dat"
    source.write_bytes(b"A")
    _db, catalog, snapshots, _restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil 1", SourceType.FILE, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "Save1.file")
    expected = tmp_path / "app" / "Saverino Backup" / "Perfil 1" / "Save1.file"
    assert snapshots.snapshot_path(profile, snap) == expected
    assert expected.is_file()
    assert expected.read_bytes() == b"A"
    assert not (expected / "payload").exists()
    assert not (expected.parent / ".save-manager").exists()
    assert snapshots.manifest_path(profile, snap).parent == tmp_path / "app" / "data" / "profiles" / str(profile.id)


def test_app_backup_layout_uses_profile_folder_and_direct_directory(tmp_path):
    source = tmp_path / "SaveData"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "slot.dat").write_bytes(b"A")
    _db, catalog, snapshots, _restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil 2", SourceType.DIRECTORY, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "Save1")
    expected = tmp_path / "app" / "Saverino Backup" / "Perfil 2" / "Save1"
    assert snapshots.snapshot_path(profile, snap) == expected
    assert (expected / "nested" / "slot.dat").read_bytes() == b"A"
    assert not (expected / "payload").exists()
    assert not (expected.parent / ".staging").exists()


def test_directory_restore_removes_extra_and_keeps_empty_dirs(tmp_path):
    source = tmp_path / "SaveData"
    (source / "empty").mkdir(parents=True)
    (source / "sub").mkdir()
    (source / "sub" / "é.bin").write_bytes(b"xyz")
    _db, catalog, snapshots, restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.DIRECTORY, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "Estado A")
    (source / "extra").write_text("x", encoding="utf-8")
    (source / "sub" / "é.bin").write_bytes(b"changed")
    restore.restore(profile.id, snap.id)
    assert not (source / "extra").exists()
    assert (source / "empty").is_dir()
    assert (source / "sub" / "é.bin").read_bytes() == b"xyz"


def test_integrity_marks_tampered_payload_corrupt(tmp_path):
    source = tmp_path / "save.dat"
    source.write_bytes(b"A")
    db, catalog, snapshots, restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.FILE, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "Estado A")
    payload = snapshots.snapshot_path(profile, snap)
    payload.write_bytes(b"tampered")
    assert MaintenanceService(db, snapshots).verify_integrity(snap.id).value == "corrupt"


def test_trash_can_be_deleted_permanently(tmp_path):
    source = tmp_path / "save.dat"
    source.write_bytes(b"A")
    db, catalog, snapshots, _restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.FILE, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "Estado A")
    maintenance = MaintenanceService(db, snapshots)
    manifest = snapshots.manifest_path(profile, snap)
    maintenance.trash_snapshot(snap.id)
    assert not (snapshots._paths(profile).namespace / ".trash").exists()
    assert (snapshots.policy.trash_dir(profile) / str(snap.id)).exists()
    maintenance.delete_permanently(snap.id)
    assert db.get_snapshot(snap.id) is None
    assert not manifest.exists()


def test_external_file_rename_updates_snapshot_index_and_manifest(tmp_path):
    source = tmp_path / "test.png"
    source.write_bytes(b"A")
    db, catalog, snapshots, _restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil 1", SourceType.FILE, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "test.png")
    old_path = snapshots.snapshot_path(profile, snap)
    new_path = old_path.with_name("test1")
    old_path.rename(new_path)

    maintenance = MaintenanceService(db, snapshots)
    assert maintenance.sync_profile(profile.id) is True
    refreshed = db.get_snapshot(snap.id)
    assert refreshed is not None
    assert refreshed.name == "test1"
    assert refreshed.relative_path == "test1"
    assert snapshots.manifest_path(profile, snap).read_text(encoding="utf-8").find('"relative_path": "test1"') >= 0


@pytest.mark.parametrize("name", ["save:name", "save/name", "CON", "save."])
def test_snapshot_name_rejects_invalid_windows_file_component(tmp_path, name):
    source = tmp_path / "test.png"
    source.write_bytes(b"A")
    db, catalog, snapshots, _restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.FILE, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "save-inicial.png")

    with pytest.raises(DomainError, match="caracteres inválidos|reservado|terminar"):
        MaintenanceService(db, snapshots).rename_snapshot(snap.id, name)


def test_external_profile_folder_rename_updates_profile_and_namespace(tmp_path):
    source = tmp_path / "test.png"
    source.write_bytes(b"A")
    db, catalog, snapshots, _restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil 1", SourceType.FILE, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "test.png")
    old_namespace = snapshots.snapshot_path(profile, snap).parent
    new_namespace = old_namespace.parent / "Perfil Renomeado"
    old_namespace.rename(new_namespace)

    assert catalog.sync_profile_folder(profile.id) is True
    refreshed = db.get_profile(profile.id)
    assert refreshed is not None
    assert refreshed.name == "Perfil Renomeado"
    assert catalog.policy.resolve(refreshed).namespace == new_namespace


def test_open_origin_targets_parent_folder_for_file_source(tmp_path, monkeypatch):
    source = tmp_path / "folder" / "test.png"
    source.parent.mkdir()
    source.write_bytes(b"A")
    db, catalog, _snapshots, _restore = make_services(tmp_path)
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.FILE, str(source), StorageMode.APP, "")
    calls = []
    monkeypatch.setattr("save_manager.ui.main_window.open_in_explorer", lambda path: calls.append(path))
    from save_manager.ui.main_window import MainWindow
    MainWindow._open_folder(type("Window", (), {"current_profile": profile, "policy": catalog.policy})(), "source")
    assert calls == [source.parent]

