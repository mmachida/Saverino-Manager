import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QGroupBox, QMessageBox

from save_manager.domain.errors import DomainError
from save_manager.domain.models import GameSettings, SourceType, StorageMode
from save_manager.filesystem.policy import PathPolicy
from save_manager.i18n import LanguageManager
from save_manager.services.catalog import CatalogService
from save_manager.storage.db import CatalogDB
from save_manager.ui.dialogs import ImportExportDialog
from save_manager.ui.game_settings_dialog import MAX_GAME_NAME_LENGTH, GameSettingsDialog
from save_manager.ui.settings_dialog import DEFAULT_APP_SETTINGS, SettingsDialog
from save_manager.ui.theme_dialog import ThemeDialog
from save_manager.ui.themes import INITIAL_THEME, THEMES, normalize_theme, theme_stylesheet


def make_catalog(tmp_path):
    root = tmp_path / "app"
    db = CatalogDB(root / "data" / "catalog.sqlite3")
    return db, CatalogService(db, PathPolicy(root))


def test_settings_dialog_offers_only_supported_destination_modes(qtbot):
    dialog = GameSettingsDialog(translator=LanguageManager("en-US"))
    qtbot.addWidget(dialog)

    assert dialog.storage.count() == 3
    assert [dialog.storage.itemData(index) for index in range(dialog.storage.count())] == [
        "app_folder", "beside_source", "custom",
    ]
    assert dialog.storage.currentData() == "app_folder"
    assert dialog.name.maxLength() == MAX_GAME_NAME_LENGTH == 50
    assert dialog.name.toolTip() == "Name used for project backup folders."
    assert dialog.source_type.toolTip() == "Choose a file or folder source."
    assert dialog.source.toolTip() == "Select the original save."
    assert dialog.storage.toolTip() == (
        "Choose where backups are stored. App folder uses Saverino backup beside the executable "
        "and requires write access."
    )
    assert dialog.backup.toolTip() == "Select the custom backup folder."


def test_existing_destination_mode_is_preserved_in_dialog(qtbot):
    settings = GameSettings(SourceType.FILE, "C:/game/save.dat", StorageMode.BESIDE_SOURCE)
    dialog = GameSettingsDialog(game_name="Game", settings=settings, translator=LanguageManager("en-US"))
    qtbot.addWidget(dialog)

    assert dialog.storage.currentData() == "beside_source"


def test_import_export_dialog_uses_large_localized_cards(qtbot):
    translator = LanguageManager("en-US")
    dialog = ImportExportDialog(translator=translator)
    qtbot.addWidget(dialog)

    assert dialog.export_card.title_label.text() == "Export"
    assert dialog.import_card.title_label.text() == "Import"
    assert "Save files and folders must be moved manually" in dialog.note_label.text()
    assert dialog.export_card.toolTip() == "Export project, profile, and app settings."
    assert dialog.import_card.toolTip() == "Import previously exported settings."
    dialog._choose("export")
    assert dialog.action == "export"


def test_import_export_dialog_uses_selected_theme_colors(qtbot):
    dialog = ImportExportDialog(theme_id="hollow_knight")
    qtbot.addWidget(dialog)

    assert "background: #121d49" in dialog.styleSheet()
    assert "background: #1d3170" in dialog.styleSheet()
    assert "background: #292d50" in dialog.styleSheet()
    assert "color: #dce4ff" in dialog.styleSheet()


def test_theme_dialog_lists_cards_and_emits_selection(qtbot):
    translator = LanguageManager("en-US")
    dialog = ThemeDialog(translator=translator, current_theme="default", themes=THEMES)
    qtbot.addWidget(dialog)

    assert [theme.id for theme in THEMES] == [
        "default", "night_mode", "hollow_knight", "silk_song", "elden_ring", "batman_arkham_knight",
    ]
    assert set(dialog.cards) == {"default", "night_mode", "hollow_knight", "silk_song", "elden_ring", "batman_arkham_knight"}
    assert dialog.cards["default"].property("selected") is True
    selected = []
    dialog.theme_selected.connect(selected.append)

    dialog._select_theme("hollow_knight")

    assert selected == ["hollow_knight"]
    assert dialog.current_theme == "hollow_knight"
    assert dialog.cards["hollow_knight"].property("selected") is True
    assert dialog.cards["default"].property("selected") is False
    assert dialog.cards["hollow_knight"].toolTip() == "Click to apply this theme."


def test_theme_palette_has_safe_default_and_distinct_preview_colors():
    assert INITIAL_THEME == "night_mode"
    assert normalize_theme("missing") == "default"
    assert "QPushButton { background: #f0f0f0" in theme_stylesheet("default")
    assert "border: 1px solid #adadad" in theme_stylesheet("default")
    assert "border-top-color: #ffffff" in theme_stylesheet("default")
    assert "border-bottom-color: #8f8f8f" in theme_stylesheet("default")
    assert "QGroupBox { border: 1px solid #808080" in theme_stylesheet("default")
    assert "QCheckBox::indicator { width: 13px" in theme_stylesheet("default")
    assert "QCheckBox::indicator:checked { background: #0078d4" in theme_stylesheet("default")
    assert "checkbox-check.svg" in theme_stylesheet("default")
    assert "QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right" in theme_stylesheet("default")
    assert "QComboBox::down-arrow { image: url(" in theme_stylesheet("default")
    assert "width: 8px; height: 8px; margin: 0" in theme_stylesheet("default")
    assert "combo-arrow-default.svg" in theme_stylesheet("default")
    assert "QPushButton#destructiveButton { background: #d6aaa4; color: #2f201e" in theme_stylesheet("default")
    assert "QPushButton#destructiveButton:disabled { background: #a08b87; color: #f1eeee" in theme_stylesheet("default")
    for theme in THEMES:
        stylesheet = theme_stylesheet(theme.id)
        assert f"QCheckBox::indicator:checked {{ background: {theme.colors['primary']}" in stylesheet
        assert "checkbox-check.svg" in stylesheet
    assert "QPushButton { background: #2d2d30" in theme_stylesheet("night_mode")
    assert "QPushButton { background: #2f5f9e" in theme_stylesheet("hollow_knight")
    assert "QPushButton { background: #443332" in theme_stylesheet("silk_song")
    assert "border-top-color: #674945" in theme_stylesheet("silk_song")
    assert "border-bottom-color: #674945" in theme_stylesheet("silk_song")
    assert "QPushButton { background: #8b7030" in theme_stylesheet("elden_ring")
    assert "QPushButton { background: #8b6738" in theme_stylesheet("batman_arkham_knight")
    assert "QListWidget::item:selected { background: #8b7030; color: #f2e8c9" in theme_stylesheet("elden_ring")
    assert "QListWidget::item:selected { background: #8b6738; color: #edf0f0" in theme_stylesheet("batman_arkham_knight")


def test_application_settings_dialog_collects_hotkeys_and_preferences(qtbot):
    dialog = SettingsDialog(settings={"language": "en-US"})
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Settings"
    assert dialog.global_hotkeys.text() == "Enable global hotkeys"
    assert dialog.load_save_hotkey_without_confirmation.text() == "Load save without confirmation"
    assert dialog.create_save_hotkey is not None
    assert dialog.load_save_hotkey is not None
    assert dialog.initialize_with_windows.text() == "Initialize with Windows"
    assert not dialog.initialize_with_windows.isChecked()
    assert dialog.always_on_top.text() == "Always on top"
    assert dialog.automatically_check_for_updates.text() == "Check for updates on startup"
    assert dialog.sound_effect_import_enabled.text() == "Sound effect when importing a save"
    assert dialog.sound_effect_load_enabled.text() == "Sound effect when loading a save"
    assert dialog.sound_effect_volume.value() == 50
    assert dialog.sound_effect_volume_label.text() == "50%"
    assert dialog.language.toolTip() == "Choose the interface language."
    assert dialog.always_on_top.toolTip() == "Keep the window above other windows."
    assert dialog.sound_effect_volume.toolTip() == "Set the volume for save sounds."
    assert dialog.default_button.toolTip() == "Restore all settings except language."
    assert [dialog.layout().itemAt(index).widget().title() for index in range(dialog.layout().count())
            if isinstance(dialog.layout().itemAt(index).widget(), QGroupBox)] == ["Configuration", "Audio", "Hotkeys"]

    dialog.global_hotkeys.setChecked(True)
    dialog.create_save_hotkey.setKeySequence(QKeySequence("Ctrl+Alt+S"))
    dialog.load_save_hotkey.setKeySequence(QKeySequence("Ctrl+Alt+L"))
    dialog.initialize_with_windows.setChecked(True)
    dialog.always_on_top.setChecked(True)

    assert dialog.values() == {
        "global_hotkeys_enabled": True,
        "create_save_hotkey": "Ctrl+Alt+S",
        "load_save_hotkey": "Ctrl+Alt+L",
        "load_save_hotkey_without_confirmation": False,
        "initialize_with_windows": True,
        "always_on_top": True,
        "sound_effect_import_enabled": True,
        "sound_effect_load_enabled": True,
        "sound_effect_auto_save_enabled": False,
        "sound_effect_volume": 50,
        "automatically_check_for_updates": True,
        "last_update_check_at": "",
        "language": "en-US",
    }


def test_hotkey_reset_buttons_clear_only_their_field(qtbot):
    dialog = SettingsDialog(
        settings={"create_save_hotkey": "Ctrl+Alt+S", "load_save_hotkey": "Ctrl+Alt+L"},
    )
    qtbot.addWidget(dialog)

    dialog.create_save_hotkey_reset.click()
    assert dialog.create_save_hotkey.keySequence().isEmpty()
    assert dialog.load_save_hotkey.keySequence().toString(QKeySequence.SequenceFormat.PortableText) == "Ctrl+Alt+L"

    dialog.load_save_hotkey_reset.click()
    assert dialog.load_save_hotkey.keySequence().isEmpty()


def test_settings_default_button_restores_all_preferences(qtbot, monkeypatch):
    dialog = SettingsDialog(
        settings={
            "language": "pt-BR",
            "global_hotkeys_enabled": True,
            "create_save_hotkey": "Ctrl+Alt+S",
            "load_save_hotkey": "Ctrl+Alt+L",
            "always_on_top": True,
            "sound_effect_import_enabled": False,
            "sound_effect_load_enabled": False,
            "sound_effect_volume": 5,
            "automatically_check_for_updates": False,
            "last_update_check_at": "2026-01-01T00:00:00+00:00",
        },
    )
    qtbot.addWidget(dialog)

    monkeypatch.setattr(
        "save_manager.ui.settings_dialog.QMessageBox.question",
        lambda *args: QMessageBox.StandardButton.Yes,
    )
    dialog.default_button.click()

    expected = dict(DEFAULT_APP_SETTINGS)
    expected["language"] = "pt-BR"
    assert dialog.values() == expected
    assert dialog.translator.language == "pt-BR"


def test_profiles_inherit_one_game_configuration(tmp_path):
    db, catalog = make_catalog(tmp_path)
    source = tmp_path / "save.dat"
    source.write_bytes(b"A")
    game = catalog.create_game(
        "Jogo",
        GameSettings(SourceType.FILE, str(source), StorageMode.APP),
    )
    first = catalog.create_profile(game.id, "Campanha 1")
    second = catalog.create_profile(game.id, "Campanha 2", notes="segunda")

    assert db.get_game_settings(game.id).source_path == str(source.resolve())
    assert first.source_path == second.source_path == str(source.resolve())
    assert first.storage_mode == second.storage_mode == StorageMode.APP
    assert catalog.policy.resolve(first).namespace != catalog.policy.resolve(second).namespace


def test_default_destination_groups_backups_by_game_name(tmp_path):
    _db, catalog = make_catalog(tmp_path)
    source = tmp_path / "game" / "save.dat"
    source.parent.mkdir()
    source.write_bytes(b"A")
    game = catalog.create_game("Meu Jogo", GameSettings(SourceType.FILE, str(source)))
    profile = catalog.create_profile(game.id, "Perfil principal")

    paths = catalog.policy.resolve(profile)

    assert paths.backup_base == source.parent.resolve()
    assert paths.namespace == source.parent / "Saverino - Meu Jogo" / "Perfil principal"
    assert paths.namespace.is_dir()


def test_profile_requires_game_configuration(tmp_path):
    _db, catalog = make_catalog(tmp_path)
    game = catalog.create_game("Jogo")
    with pytest.raises(DomainError, match="Configurações do jogo"):
        catalog.create_profile(game.id, "Campanha")


@pytest.mark.parametrize("name", ["Meu:Jogo", "Meu/Jogo", "Jogo?", "CON", "COM1", "Jogo."])
def test_game_name_must_be_valid_windows_folder_component(tmp_path, name):
    _db, catalog = make_catalog(tmp_path)

    with pytest.raises(DomainError, match="pasta|reservado|terminar"):
        catalog.create_game(name)


def test_renaming_game_rejects_invalid_folder_component(tmp_path):
    _db, catalog = make_catalog(tmp_path)
    game = catalog.create_game("Jogo")

    with pytest.raises(DomainError, match="pasta|reservado|terminar"):
        catalog.rename_game(game.id, "Jogo:novo")


def test_reconfiguring_game_migrates_all_profile_namespaces(tmp_path):
    db, catalog = make_catalog(tmp_path)
    source = tmp_path / "save.dat"
    source.write_bytes(b"A")
    old_base = tmp_path / "old-backups"
    new_base = tmp_path / "new-backups"
    game = catalog.create_game("Jogo", GameSettings(SourceType.FILE, str(source), StorageMode.CUSTOM, str(old_base)))
    first = catalog.create_profile(game.id, "Um")
    second = catalog.create_profile(game.id, "Dois")
    (catalog.policy.resolve(first).namespace / "estado").write_bytes(b"first")
    (catalog.policy.resolve(second).namespace / "estado").write_bytes(b"second")

    catalog.configure_game(game.id, GameSettings(SourceType.FILE, str(source), StorageMode.CUSTOM, str(new_base)))

    refreshed = db.get_profile(first.id)
    refreshed_second = db.get_profile(second.id)
    assert refreshed.backup_base_path == str(new_base.resolve())
    game_root = new_base / "Saverino - Jogo"
    assert (game_root / "Um" / "estado").read_bytes() == b"first"
    assert (game_root / "Dois" / "estado").read_bytes() == b"second"
    assert (old_base / "Saverino - Jogo" / "Um" / "estado").read_bytes() == b"first"
    assert (old_base / "Saverino - Jogo" / "Dois" / "estado").read_bytes() == b"second"
    assert catalog.policy.resolve(refreshed).namespace == game_root / "Um"
    assert catalog.policy.resolve(refreshed_second).namespace == game_root / "Dois"


def test_move_removes_verified_source_namespaces_and_empty_group_folders(tmp_path):
    db, catalog = make_catalog(tmp_path)
    source = tmp_path / "save.dat"
    source.write_bytes(b"original")
    old_base = tmp_path / "old-backups"
    game = catalog.create_game(
        "Jogo",
        GameSettings(SourceType.FILE, str(source), StorageMode.CUSTOM, str(old_base)),
    )
    first = catalog.create_profile(game.id, "Um")
    second = catalog.create_profile(game.id, "Dois")
    first_old_namespace = catalog.policy.resolve(first).namespace
    second_old_namespace = catalog.policy.resolve(second).namespace
    (first_old_namespace / "save-one.sav").write_bytes(b"one")
    (second_old_namespace / "save-two.png").write_bytes(b"two")

    result = catalog.configure_game(
        game.id,
        GameSettings(SourceType.FILE, str(source), StorageMode.APP_FOLDER),
        move_backups=True,
    )

    first_new_namespace = catalog.policy.resolve(db.get_profile(first.id)).namespace
    second_new_namespace = catalog.policy.resolve(db.get_profile(second.id)).namespace
    old_project_folder = old_base / "Saverino - Jogo"
    assert result.transfer_action == "move"
    assert result.transfer_complete is True
    assert (first_new_namespace / "save-one.sav").read_bytes() == b"one"
    assert (second_new_namespace / "save-two.png").read_bytes() == b"two"
    assert not first_old_namespace.exists()
    assert not second_old_namespace.exists()
    assert not old_project_folder.exists()
    assert old_base.is_dir()
    assert source.read_bytes() == b"original"


def test_move_preserves_nonempty_old_group_folder(tmp_path):
    _db, catalog = make_catalog(tmp_path)
    source = tmp_path / "save.dat"
    source.write_bytes(b"original")
    old_base = tmp_path / "old-backups"
    game = catalog.create_game(
        "Jogo",
        GameSettings(SourceType.FILE, str(source), StorageMode.CUSTOM, str(old_base)),
    )
    profile = catalog.create_profile(game.id, "Um")
    old_namespace = catalog.policy.resolve(profile).namespace
    (old_namespace / "save.sav").write_bytes(b"backup")
    unrelated = old_namespace.parent / "unmanaged.txt"
    unrelated.write_text("keep", encoding="utf-8")

    result = catalog.configure_game(
        game.id,
        GameSettings(SourceType.FILE, str(source), StorageMode.APP_FOLDER),
        move_backups=True,
    )

    assert result.transfer_complete is True
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert old_namespace.parent.is_dir()


def test_reconfiguring_game_adopts_existing_profile_namespace(tmp_path):
    db, catalog = make_catalog(tmp_path)
    source = tmp_path / "save.dat"
    source.write_bytes(b"A")
    old_base = tmp_path / "old-backups"
    game = catalog.create_game("Jogo", GameSettings(SourceType.FILE, str(source), StorageMode.CUSTOM, str(old_base)))
    profile = catalog.create_profile(game.id, "Um")
    old_namespace = catalog.policy.resolve(profile).namespace
    target = source.parent / "Saverino - Jogo" / "Um"
    target.mkdir(parents=True)
    (target / "estado.sav").write_bytes(b"existing")

    catalog.configure_game(game.id, GameSettings(SourceType.FILE, str(source), StorageMode.BESIDE_SOURCE))

    refreshed = db.get_profile(profile.id)
    assert catalog.policy.resolve(refreshed).namespace == target
    assert (target / "estado.sav").read_bytes() == b"existing"
    assert old_namespace.is_dir()


def test_reconfiguring_game_can_point_to_new_destination_without_copying(tmp_path):
    db, catalog = make_catalog(tmp_path)
    source = tmp_path / "save.dat"
    source.write_bytes(b"A")
    old_base = tmp_path / "old-backups"
    new_base = tmp_path / "new-backups"
    game = catalog.create_game("Jogo", GameSettings(SourceType.FILE, str(source), StorageMode.CUSTOM, str(old_base)))
    profile = catalog.create_profile(game.id, "Um")
    old_namespace = catalog.policy.resolve(profile).namespace
    (old_namespace / "estado.sav").write_bytes(b"old")

    catalog.configure_game(
        game.id,
        GameSettings(SourceType.FILE, str(source), StorageMode.CUSTOM, str(new_base)),
        copy_backups=False,
    )

    refreshed = db.get_profile(profile.id)
    new_namespace = catalog.policy.resolve(refreshed).namespace
    assert new_namespace == new_base / "Saverino - Jogo" / "Um"
    assert new_namespace.is_dir()
    assert not (new_namespace / "estado.sav").exists()
    assert (old_namespace / "estado.sav").read_bytes() == b"old"


def test_reconfiguring_game_updates_all_profile_metadata(tmp_path):
    db, catalog = make_catalog(tmp_path)
    source = tmp_path / "save.dat"
    source.write_bytes(b"A")
    new_source = tmp_path / "save2.dat"
    new_source.write_bytes(b"B")
    game = catalog.create_game("Jogo", GameSettings(SourceType.FILE, str(source)))
    first = catalog.create_profile(game.id, "Um")
    second = catalog.create_profile(game.id, "Dois")

    catalog.configure_game(game.id, GameSettings(SourceType.FILE, str(new_source)))

    assert db.get_profile(first.id).source_path == str(new_source.resolve())
    assert db.get_profile(second.id).source_path == str(new_source.resolve())
    assert source.read_bytes() == b"A"
    assert new_source.read_bytes() == b"B"
