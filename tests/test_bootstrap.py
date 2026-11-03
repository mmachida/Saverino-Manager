from save_manager import bootstrap
from save_manager.storage.db import CatalogDB


def test_initialize_uses_appdata_and_migrates_legacy_catalog(tmp_path, monkeypatch):
    roaming = tmp_path / "roaming"
    legacy_root = tmp_path / "old-install"
    legacy_db = CatalogDB(legacy_root / "data" / "catalog.sqlite3")
    legacy_db.set_setting("selected_game", "legacy-id")
    (legacy_root / "data" / "profiles" / "profile-id").mkdir(parents=True)
    (legacy_root / "data" / "profiles" / "profile-id" / "profile.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setattr(bootstrap, "app_root", lambda: legacy_root)

    paths = bootstrap.initialize()

    assert paths["root"] == roaming / "Saverino Manager"
    assert paths["catalog"] == roaming / "Saverino Manager" / "data" / "catalog.sqlite3"
    assert CatalogDB(paths["catalog"]).get_setting("selected_game") == "legacy-id"
    assert (paths["data"] / "profiles" / "profile-id" / "profile.json").is_file()
