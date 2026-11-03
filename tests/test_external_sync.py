import shutil
import threading

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSplitter,
    QTextEdit,
)
from test_snapshots import make_services

from save_manager.domain.models import GameSettings, SnapshotStatus, SourceType, StorageMode
from save_manager.services.external_sync import ExternalSyncService
from save_manager.services.maintenance import MaintenanceService
from save_manager.storage.manifests import read_manifest, write_manifest
from save_manager.ui.filesystem_monitor import FilesystemMonitor
from save_manager.ui.main_window import MAX_GAME_MENU_NAME_LENGTH, MainWindow
from save_manager.version import APP_VERSION


@pytest.fixture
def setup_sync(tmp_path):
    db, catalog, snapshots, restore = make_services(tmp_path)
    source = tmp_path / "test.png"
    source.write_bytes(b"original")
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.FILE, str(source), StorageMode.APP, "")
    maintenance = MaintenanceService(db, snapshots)
    sync = ExternalSyncService(catalog, maintenance)
    return db, catalog, snapshots, restore, source, game, profile, sync


def test_two_identical_backups_keep_identity_when_renamed(setup_sync):
    db, _, snapshots, _, _, _, profile, sync = setup_sync
    first = snapshots.create(profile.id, "test.png", "nota 1")
    second = snapshots.create(profile.id, "copy.png", "nota 2")
    snapshots.snapshot_path(profile, first).rename(snapshots.snapshot_path(profile, first).with_name("test1"))
    snapshots.snapshot_path(profile, second).rename(snapshots.snapshot_path(profile, second).with_name("copy1"))
    sync.scan()
    assert (db.get_snapshot(first.id).name, db.get_snapshot(first.id).notes) == ("test1", "nota 1")
    assert (db.get_snapshot(second.id).name, db.get_snapshot(second.id).notes) == ("copy1", "nota 2")
    assert snapshots.verify(profile, db.get_snapshot(first.id))


def test_profile_and_payload_case_only_rename(setup_sync):
    db, catalog, snapshots, _, _, _, profile, sync = setup_sync
    snap = snapshots.create(profile.id, "test.png")
    namespace = catalog.policy.resolve(profile).namespace
    snapshots.snapshot_path(profile, snap).rename(namespace / "TEST")
    namespace.rename(namespace.with_name("PERFIL"))
    sync.scan()
    assert db.get_profile(profile.id).name == "PERFIL"
    assert db.get_snapshot(snap.id).name == "TEST"
    assert catalog.policy.resolve(db.get_profile(profile.id)).namespace.name == "PERFIL"


def test_empty_profiles_in_other_games_are_followed_by_identity(setup_sync):
    db, catalog, _, _, source, _, first, sync = setup_sync
    game = catalog.create_game("Outro jogo")
    second = catalog.create_profile(game.id, "Outro perfil", SourceType.FILE, str(source), StorageMode.APP, "")
    for profile in (first, second):
        path = catalog.policy.resolve(profile).namespace
        path.rename(path.with_name(profile.name + " novo"))
    result = sync.scan()
    assert result.profiles == {first.id, second.id}
    assert db.get_profile(first.id).name == "Perfil novo"
    assert db.get_profile(second.id).name == "Outro perfil novo"


def test_deleted_profile_is_removed_and_unrelated_folder_is_ignored(setup_sync):
    db, catalog, _, _, _, _, profile, sync = setup_sync
    path = catalog.policy.resolve(profile).namespace
    unrelated = path.with_name("Pasta alheia")
    unrelated.mkdir()
    path.rmdir()
    result = sync.scan()
    assert db.get_profile(profile.id) is None
    assert not db.list_snapshots(profile.id)
    assert profile.id in result.removed_profiles
    assert profile.id not in result.availability
    shutil.rmtree(unrelated)


def test_deleted_profile_removes_its_snapshot_records(setup_sync):
    db, catalog, snapshots, _, _, _, profile, sync = setup_sync
    snap = snapshots.create(profile.id, "Estado A")
    namespace = catalog.policy.resolve(profile).namespace
    shutil.rmtree(namespace)

    result = sync.scan()

    assert db.get_profile(profile.id) is None
    assert db.get_snapshot(snap.id) is None
    assert not catalog.policy.profile_data_dir(profile).exists()
    assert profile.id in result.removed_profiles


def test_missing_backup_reappears_and_restore_uses_the_renamed_path(setup_sync, tmp_path):
    db, _, snapshots, restore, source, _, profile, sync = setup_sync
    snap = snapshots.create(profile.id, "test.png")
    path = snapshots.snapshot_path(profile, snap)
    away = tmp_path / "temporariamente fora.png"
    path.rename(away)
    sync.scan()
    assert db.get_snapshot(snap.id).status == SnapshotStatus.MISSING
    renamed = path.with_name("test1")
    away.rename(renamed)
    sync.scan()
    assert db.get_snapshot(snap.id).status == SnapshotStatus.READY
    assert db.get_snapshot(snap.id).relative_path == renamed.name
    source.write_bytes(b"estado atual")
    restore.restore(profile.id, snap.id)
    assert source.read_bytes() == b"original"
    assert not any(item.kind.value == "safety" for item in db.list_snapshots(profile.id))


def test_sync_continues_with_missing_source_parent(setup_sync):
    db, catalog, snapshots, _, source, _, profile, sync = setup_sync
    snap = snapshots.create(profile.id, "test.png")
    profile.source_path = str(source.parent / "missing" / "test.png")
    db.update_profile(profile)
    path = catalog.policy.resolve(profile, validate_origin=False).namespace / snap.relative_path
    path.rename(path.with_name("renamed"))
    result = sync.scan()
    assert result.availability[profile.id] == (False, True)
    assert db.get_snapshot(snap.id).name == "renamed"


def test_legacy_manifests_get_identities_and_rename_hash_fallback(setup_sync):
    db, catalog, snapshots, _, _, _, profile, sync = setup_sync
    snap = snapshots.create(profile.id, "test.png")
    for path in (catalog.policy.metadata_dir(profile) / "profile.json", snapshots.manifest_path(profile, snap)):
        manifest = read_manifest(path)
        manifest.pop("filesystem_identity")
        write_manifest(path, manifest)
    payload = snapshots.snapshot_path(profile, snap)
    payload.rename(payload.with_name("legacy"))
    sync.scan()
    assert db.get_snapshot(snap.id).name == "legacy"
    assert read_manifest(catalog.policy.metadata_dir(profile) / "profile.json")["filesystem_identity"]


def test_modified_backup_is_not_silently_accepted(setup_sync):
    db, _, snapshots, _, _, _, profile, sync = setup_sync
    snap = snapshots.create(profile.id, "test.png")
    sync.scan()
    snapshots.snapshot_path(profile, snap).write_bytes(b"alterado")
    sync.scan()
    assert db.get_snapshot(snap.id).status == SnapshotStatus.CORRUPT
    assert read_manifest(snapshots.manifest_path(profile, snap))["files"][0]["size"] == len(b"original")


def test_new_manual_file_payloads_are_imported(setup_sync):
    db, catalog, snapshots, _, _, _, profile, sync = setup_sync
    namespace = catalog.policy.resolve(profile).namespace
    (namespace / "slot-a").write_bytes(b"A")
    (namespace / "slot-b.png").write_bytes(b"B")

    result = sync.scan()

    imported = {snap.name: snap for snap in db.list_snapshots(profile.id)}
    assert set(imported) == {"slot-a", "slot-b.png"}
    assert imported["slot-a"].relative_path == "slot-a"
    assert result.snapshots == {profile.id}
    assert all(snapshots.verify(profile, snap) for snap in imported.values())
    sync.scan()
    assert len(db.list_snapshots(profile.id)) == 2


def test_new_manual_file_payloads_with_and_without_extensions_are_imported(setup_sync):
    db, catalog, snapshots, _, _, _, profile, sync = setup_sync
    namespace = catalog.policy.resolve(profile).namespace
    (namespace / "slot").write_bytes(b"A")
    (namespace / "slot.txt").write_bytes(b"B")

    result = sync.scan()

    imported = {snap.name: snap for snap in db.list_snapshots(profile.id)}
    assert set(imported) == {"slot", "slot.txt"}
    assert imported["slot"].relative_path == "slot"
    assert result.snapshots == {profile.id}
    assert all(snapshots.verify(profile, item) for item in imported.values())


def test_existing_profile_folders_are_discovered(tmp_path):
    db, catalog, snapshots, _restore = make_services(tmp_path)
    source = tmp_path / "save.sav"
    source.write_bytes(b"current")
    backup = tmp_path / "backups"
    game = catalog.create_game("Teste2", GameSettings(SourceType.FILE, str(source), StorageMode.CUSTOM, str(backup)))
    namespace = backup / "Saverino - Teste2" / "Campanha"
    namespace.mkdir(parents=True)
    (namespace / "slot").write_bytes(b"saved")
    sync = ExternalSyncService(catalog, MaintenanceService(db, snapshots))

    result = sync.scan()

    profiles = catalog.db.list_profiles(game.id)
    assert [profile.name for profile in profiles] == ["Campanha"]
    assert profiles[0].id in result.profiles
    assert profiles[0].id in result.snapshots
    assert db.list_snapshots(profiles[0].id)[0].relative_path == "slot"


def test_existing_file_snapshot_keeps_any_extension(setup_sync):
    db, _, snapshots, _, _, _, profile, sync = setup_sync
    snap = snapshots.create(profile.id, "GitHub Desktop")
    payload = snapshots.snapshot_path(profile, snap)
    payload = payload.with_name("GitHub Desktop.lnk")
    snapshots.snapshot_path(profile, snap).rename(payload)

    assert sync.scan().snapshots == {profile.id}

    assert db.get_snapshot(snap.id).name == "GitHub Desktop.lnk"
    assert payload.exists()


def test_new_manual_directory_payloads_are_imported(tmp_path):
    db, catalog, snapshots, _restore = make_services(tmp_path)
    source = tmp_path / "SaveData"
    source.mkdir()
    game = catalog.create_game("Jogo")
    profile = catalog.create_profile(game.id, "Perfil", SourceType.DIRECTORY, str(source), StorageMode.APP, "")
    maintenance = MaintenanceService(db, snapshots)
    sync = ExternalSyncService(catalog, maintenance)
    namespace = catalog.policy.resolve(profile).namespace
    (namespace / "slot-a").mkdir()
    (namespace / "slot-a" / "save.dat").write_bytes(b"A")
    (namespace / "slot-b").mkdir()

    sync.scan()

    imported = {snap.name: snap for snap in db.list_snapshots(profile.id)}
    assert set(imported) == {"slot-a", "slot-b"}
    assert imported["slot-a"].file_count == 1
    assert all(snapshots.verify(profile, snap) for snap in imported.values())


def test_directory_snapshot_rename_does_not_rehash_payload(setup_sync, tmp_path, monkeypatch):
    db, catalog, snapshots, _, _, game, _, sync = setup_sync
    source = tmp_path / "SaveData"
    (source / "empty").mkdir(parents=True)
    (source / "slot.dat").write_bytes(b"original")
    profile = catalog.create_profile(game.id, "Pasta", SourceType.DIRECTORY, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "Antes")
    sync.scan()
    path = snapshots.snapshot_path(profile, snap)
    path.rename(path.with_name("Depois"))

    def unexpected_hash(*args, **kwargs):
        pytest.fail("Um rename reconhecido por identidade não deve reler o payload")

    monkeypatch.setattr("save_manager.services.synchronization.build_inventory", unexpected_hash)
    sync.scan()
    assert db.get_snapshot(snap.id).name == "Depois"
    assert (path.with_name("Depois") / "empty").is_dir()


def test_two_missing_legacy_backups_are_not_assigned_to_one_file(setup_sync, tmp_path):
    db, _, snapshots, _, _, _, profile, sync = setup_sync
    first = snapshots.create(profile.id, "A")
    second = snapshots.create(profile.id, "B")
    for snap in (first, second):
        path = snapshots.manifest_path(profile, snap)
        manifest = read_manifest(path)
        manifest.pop("filesystem_identity")
        write_manifest(path, manifest)
    snapshots.snapshot_path(profile, first).rename(tmp_path / "fora.png")
    path = snapshots.snapshot_path(profile, second)
    path.rename(path.with_name("ambíguo"))
    sync.scan()
    assert db.get_snapshot(first.id).status == SnapshotStatus.MISSING
    assert db.get_snapshot(second.id).status == SnapshotStatus.MISSING


@pytest.fixture
def window(qtbot, monkeypatch, setup_sync):
    _, catalog, _, _, _, _, _, _ = setup_sync
    root = catalog.policy.app_root
    monkeypatch.setattr(
        "save_manager.ui.main_window.WindowsStartupService.set_enabled",
        lambda _service, _enabled: True,
    )
    monkeypatch.setattr("save_manager.ui.main_window.initialize", lambda: {
        "root": root, "data": root / "data", "catalog": root / "data/catalog.sqlite3",
        "operations": root / "data/operations",
    })
    widget = MainWindow()

    def stop_monitor(window):
        window.monitor.stop()
        qtbot.waitUntil(lambda: not window.monitor.running)

    qtbot.addWidget(widget, before_close_func=stop_monitor)
    widget.show()
    qtbot.waitUntil(lambda: bool(widget.monitor.watcher.directories()) and not widget.monitor.running)
    return widget


def test_native_watcher_updates_renames_and_preserves_selection(qtbot, window, setup_sync):
    db, catalog, snapshots, _, source, game, _, _ = setup_sync
    profile = catalog.create_profile(game.id, "Segundo", SourceType.FILE, str(source), StorageMode.APP, "")
    snap = snapshots.create(profile.id, "test.png")
    snapshots.create(profile.id, "Outro")
    window._game_changed(window.games.currentRow())
    window.profiles.setCurrentIndex(window.profiles.findText("Segundo"))
    window.table.sortItems(1, Qt.SortOrder.DescendingOrder)
    row = next(row for row in range(window.table.rowCount()) if window.table.item(row, 1).text() == "test.png")
    window.table.setCurrentCell(row, 1)
    assert window._selected_snapshot().id == snap.id
    window.monitor.request()
    namespace = catalog.policy.resolve(profile).namespace
    qtbot.waitUntil(lambda: str(namespace) in window.monitor.watcher.directories() and not window.monitor.running)
    window.monitor.fallback.stop()  # O teste depende do evento nativo, não do timer.
    path = snapshots.snapshot_path(profile, snap)
    path.rename(path.with_name("test1"))
    renamed_profile = namespace.with_name("Perfil renomeado")
    namespace.rename(renamed_profile)
    qtbot.waitUntil(lambda: window.profiles.currentText() == "Perfil renomeado", timeout=5000)
    qtbot.waitUntil(lambda: window.table.item(window.table.currentRow(), 1).text() == "test1", timeout=5000)
    assert window.current_profile.id == profile.id
    assert window._selected_snapshot().id == snap.id
    assert "Perfil renomeado" in window.path_label.text()
    assert db.get_snapshot(snap.id).name == "test1"
    qtbot.waitUntil(lambda: not window.monitor.running)
    (renamed_profile / "test1").rename(renamed_profile / "test2")
    qtbot.waitUntil(lambda: window.table.item(window.table.currentRow(), 1).text() == "test2", timeout=5000)


def test_manual_import_plays_import_sound_once(qtbot, window, setup_sync, monkeypatch):
    _, catalog, _, _, _, _, profile, sync = setup_sync
    qtbot.waitUntil(lambda: not window.monitor.running)
    namespace = catalog.policy.resolve(profile).namespace
    namespace.mkdir(parents=True, exist_ok=True)
    (namespace / "manual").write_bytes(b"manual")
    played = []
    monkeypatch.setattr(window.audio, "play", played.append)

    result = sync.scan()
    assert result is not None and result.imported_snapshots
    window._external_changes_synced(result)
    assert played == ["import"]


def test_refresh_button_reconciles_disk_and_preserves_selection(qtbot, window, setup_sync):
    _db, catalog, snapshots, _, _source, game, profile, _sync = setup_sync
    kept = snapshots.create(profile.id, "keep.sav")
    removed = snapshots.create(profile.id, "removed.sav")
    window._game_changed(window.games.currentRow())
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    kept_row = next(row for row in range(window.table.rowCount()) if window.table.item(row, 1).text() == kept.name)
    window.table.setCurrentCell(kept_row, 1)
    snapshots.snapshot_path(profile, removed).unlink()
    namespace = catalog.policy.resolve(profile).namespace
    (namespace / "manual.png").write_bytes(b"manual")

    window.i18n.set_language("en-US")
    assert window.refresh_button.text() == "Refresh"
    window.refresh_button.click()
    qtbot.waitUntil(lambda: not window.operation_active, timeout=5000)

    names = [window.table.item(row, 1).text() for row in range(window.table.rowCount())]
    assert names == [kept.name, "manual.png"]
    assert window._selected_snapshot().id == kept.id
    window.i18n.set_language("pt-BR")
    assert window.refresh_button.text() == "Atualizar"
    window.profiles.setCurrentIndex(-1)
    assert not window.refresh_button.isEnabled()


def test_snapshot_table_uses_requested_columns_and_edits_description(qtbot, window, setup_sync):
    db, _, snapshots, _, _, _, profile, _ = setup_sync
    snap = snapshots.create(profile.id, "Estado A", "descrição antiga")
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    window._refresh_snapshots()

    assert window.table.columnCount() == 4
    assert [window.table.horizontalHeaderItem(index).text() for index in range(4)] == ["#", "Nome", "Descrição", "Criação"]
    assert not window.table.verticalHeader().isVisible()
    assert window.table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert not window.table.horizontalHeader().stretchLastSection()
    assert [window.table.horizontalHeader().sectionResizeMode(index) for index in range(4)] == [QHeaderView.ResizeMode.Interactive] * 4
    header = window.table.horizontalHeader()
    assert header.sectionPosition(3) + header.sectionSize(3) == window.table.viewport().width()
    assert not window.table.horizontalScrollBar().isVisible()
    assert header.sectionSize(1) < header.sectionSize(3) < header.sectionSize(2)
    assert header.sectionSize(3) < 160
    assert window.table.item(0, 3).text() == window._format_local_datetime(snap.created_at)
    assert window.table.item(0, 1).flags() & Qt.ItemFlag.ItemIsEditable
    assert window.table.item(0, 2).flags() & Qt.ItemFlag.ItemIsEditable
    assert isinstance(window.centralWidget(), QSplitter)
    splitter = window.centralWidget()
    left_panel = splitter.widget(0)
    initial_left_width = left_panel.width()
    splitter.moveSplitter(initial_left_width + 100, 1)
    qtbot.wait(20)
    assert left_panel.width() == initial_left_width
    assert splitter.handle(1).cursor().shape() == Qt.CursorShape.ArrowCursor
    assert splitter.sizes()[0] <= 240
    assert window.game_search.geometry().top() == window.profiles.geometry().top()
    assert window.games.geometry().bottom() == window.table.geometry().bottom()
    assert window.game_search.toolTip() == window.i18n.tr("main.search_games_tooltip")
    assert window.profiles.toolTip() == window.i18n.tr("main.profile_tooltip")
    assert window.snapshot_search.toolTip() == window.i18n.tr("main.search_saves_tooltip")
    assert window.create_button.toolTip() == window.i18n.tr("main.create_save_tooltip")
    assert window.table.toolTip() == window.i18n.tr("main.save_list_tooltip")

    def click_cell(column):
        rect = window.table.visualRect(window.table.model().index(0, column))
        QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())

    click_cell(1)
    assert {item.column() for item in window.table.selectedItems()} == {0, 1, 2, 3}
    click_cell(2)
    assert {item.column() for item in window.table.selectedItems()} == {0, 1, 2, 3}
    click_cell(3)
    assert {item.column() for item in window.table.selectedItems()} == {0, 1, 2, 3}
    click_cell(0)
    assert {item.column() for item in window.table.selectedItems()} == {0, 1, 2, 3}

    window.table.item(0, 1).setText("Estado renomeado")
    qtbot.wait(50)
    renamed = db.get_snapshot(snap.id)
    assert renamed.name == "Estado renomeado"
    assert snapshots.snapshot_path(profile, renamed).exists()

    window.table.item(0, 2).setText("descrição nova")
    qtbot.wait(50)
    assert db.get_snapshot(snap.id).notes == "descrição nova"

    header.resizeSection(2, 2000)
    qtbot.wait(50)
    assert sum(header.sectionSize(index) for index in range(4)) <= window.table.viewport().width()
    assert not window.table.horizontalScrollBar().isVisible()
    header.resizeSection(2, 140)
    qtbot.wait(50)
    assert sum(header.sectionSize(index) for index in range(4)) <= window.table.viewport().width()

    header.resizeSection(1, 400)
    assert window._snapshot_columns_user_resized
    window._profile_changed(window.profiles.currentIndex())
    assert not window._snapshot_columns_user_resized
    assert header.sectionSize(1) < 400

    row_height = window.table.rowHeight(0)
    window.table.editItem(window.table.item(0, 2))
    editor = window.table.findChild(QTextEdit)
    assert editor is not None
    editor.setPlainText("descrição muito longa " * 30)
    qtbot.wait(50)
    assert window.table.rowHeight(0) > row_height

    snapshots.create(profile.id, "Estado B")
    window._refresh_snapshots()
    assert [window.table.item(row, 1).text() for row in range(window.table.rowCount())] == ["Estado renomeado", "Estado B"]
    assert [window.table.item(row, 0).text() for row in range(window.table.rowCount())] == ["1", "2"]
    window.table.sortItems(3, Qt.SortOrder.AscendingOrder)
    assert [window.table.item(row, 0).text() for row in range(window.table.rowCount())] == ["1", "2"]


def test_single_click_name_or_description_selects_row_and_keeps_cell_editing(qtbot, window, setup_sync):
    _, _, snapshots, _, _, _, profile, _ = setup_sync
    snapshots.create(profile.id, "Estado A", "descrição")
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    window._refresh_snapshots()

    name_rect = window.table.visualRect(window.table.model().index(0, 1))
    QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=name_rect.center())
    assert len(window.table.selectedItems()) == window.table.columnCount()
    window.table.setCurrentCell(0, 2)
    QTest.keyClick(window.table.viewport(), Qt.Key.Key_F2)
    assert window.table.findChild(QTextEdit) is not None


def test_save_actions_warn_without_selected_snapshot(qtbot, window, setup_sync, monkeypatch):
    _, _, snapshots, _, _, _, profile, _ = setup_sync
    snapshots.create(profile.id, "Estado A")
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    window._refresh_snapshots()
    window.table.clearSelection()
    assert not window.overwrite_button.isEnabled()
    assert not window.restore_button.isEnabled()
    assert not window.trash_button.isEnabled()
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args))

    window._overwrite_snapshot()
    window._restore_snapshot()

    assert len(messages) == 2
    assert "sobrescrevê-lo" in messages[0][2]
    assert "carregá-lo" in messages[1][2]


def test_load_hotkey_can_skip_confirmation(qtbot, window, setup_sync, monkeypatch):
    _, _, snapshots, _, _, _, profile, _ = setup_sync
    snapshots.create(profile.id, "Estado A")
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    window._refresh_snapshots()
    window.table.setCurrentCell(0, 1)
    window._app_settings["load_save_hotkey_without_confirmation"] = True

    class FakeSignal:
        def __init__(self):
            self.callbacks = []

        def connect(self, _callback):
            self.callbacks.append(_callback)

    class FakeWorker:
        def __init__(self, *_args, **_kwargs):
            self.signals = type("Signals", (), {"finished": FakeSignal(), "error": FakeSignal()})()

    started = []
    questions = []
    sounds = []
    monkeypatch.setattr(window.audio, "play", sounds.append)
    monkeypatch.setattr("save_manager.ui.main_window.ServiceWorker", FakeWorker)
    monkeypatch.setattr(window.thread_pool, "start", started.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: questions.append(args))

    window._global_hotkey_triggered("load_save")

    assert len(started) == 1
    assert questions == []
    for callback in started[0].signals.finished.callbacks:
        callback(None)
    assert window.status_label.text() == 'Save "Estado A" carregado'
    assert window.version_label.text() == f"v{APP_VERSION}"
    assert sounds == ["load"]
    window.operation_active = False
    window.active_worker = None


def test_backup_folder_button_requires_profile_but_source_folder_does_not(qtbot, window, setup_sync, monkeypatch):
    _, _, _, _, source, _, profile, _ = setup_sync
    window._profile_changed(-1)

    assert not window.create_button.isEnabled()
    assert not window.open_backups_button.isEnabled()
    assert window.open_source_button.isEnabled()
    assert "QPushButton:pressed" in window.styleSheet()
    assert "QListWidget::item:selected { background: #3a3a3a" in window.styleSheet()

    opened = []
    monkeypatch.setattr("save_manager.ui.main_window.open_in_explorer", lambda path: opened.append(path))
    window._open_folder("source")
    assert opened == [source.parent]
    assert profile is not None


def test_delete_key_triggers_window_delete_action(qtbot, window, setup_sync, monkeypatch):
    _, _, snapshots, _, _, _, profile, _ = setup_sync
    snapshots.create(profile.id, "Estado A")
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    window._refresh_snapshots()
    window.table.setCurrentCell(0, 1)
    messages = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: messages.append(args) or QMessageBox.StandardButton.Cancel)

    QTest.keyClick(window.table.viewport(), Qt.Key.Key_Delete)
    qtbot.wait(50)

    assert len(messages) == 1
    assert "save selecionado" in messages[0][2]
    assert window.delete_shortcut.context() == Qt.ShortcutContext.WindowShortcut


def test_multiple_snapshot_selection_uses_rows_and_blocks_single_item_actions(qtbot, window, setup_sync, monkeypatch):
    _, _, snapshots, _, _, _, profile, _ = setup_sync
    snapshots.create(profile.id, "Estado A")
    snapshots.create(profile.id, "Estado B")
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    window._refresh_snapshots()
    assert window.create_button.isEnabled()

    first = window.table.visualRect(window.table.model().index(0, 1)).center()
    second = window.table.visualRect(window.table.model().index(1, 1)).center()
    QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=first)
    QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, second)

    selected_rows = {item.row() for item in window.table.selectedItems()}
    assert selected_rows == {0, 1}
    assert all(sum(item.row() == row for item in window.table.selectedItems()) == 4 for row in selected_rows)
    assert window.trash_button.text() == "Excluir (2)"

    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args))
    window._overwrite_snapshot()
    window._restore_snapshot()
    assert len(messages) == 2
    assert "somente um item" in messages[0][2]
    assert "somente um item" in messages[1][2]

    window.table.clearSelection()
    QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=first)
    QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, second)
    assert {item.row() for item in window.table.selectedItems()} == {0, 1}
    assert window.trash_button.text() == "Excluir (2)"

    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: questions.append(args) or QMessageBox.StandardButton.Cancel)
    window._trash_snapshot()
    assert "excluídos permanentemente" in questions[0][2]
    assert "não poderão ser recuperados" in questions[0][2]


def test_snapshot_table_selects_rows_by_mouse_drag(qtbot, window, setup_sync):
    _, _, snapshots, _, _, _, profile, _ = setup_sync
    for name in ("Estado A", "Estado B", "Estado C"):
        snapshots.create(profile.id, name)
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    window._refresh_snapshots()

    first = window.table.visualRect(window.table.model().index(0, 1)).center()
    last = window.table.visualRect(window.table.model().index(2, 1)).center()
    QTest.mousePress(window.table.viewport(), Qt.MouseButton.LeftButton, pos=first)
    QTest.mouseMove(window.table.viewport(), last)
    QTest.mouseRelease(window.table.viewport(), Qt.MouseButton.LeftButton, pos=last)

    selected_rows = {item.row() for item in window.table.selectedItems()}
    assert selected_rows == {0, 1, 2}
    assert all(sum(item.row() == row for item in window.table.selectedItems()) == 4 for row in selected_rows)


def test_context_menus_reflect_game_and_snapshot_actions(qtbot, window, setup_sync, monkeypatch):
    _, _, snapshots, _, _, game, profile, _ = setup_sync
    snapshots.create(profile.id, "Estado A")
    snapshots.create(profile.id, "Estado B")
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    window._refresh_snapshots()

    first = window.table.visualRect(window.table.model().index(0, 1)).center()
    second = window.table.visualRect(window.table.model().index(1, 1)).center()
    QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=first)
    QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, second)

    menus = []

    class FakeSignal:
        def connect(self, _callback):
            return None

    class FakeAction:
        def __init__(self, text):
            self._text, self._enabled, self.triggered = text, True, FakeSignal()

        def text(self):
            return self._text

        def setEnabled(self, enabled):
            self._enabled = enabled

        def isEnabled(self):
            return self._enabled

    class FakeMenu:
        def __init__(self, *_args):
            self._actions = []
            menus.append(self)

        def addAction(self, text):
            action = FakeAction(text)
            self._actions.append(action)
            return action

        def actions(self):
            return self._actions

        def exec(self, *_args):
            return None

    monkeypatch.setattr("save_manager.ui.main_window.QMenu", FakeMenu)
    window._show_snapshot_menu(second)
    actions = menus.pop().actions()
    assert [action.text() for action in actions] == [
        "Renomear", "Auto-Save", "Sobrescrever save", "Carregar save", "Excluir (2)",
    ]
    assert not actions[0].isEnabled()
    assert actions[1].isEnabled()
    assert not actions[2].isEnabled()
    assert not actions[3].isEnabled()
    assert actions[4].isEnabled()
    assert "QMenu::item:selected" in window.styleSheet()
    assert "QMenu::item:pressed" in window.styleSheet()

    window.table.clearSelection()
    QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=first)
    window._show_snapshot_menu(first)
    actions = menus.pop().actions()
    assert all(action.isEnabled() for action in actions)
    assert [action.text() for action in actions] == [
        "Renomear", "Auto-Save", "Sobrescrever save", "Carregar save", "Excluir",
    ]

    description = window.table.visualRect(window.table.model().index(0, 2)).center()
    window._show_snapshot_menu(description)
    actions = menus.pop().actions()
    assert actions[0].text() == "Editar descrição"

    game_item = window.games.findItems(game.name, Qt.MatchFlag.MatchExactly)[0]
    window._show_game_menu(window.games.visualItemRect(game_item).center())
    actions = menus.pop().actions()
    assert [action.text() for action in actions] == ["Configurações do projeto…", "Excluir projeto…"]


def test_main_menu_bar_has_file_options_and_about_actions(qtbot, window, monkeypatch):
    window.i18n.set_language("en-US")
    menus = window.menuBar().actions()
    assert [action.text() for action in menus] == ["File", "Options", "About"]

    file_actions = [action for action in menus[0].menu().actions() if not action.isSeparator()]
    options_actions = [action for action in menus[1].menu().actions() if not action.isSeparator()]
    about_actions = [action for action in menus[2].menu().actions() if not action.isSeparator()]
    assert [action.text() for action in file_actions] == ["Add Project", "Import/Export", "Exit"]
    assert [action.text() for action in options_actions] == ["Themes", "Settings"]
    assert [action.text() for action in about_actions] == ["About", "Check for Updates"]
    assert window.support_menu_item.text() == "Support"
    assert window.menuBar().cornerWidget(Qt.Corner.TopRightCorner) is window.support_menu_item
    assert "QMenuBar { background: #1e1e1e" in window.styleSheet()
    assert "QMenuBar::item:selected" in window.styleSheet()
    assert "QMenuBar::item:pressed" in window.styleSheet()

    opened_urls = []

    class DesktopServicesStub:
        # Capture opened URLs.
        @staticmethod
        def openUrl(url):
            opened_urls.append(url.toString())
            return True

    monkeypatch.setattr("save_manager.ui.main_window.QDesktopServices", DesktopServicesStub)
    window.support_menu_item.click()
    assert opened_urls == ["https://ko-fi.com/mmachida"]

    about = []

    class AboutStub:
        def __init__(self, *args, **kwargs):
            about.append((args, kwargs))

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr("save_manager.ui.main_window.AboutDialog", AboutStub)
    window._show_about()
    assert len(about) == 1
    assert about[0][1]["github_url"] == "https://github.com/mmachida/Saverino-Manager"


def test_theme_choice_applies_immediately_and_is_persisted(qtbot, window):
    window._apply_theme("hollow_knight")

    assert window._theme_id == "hollow_knight"
    assert window.db.get_setting("theme") == "hollow_knight"
    assert "QPushButton { background: #2f5f9e" in window.styleSheet()

    window._apply_theme("unknown")
    assert window._theme_id == "default"


def test_game_menu_elides_long_labels_and_keeps_full_name_in_tooltip(qtbot, window, setup_sync):
    _, catalog, _, _, _, _, _, _ = setup_sync
    long_name = ("Nome de jogo muito extenso " * 3).strip()
    catalog.create_game(long_name)
    window._refresh_games()

    item = next(item for item in (window.games.item(row) for row in range(window.games.count()))
                 if item.toolTip() == long_name)
    assert len(item.text()) <= MAX_GAME_MENU_NAME_LENGTH
    assert item.text().endswith("…")
    assert not window.games.wordWrap()
    assert window.games.textElideMode() == Qt.TextElideMode.ElideRight


def test_games_can_be_reordered_by_drag_and_order_is_persisted(qtbot, window, setup_sync):
    db, catalog, _, _, _, first_game, _, _ = setup_sync
    second_game = catalog.create_game("Segundo jogo")
    third_game = catalog.create_game("Terceiro jogo")
    window._refresh_games()

    assert window.games.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
    original_ids = [str(game.id) for game in window.visible_games]
    item = window.games.takeItem(window.games.count() - 1)
    window.games.insertItem(0, item)
    window.games.setCurrentRow(0)
    window._games_reordered()

    expected = [str(third_game.id), *original_ids[:-1]]
    assert db.get_setting("game_order") == expected
    window._refresh_games()
    assert [str(game.id) for game in window.visible_games] == expected
    assert str(first_game.id) in expected and str(second_game.id) in expected


def test_about_check_for_updates_starts_manual_query(qtbot, window, monkeypatch):
    started = []

    class SignalStub:
        def connect(self, _callback):
            pass

    class DialogStub:
        download_update_requested = SignalStub()
        cancel_update_requested = SignalStub()

        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr("save_manager.ui.main_window.UpdateDialog", DialogStub)
    monkeypatch.setattr(window, "_start_update_check", lambda dialog, *, automatic: started.append((dialog, automatic)))
    window._check_updates_from_about()

    assert len(started) == 1
    assert started[0][1] is False


def test_startup_update_check_runs_after_a_recent_manual_check(qtbot, window, monkeypatch):
    started = []
    monkeypatch.setattr(window, "_start_update_check", lambda dialog, *, automatic: started.append((dialog, automatic)))
    window._app_settings["automatically_check_for_updates"] = True
    window._app_settings["last_update_check_at"] = "2099-01-01T00:00:00+00:00"

    window._maybe_auto_check_updates()

    assert started == [(None, True)]


def test_settings_menu_persists_preferences(qtbot, window, setup_sync, monkeypatch):
    window.i18n.set_language("en-US")
    db = setup_sync[0]

    class FakeDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return {
                "global_hotkeys_enabled": True,
                "create_save_hotkey": "Ctrl+Alt+S",
                "load_save_hotkey": "Ctrl+Alt+L",
                "load_save_hotkey_without_confirmation": False,
                "initialize_with_windows": True,
                "always_on_top": False,
                "sound_effect_import_enabled": True,
                "sound_effect_load_enabled": True,
                    "sound_effect_auto_save_enabled": False,
                "sound_effect_volume": 50,
                "automatically_check_for_updates": True,
                "last_update_check_at": "",
                "language": "en-US",
            }

    monkeypatch.setattr("save_manager.ui.main_window.SettingsDialog", FakeDialog)
    monkeypatch.setattr(window.global_hotkeys, "apply", lambda _settings: [])
    registered = []
    monkeypatch.setattr(window.startup_service, "set_enabled", lambda enabled: registered.append(enabled) or True)
    window._show_settings()

    assert db.get_setting("app_settings") == {
        "global_hotkeys_enabled": True,
        "create_save_hotkey": "Ctrl+Alt+S",
        "load_save_hotkey": "Ctrl+Alt+L",
        "load_save_hotkey_without_confirmation": False,
        "initialize_with_windows": True,
        "always_on_top": False,
        "sound_effect_import_enabled": True,
        "sound_effect_load_enabled": True,
        "sound_effect_auto_save_enabled": False,
        "sound_effect_volume": 50,
        "automatically_check_for_updates": True,
        "last_update_check_at": "",
        "language": "en-US",
    }
    assert registered == [True]


def test_always_on_top_toggle_rebuilds_window_safely(qtbot, window, monkeypatch):
    applied = []
    monkeypatch.setattr(window.global_hotkeys, "apply", lambda settings: applied.append(dict(settings)) or [])
    flag = Qt.WindowType.WindowStaysOnTopHint

    settings = dict(window._app_settings, always_on_top=True)
    window._apply_application_settings(settings)
    qtbot.waitUntil(lambda: bool(window.windowFlags() & flag))
    qtbot.waitUntil(lambda: bool(applied) and applied[-1]["always_on_top"] is True)

    settings["always_on_top"] = False
    window._apply_application_settings(settings)
    qtbot.waitUntil(lambda: not bool(window.windowFlags() & flag))
    qtbot.waitUntil(lambda: bool(applied) and applied[-1]["always_on_top"] is False)


def test_global_create_hotkey_uses_next_line_name_and_skips_collision(qtbot, window, setup_sync, monkeypatch):
    _, _, snapshots, _, _, _, profile, _ = setup_sync
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    assert window._next_hotkey_snapshot_name() == "save_01"
    snapshots.create(profile.id, "Estado A")
    assert window._next_hotkey_snapshot_name() == "save_02"
    snapshots.create(profile.id, "save_03")
    assert window._next_hotkey_snapshot_name() == "save_04"

    names = []
    monkeypatch.setattr(window, "_start_snapshot_creation", names.append)
    window._create_snapshot_automatic()
    assert names == ["save_04"]
    names.clear()
    monkeypatch.setattr("save_manager.ui.main_window.QInputDialog.getText", lambda *_args, **_kwargs: pytest.fail("não deve abrir prompt"))
    window._create_snapshot()
    assert names == ["save_04"]


def test_snapshot_refresh_can_scroll_to_last_row(qtbot, window, setup_sync):
    _, _, snapshots, _, _, _, profile, _ = setup_sync
    for number in range(40):
        snapshots.create(profile.id, f"Estado {number:02d}")
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    window._refresh_snapshots()
    assert window.table.rowCount() == 40
    qtbot.wait(50)
    scrollbar = window.table.verticalScrollBar()
    assert scrollbar.maximum() > 0

    scrollbar.setValue(0)
    window._refresh_snapshots(scroll_to_end=True)
    qtbot.waitUntil(lambda: scrollbar.value() == scrollbar.maximum())


def test_switching_profiles_refits_columns_after_vertical_scrollbar_change(qtbot, window, setup_sync):
    _, catalog, snapshots, _, source, game, profile, _ = setup_sync
    crowded = catalog.create_profile(game.id, "Muitos", SourceType.FILE, str(source), StorageMode.APP, "")
    for number in range(40):
        snapshots.create(crowded.id, f"Estado {number:02d}")
    window._game_changed(window.games.currentRow())

    window.profiles.setCurrentIndex(window.profiles.findText(crowded.name))
    window._refresh_snapshots()
    qtbot.waitUntil(lambda: window.table.verticalScrollBar().maximum() > 0)

    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    qtbot.waitUntil(lambda: window.table.verticalScrollBar().maximum() == 0)
    qtbot.wait(50)
    header = window.table.horizontalHeader()
    assert header.sectionPosition(3) + header.sectionSize(3) == window.table.viewport().width()
    assert not window.table.horizontalScrollBar().isVisible()


def test_global_hotkey_ignores_messages_while_action_is_active(qtbot, window, monkeypatch):
    calls = []

    def create_once():
        calls.append(True)
        window.operation_active = True

    monkeypatch.setattr(window, "_create_snapshot_automatic", create_once)
    window._global_hotkey_triggered("create_save")
    window._global_hotkey_triggered("create_save")

    assert calls == [True]
    window.operation_active = False


def test_snapshot_table_middle_column_drag_remains_interactive(qtbot, window, setup_sync):
    _, _, snapshots, _, _, _, profile, _ = setup_sync
    snapshots.create(profile.id, "Estado A", "descrição")
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    window._refresh_snapshots()

    header = window.table.horizontalHeader()
    before = header.sectionSize(2)
    divider_x = header.sectionPosition(2) + before - 1
    divider = QPoint(divider_x, max(1, header.height() // 2))
    target = QPoint(divider_x - 80, divider.y())
    qtbot.mouseMove(header.viewport(), divider)
    qtbot.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=divider)
    qtbot.mouseMove(header.viewport(), target)
    qtbot.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=target)
    qtbot.wait(50)

    assert header.sectionSize(2) != before
    assert header.sectionPosition(3) + header.sectionSize(3) <= window.table.viewport().width()


def test_new_profile_is_selected_and_profile_dialog_has_no_notes(qtbot, window, setup_sync, monkeypatch):
    _, catalog, _, _, source, game, profile, _ = setup_sync
    catalog.db.set_game_settings(game.id, GameSettings(SourceType.FILE, str(source), StorageMode.APP))
    window._game_changed(window.games.currentRow())

    def accept_profile(dialog):
        dialog.findChild(QLineEdit).setText("Novo perfil")
        assert dialog.width() >= 520
        assert "Nota" not in dialog.windowTitle()
        assert not any("nota" in label.text().casefold() for label in dialog.findChildren(QLabel))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept_profile)
    window._add_profile()

    assert window.profiles.currentText() == "Novo perfil"
    assert window.current_profile.name == "Novo perfil"


def test_profile_delete_button_removes_profile_and_backups_after_confirmation(qtbot, window, setup_sync, monkeypatch):
    _, catalog, snapshots, _, _, game, profile, _ = setup_sync
    window.profiles.setCurrentIndex(window.profiles.findText(profile.name))
    assert window.game_title.text() == game.name
    assert window.delete_profile_button.objectName() == "destructiveButton"

    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: questions.append(args) or QMessageBox.StandardButton.Yes)
    snapshots.create(profile.id, "Estado")
    namespace = catalog.policy.resolve(profile).namespace
    assert any(namespace.iterdir())
    window.monitor.watcher.addPath(str(namespace))
    assert str(namespace) in window.monitor.watcher.directories()
    window.delete_profile_button.click()

    assert len(questions) == 1
    assert profile.name in questions[0][2]
    assert catalog.db.get_profile(profile.id) is None
    assert catalog.db.list_profiles(game.id) == []
    assert not namespace.exists()
    assert str(namespace) not in window.monitor.watcher.directories()
    assert not catalog.policy.profile_data_dir(profile).exists()
    assert window.current_profile is None
    assert window.profiles.count() == 0
    assert not window.delete_profile_button.isEnabled()


def test_game_delete_requires_delete_confirmation_text(qtbot, window, setup_sync, monkeypatch):
    _, catalog, _, _, _, game, _, _ = setup_sync
    profile_namespaces = [catalog.policy.resolve(profile).namespace for profile in catalog.db.list_profiles(game.id, True)]
    for namespace in profile_namespaces:
        window.monitor.watcher.addPath(str(namespace))
    entered = []

    def accept_wrong(dialog):
        entered.append(dialog.findChild(QLineEdit).placeholderText())
        dialog.findChild(QLineEdit).setText("DELETE")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept_wrong)

    window._delete_game()

    assert catalog.db.get_game(game.id) is not None
    assert entered == ["delete"]

    monkeypatch.setattr(QDialog, "exec", lambda dialog: dialog.findChild(QLineEdit).setText("delete") or QDialog.DialogCode.Accepted)
    window._delete_game()
    assert catalog.db.get_game(game.id) is None
    assert all(str(namespace) not in window.monitor.watcher.directories() for namespace in profile_namespaces)


def test_profile_popup_handles_immediate_item_click(qtbot, window, setup_sync):
    _, catalog, _, _, source, game, _, _ = setup_sync
    second = catalog.create_profile(game.id, "Segundo", SourceType.FILE, str(source), StorageMode.APP, "")
    window._game_changed(window.games.currentRow())

    QTest.mouseClick(window.profiles, Qt.MouseButton.LeftButton, pos=window.profiles.rect().center())
    view = window.profiles.view()
    index = window.profiles.model().index(window.profiles.findText(second.name), 0)
    target = view.visualRect(index).center()
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=target)

    assert window.profiles.currentText() == second.name
    assert window.current_profile.id == second.id
    assert not view.window().isVisible()


def test_changes_during_operation_are_applied_afterwards(qtbot, window, setup_sync):
    _, catalog, _, _, _, _, profile, _ = setup_sync
    window.operation_active = True
    path = catalog.policy.resolve(profile).namespace
    path.rename(path.with_name("Depois da operação"))
    qtbot.wait(250)
    assert window.profiles.currentText() == "Perfil"
    window._operation_finished("Pronto")
    qtbot.waitUntil(lambda: window.profiles.currentText() == "Depois da operação")


def test_watcher_shows_missing_and_reappearing_backups(qtbot, window, setup_sync, tmp_path):
    db, _, snapshots, _, _, _, profile, _ = setup_sync
    snap = snapshots.create(profile.id, "test.png")
    window.monitor.request()
    path = snapshots.snapshot_path(profile, snap)
    qtbot.waitUntil(lambda: str(path) in window.monitor.watcher.files() and not window.monitor.running)
    # Criação interna atualiza a tabela no callback de conclusão da operação.
    window._refresh_snapshots()
    assert window.table.item(0, 1).toolTip() == ""
    outside = tmp_path / "fora.png"
    path.rename(outside)
    qtbot.waitUntil(lambda: db.get_snapshot(snap.id).status == SnapshotStatus.MISSING)
    qtbot.waitUntil(lambda: not window.monitor.running)
    outside.rename(path)
    qtbot.waitUntil(lambda: db.get_snapshot(snap.id).status == SnapshotStatus.READY)
    qtbot.waitUntil(lambda: not window.monitor.running)
    assert window.table.item(0, 1).toolTip() == ""


def test_slow_scan_does_not_block_qt_event_loop(qtbot, setup_sync):
    *_, sync = setup_sync
    entered, release = threading.Event(), threading.Event()
    original = sync.scan

    def slow_scan():
        entered.set()
        assert release.wait(5)
        return original()

    sync.scan = slow_scan
    monitor = FilesystemMonitor(sync, lambda: False)
    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    try:
        qtbot.waitUntil(entered.is_set)
        timer.start()
        qtbot.waitUntil(lambda: len(ticks) >= 3)
        assert monitor.running
    finally:
        timer.stop()
        monitor.stop()
        release.set()
        qtbot.waitUntil(lambda: not monitor.running)


def test_closing_window_waits_for_running_sync(qtbot, window):
    entered, release = threading.Event(), threading.Event()
    original = window.monitor.service.scan

    def slow_scan():
        entered.set()
        assert release.wait(5)
        return original()

    window.monitor.service.scan = slow_scan
    window.monitor.request()
    try:
        qtbot.waitUntil(entered.is_set)
        window.close()
        assert window.isVisible()
        assert window.monitor.stopped
    finally:
        release.set()
        qtbot.waitUntil(lambda: not window.isVisible())
        assert not window.monitor.running

