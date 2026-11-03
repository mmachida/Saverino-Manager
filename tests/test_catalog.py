import shutil

import pytest

from save_manager.domain.models import Game, GameSettings, SourceType, StorageMode
from save_manager.filesystem.policy import PathPolicy
from save_manager.services.catalog import CatalogService
from save_manager.storage.db import CatalogDB


def test_catalog_persists_games_and_profiles(tmp_path):
    root = tmp_path / "app"
    source = tmp_path / "save.dat"
    source.write_bytes(b"save")
    db = CatalogDB(root / "data" / "catalog.sqlite3")
    service = CatalogService(db, PathPolicy(root))
    game = service.create_game("  Meu Jogo  ")
    profile = service.create_profile(game.id, "Campanha", SourceType.FILE, str(source), StorageMode.APP, "")
    reopened = CatalogDB(root / "data" / "catalog.sqlite3")
    assert [g.name for g in reopened.list_games()] == ["Meu Jogo"]
    assert reopened.get_profile(profile.id).source_path == str(source.resolve())


def test_invalid_catalog_is_not_replaced(tmp_path):
    path = tmp_path / "data" / "catalog.sqlite3"
    path.parent.mkdir()
    original = b"not sqlite"
    path.write_bytes(original)
    with pytest.raises(RuntimeError):
        CatalogDB(path)
    assert path.read_bytes() == original


def test_renaming_profile_moves_its_visible_backup_folder(tmp_path):
    root = tmp_path / "app"
    source = tmp_path / "save.dat"
    source.write_bytes(b"save")
    db = CatalogDB(root / "data" / "catalog.sqlite3")
    service = CatalogService(db, PathPolicy(root))
    game = service.create_game("Jogo")
    profile = service.create_profile(game.id, "Perfil 1", SourceType.FILE, str(source), StorageMode.APP, "")
    old_path = root / "Saverino Backup" / "Perfil 1"
    renamed = service.rename_profile(profile.id, "Perfil 2")
    assert renamed.name == "Perfil 2"
    assert old_path.exists() is False
    assert (root / "Saverino Backup" / "Perfil 2").is_dir()


def test_deleting_game_removes_catalog_profiles_and_managed_backups(tmp_path):
    root = tmp_path / "app"
    source = tmp_path / "save.dat"
    source.write_bytes(b"save")
    db = CatalogDB(root / "data" / "catalog.sqlite3")
    service = CatalogService(db, PathPolicy(root))
    game = service.create_game("Jogo")
    profile = service.create_profile(game.id, "Perfil 1", SourceType.FILE, str(source), StorageMode.APP, "")
    namespace = root / "Saverino Backup" / "Perfil 1"
    service.delete_game(game.id)
    assert db.get_game(game.id) is None
    assert db.get_profile(profile.id) is None
    assert not namespace.exists()


def test_legacy_backup_metadata_is_moved_out_of_visible_folder(tmp_path):
    root = tmp_path / "app"
    source = tmp_path / "save.dat"
    source.write_bytes(b"save")
    policy = PathPolicy(root)
    db = CatalogDB(root / "data" / "catalog.sqlite3")
    service = CatalogService(db, policy)
    game = service.create_game("Jogo")
    profile = service.create_profile(game.id, "Perfil 1", SourceType.FILE, str(source), StorageMode.APP, "")
    namespace = policy.resolve(profile).namespace
    old_metadata = namespace / "save-manager"
    old_metadata.mkdir()
    old_metadata.joinpath("profile.json").write_text(policy.metadata_dir(profile).joinpath("profile.json").read_text(encoding="utf-8"), encoding="utf-8")
    shutil.rmtree(policy.metadata_dir(profile))
    (namespace / "staging").mkdir()
    service.migrate_legacy_storage()
    assert not old_metadata.exists()
    assert not (namespace / "staging").exists()
    assert policy.metadata_dir(profile).joinpath("profile.json").exists()


def test_external_empty_profile_folder_rename_updates_catalog(tmp_path):
    root = tmp_path / "app"
    source = tmp_path / "save.dat"
    source.write_bytes(b"save")
    policy = PathPolicy(root)
    db = CatalogDB(root / "data" / "catalog.sqlite3")
    service = CatalogService(db, policy)
    game = service.create_game("Jogo")
    profile = service.create_profile(game.id, "Perfil 1", SourceType.FILE, str(source), StorageMode.APP, "")
    old_path = policy.resolve(profile).namespace
    new_path = old_path.parent / "Perfil Renomeado"
    old_path.rename(new_path)

    assert service.sync_profile_folder(profile.id) is True
    refreshed = db.get_profile(profile.id)
    assert refreshed is not None
    assert refreshed.name == "Perfil Renomeado"
    assert policy.resolve(refreshed).namespace == new_path


def test_catalog_export_and_import_preserve_metadata_without_payloads(tmp_path):
    source_root = tmp_path / "source"
    source_db = CatalogDB(source_root / "data" / "catalog.sqlite3")
    game = Game("Exported game")
    source_db.add_game(game)
    source_db.set_setting("app_settings", {"language": "pt-BR", "sound_effect_volume": 25})
    metadata = source_root / "data" / "profiles" / "profile-id"
    metadata.mkdir(parents=True)
    (metadata / "profile.json").write_text('{"profile_id":"profile-id"}', encoding="utf-8")
    export_path = tmp_path / "exports" / "saverino-manager-data.saverino"

    source_db.export_to(export_path)

    target_db = CatalogDB(tmp_path / "target" / "data" / "catalog.sqlite3")
    target_db.import_from(export_path)

    assert [item.name for item in target_db.list_games()] == ["Exported game"]
    assert target_db.get_setting("app_settings") == {"language": "pt-BR", "sound_effect_volume": 25}
    assert (tmp_path / "target" / "data" / "profiles" / "profile-id" / "profile.json").is_file()
    assert not (tmp_path / "target" / "Saverino Backup").exists()

def test_creating_game_and_profile_prepares_backup_folders(tmp_path):
    root = tmp_path / "app"
    source = tmp_path / "save.dat"
    source.write_bytes(b"save")
    db = CatalogDB(root / "data" / "catalog.sqlite3")
    service = CatalogService(db, PathPolicy(root))
    settings = GameSettings(SourceType.FILE, str(source), StorageMode.BESIDE_SOURCE)

    game = service.create_game("Jogo", settings)

    game_folder = source.parent / "Saverino - Jogo"
    assert game_folder.is_dir()
    assert list(game_folder.iterdir()) == []

    service.create_profile(game.id, "Perfil")
    assert (game_folder / "Perfil").is_dir()

def test_deleting_game_removes_the_entire_game_backup_folder(tmp_path):
    root = tmp_path / "app"
    source = tmp_path / "save.dat"
    source.write_bytes(b"save")
    db = CatalogDB(root / "data" / "catalog.sqlite3")
    service = CatalogService(db, PathPolicy(root))
    settings = GameSettings(SourceType.FILE, str(source), StorageMode.BESIDE_SOURCE)
    game = service.create_game("Jogo", settings)
    service.create_profile(game.id, "Perfil")
    game_folder = source.parent / "Saverino - Jogo"
    (game_folder / "manual-folder" / "nested").mkdir(parents=True)
    (game_folder / "manual-folder" / "nested" / "marker.txt").write_text("managed", encoding="utf-8")

    service.delete_game(game.id)

    assert not game_folder.exists()
    assert source.is_file()
