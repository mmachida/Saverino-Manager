from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID

from ..bootstrap import APP_ROOT, initialize
from ..bootstrap_lock import InstanceLock
from ..domain.models import GameSettings, Profile, SnapshotStatus, SourceType, StorageMode
from ..filesystem.explorer import open_in_explorer
from ..filesystem.policy import PathPolicy
from ..i18n import LanguageManager
from ..services.catalog import CatalogService
from ..services.auto_save import AutoSaveService
from ..services.external_sync import ExternalSyncService
from ..services.maintenance import MaintenanceService
from ..services.restore import RestoreService
from ..services.snapshot import SnapshotService
from ..services.windows_startup import WindowsStartupService
from ..storage.db import CatalogDB
from ..storage.journal import JournalStore
from ..updates.config import UpdateConfig
from ..updates.installer import check_install_prerequisites, default_install_root
from ..updates.service import PreparedUpdate, ReleaseInfo, UpdateService
from ..version import APP_VERSION
from .about_dialog import AboutDialog
from .auto_save_dialog import AutoSaveIntervalDialog, ProjectAutoSaveDialog
from .audio import AudioManager
from .dialogs import ImportExportDialog, ask_question, equalize_button_widths
from .filesystem_monitor import FilesystemMonitor
from .game_settings_dialog import GameSettingsDialog
from .profile_settings_dialog import ProfileSettingsDialog
from .global_hotkeys import GlobalHotkeyManager
from .settings_dialog import DEFAULT_APP_SETTINGS, SettingsDialog
from .theme_dialog import ThemeDialog
from .themes import INITIAL_THEME, THEMES, get_theme, normalize_theme, theme_stylesheet
from .update_dialog import UpdateDialog
from .workers import ServiceWorker

try:
    from PySide6.QtCore import QEvent, QItemSelectionModel, QRect, Qt, QThreadPool, QTimer, QUrl
    from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QKeySequence, QPainter, QPalette, QShortcut, QTextOption
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QFrame,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QListWidget,
        QMainWindow,
        QMenu,
        QMessageBox,
        QProgressBar,
        QProgressDialog,
        QPushButton,
        QSplitter,
        QStyle,
        QStyledItemDelegate,
        QStyleOptionViewItem,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )

    class SnapshotTableWidget(QTableWidget):
        """Tabela que mantém a coluna # como numeração da posição visível."""

        # Initialize the object.
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pending_row_selection = None
            self._double_click_editing = False
            self._drag_selection_anchor = None
            self._drag_selection_active = False

        # Handle mouse press.
        def mousePressEvent(self, event):
            index = self.indexAt(event.position().toPoint())
            modifiers = event.modifiers()
            if index.isValid() and event.button() == Qt.MouseButton.LeftButton and modifiers & Qt.KeyboardModifier.AltModifier:
                self._drag_selection_anchor = None
                self._drag_selection_active = False
                # O Qt não usa Alt como modificador de seleção. Tratá-lo como
                # alternância permite selecionar linhas com Alt+clique, além
                # do Ctrl/Shift padrão do Windows.
                row_items = [self.item(index.row(), column) for column in range(self.columnCount())]
                row_is_selected = all(item is not None and item.isSelected() for item in row_items)
                if index.column() == 0 or row_is_selected:
                    for item in row_items:
                        if item is not None:
                            item.setSelected(not row_is_selected)
                else:
                    item = self.item(index.row(), index.column())
                    if item is not None:
                        item.setSelected(not item.isSelected())
                self.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
                event.accept()
                return
            multi = event.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)
            self._pending_row_selection = (
                index.row()
                if (index.isValid() and index.column() in (0, 1, 2, 3)
                    and event.button() == Qt.MouseButton.LeftButton and not multi)
                else None
            )
            self._drag_selection_anchor = self._pending_row_selection
            self._drag_selection_active = False
            super().mousePressEvent(event)

        # Extend row selection while dragging.
        def mouseMoveEvent(self, event):
            super().mouseMoveEvent(event)
            anchor = self._drag_selection_anchor
            if anchor is None:
                return
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and index.row() != anchor:
                self._drag_selection_active = True
                self._select_full_row_range(anchor, index.row())

        # Select complete rows in a range.
        def _select_full_row_range(self, first_row, last_row):
            self.clearSelection()
            for row in range(min(first_row, last_row), max(first_row, last_row) + 1):
                for column in range(self.columnCount()):
                    item = self.item(row, column)
                    if item is not None:
                        item.setSelected(True)

        # Handle mouse release.
        def mouseReleaseEvent(self, event):
            super().mouseReleaseEvent(event)
            row = self._pending_row_selection
            anchor = self._drag_selection_anchor
            dragged = self._drag_selection_active
            index = self.indexAt(event.position().toPoint())
            last_row = index.row() if index.isValid() else anchor
            self._pending_row_selection = None
            self._drag_selection_anchor = None
            self._drag_selection_active = False
            double_click_editing = self._double_click_editing
            self._double_click_editing = False
            if row is None or event.button() != Qt.MouseButton.LeftButton or double_click_editing:
                return
            if dragged and last_row is not None:
                self._select_full_row_range(anchor, last_row)
                return
            # Aplicar depois do release evita que o processamento nativo da
            # tabela reduza a seleção novamente à célula clicada quando a
            # linha já estava selecionada.
            self.clearSelection()
            for column in range(self.columnCount()):
                item = self.item(row, column)
                if item is not None:
                    item.setSelected(True)

        # Handle double click.
        def mouseDoubleClickEvent(self, event):
            index = self.indexAt(event.position().toPoint())
            self._double_click_editing = (
                event.button() == Qt.MouseButton.LeftButton
                and index.isValid()
                and index.column() in (1, 2)
            )
            super().mouseDoubleClickEvent(event)

        # Handle sortItems.
        def sortItems(self, column, order=Qt.SortOrder.AscendingOrder):
            if column == 0:
                return
            super().sortItems(column, order)
            self.renumber_rows()

        # Handle renumber rows.
        def renumber_rows(self):
            for row in range(self.rowCount()):
                item = self.item(row, 0)
                if item:
                    item.setText(str(row + 1))

    class ProfileComboBox(QComboBox):
        """Combo whose popup is laid out before the next mouse click."""

        # Initialize the object.
        def __init__(self, parent=None):
            super().__init__(parent)
            self.view().viewport().installEventFilter(self)

        # Process widget events.
        def eventFilter(self, watched, event):
            if (watched is self.view().viewport()
                    and event.type() == QEvent.Type.MouseButtonRelease
                    and event.button() == Qt.MouseButton.LeftButton):
                index = self.view().indexAt(event.position().toPoint())
                if index.isValid():
                    self.setCurrentIndex(index.row())
                    self.hidePopup()
                    return True
            return super().eventFilter(watched, event)

        # Handle showPopup.
        def showPopup(self):
            super().showPopup()
            popup = self.view().window()
            popup.activateWindow()
            self.view().setFocus(Qt.FocusReason.PopupFocusReason)

    class ProjectListDelegate(QStyledItemDelegate):
        """Paints the Auto-Save dot without changing row metrics."""

        # Paint project label and indicator.
        def paint(self, painter, option, index):
            if not index.data(Qt.ItemDataRole.UserRole + 1):
                super().paint(painter, option, index)
                return

            style_option = QStyleOptionViewItem(option)
            self.initStyleOption(style_option, index)
            style = option.widget.style() if option.widget else QApplication.style()
            text_rect = style.subElementRect(
                QStyle.SubElement.SE_ItemViewItemText,
                style_option,
                option.widget,
            )

            label = style_option.text
            style_option.text = ""
            style.drawControl(
                QStyle.ControlElement.CE_ItemViewItem,
                style_option,
                painter,
                option.widget,
            )

            painter.save()
            painter.setClipRect(option.rect)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            dot_size = 10
            dot_rect = QRect(
                text_rect.left(),
                text_rect.center().y() - dot_size // 2,
                dot_size,
                dot_size,
            )
            painter.setPen(QColor("#1d6b3d"))
            painter.setBrush(QColor("#39c76a"))
            painter.drawEllipse(dot_rect)

            text_rect.setLeft(text_rect.left() + 16)
            selected_text_color = index.data(Qt.ItemDataRole.UserRole + 2)
            if (style_option.state & QStyle.StateFlag.State_Selected) and selected_text_color:
                painter.setPen(QColor(str(selected_text_color)))
            else:
                painter.setPen(style_option.palette.color(QPalette.ColorRole.Text))
            painter.setFont(style_option.font)
            label = QFontMetrics(style_option.font).elidedText(
                label,
                style_option.textElideMode,
                max(0, text_rect.width()),
            )
            painter.drawText(
                text_rect,
                style_option.displayAlignment,
                label,
            )
            painter.restore()

    class DescriptionDelegate(QStyledItemDelegate):
        """Editor multilinha que acompanha o crescimento da descrição."""

        # Handle createEditor.
        def createEditor(self, parent, option, index):
            if index.column() != 2:
                return super().createEditor(parent, option, index)
            editor = QTextEdit(parent)
            editor.setAcceptRichText(False)
            editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            editor.setFrameStyle(QFrame.Shape.NoFrame)
            editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            editor.setTabChangesFocus(True)
            editor.document().setDocumentMargin(4)
            editor.textChanged.connect(lambda: self._resize_editor(editor, index))
            return editor

        # Handle setEditorData.
        def setEditorData(self, editor, index):
            if isinstance(editor, QTextEdit):
                editor.setPlainText(index.data(Qt.ItemDataRole.DisplayRole) or "")
                return
            super().setEditorData(editor, index)

        # Style active Auto-Save names.
        def initStyleOption(self, option, index):
            super().initStyleOption(option, index)
            auto_save_color = index.data(Qt.ItemDataRole.UserRole + 1)
            if auto_save_color:
                color = QColor(str(auto_save_color))
                option.palette.setColor(QPalette.ColorRole.Text, color)
                option.palette.setColor(QPalette.ColorRole.HighlightedText, color)

        # Paint Auto-Save names clearly.
        def paint(self, painter, option, index):
            auto_save_color = index.data(Qt.ItemDataRole.UserRole + 1)
            if not auto_save_color:
                super().paint(painter, option, index)
                return
            style_option = QStyleOptionViewItem(option)
            self.initStyleOption(style_option, index)
            text = style_option.text
            style_option.text = ""
            style = option.widget.style() if option.widget else QApplication.style()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, style_option, painter, option.widget)
            painter.save()
            painter.setClipRect(option.rect)
            painter.setPen(QColor(str(auto_save_color)))
            painter.setFont(option.font)
            text_rect = option.rect.adjusted(4, 0, -4, 0)
            painter.drawText(
                text_rect,
                style_option.displayAlignment | Qt.TextFlag.TextWordWrap,
                text,
            )
            painter.restore()

        # Handle setModelData.
        def setModelData(self, editor, model, index):
            if isinstance(editor, QTextEdit):
                model.setData(index, editor.toPlainText(), Qt.ItemDataRole.EditRole)
                return
            super().setModelData(editor, model, index)

        # Handle updateEditorGeometry.
        def updateEditorGeometry(self, editor, option, index):
            editor.setGeometry(option.rect.adjusted(2, 2, -2, -2))
            if isinstance(editor, QTextEdit):
                self._resize_editor(editor, index)

        # Handle resize editor.
        def _resize_editor(self, editor, index):
            view = self.parent()
            if not isinstance(editor, QTextEdit) or not hasattr(view, "setRowHeight"):
                return
            if editor.property("_resizing_description"):
                return
            editor.setProperty("_resizing_description", True)
            try:
                editor.document().setTextWidth(max(1, editor.width() - 10))
                height = max(30, int(editor.document().size().height()) + 12)
                view.setRowHeight(index.row(), height)
                editor.setFixedHeight(height - 4)
                view.updateEditorGeometries()
            finally:
                editor.setProperty("_resizing_description", False)
except ImportError:  # permite importar serviços sem Qt instalado
    QMainWindow = object  # type: ignore[misc,assignment]


MAX_GAME_MENU_NAME_LENGTH = 50


class MainWindow(QMainWindow):
    # Initialize the object.
    def __init__(self):
        super().__init__()
        paths = initialize()
        self.instance_lock = InstanceLock(paths["data"] / "instance.lock")
        if not self.instance_lock.acquire():
            raise RuntimeError("O catálogo já está aberto em outra instância")
        self.db = CatalogDB(paths["catalog"])
        self.policy = PathPolicy(APP_ROOT, paths["data"])
        self.startup_service = WindowsStartupService()
        self.journals = JournalStore(paths["operations"])
        self.catalog = CatalogService(self.db, self.policy)
        self.auto_saves = AutoSaveService(self.db)
        self._auto_save_global_enabled = bool(self.db.get_setting("auto_save_global_enabled", True))
        settings_recovery_error = None
        try:
            self.catalog.game_settings.recover_pending()
        except Exception as exc:
            settings_recovery_error = str(exc)
            logging.getLogger("save_manager").exception("Configuração do jogo aguarda recuperação")
        self.snapshots = SnapshotService(self.db, self.policy, self.journals)
        self.restore_service = RestoreService(self.db, self.policy, self.journals, self.snapshots)
        self.maintenance = MaintenanceService(self.db, self.snapshots)
        try:
            self.catalog.migrate_legacy_storage()
        except Exception as exc:
            logging.getLogger("save_manager").warning("Migração de armazenamento legado pendente: %s", exc)
        self.snapshots.reconcile_pending_publications()
        self.current_game = None
        self.current_profile = None
        self.operation_active = False
        self.active_worker = None
        self._manual_refresh_active = False
        self._manual_refresh_pending = False
        self.thread_pool = QThreadPool.globalInstance()
        self._app_settings = self._load_app_settings()
        self.i18n = LanguageManager(self._app_settings.get("language"), self)
        self.i18n.language_changed.connect(self._language_changed)
        self.setWindowTitle(self.i18n.tr("app.title"))
        self.resize(960, 540)
        self.setMinimumSize(800, 450)
        # The native combo popup can animate into place.  Disabling that
        # effect makes the first click reliable instead of racing the popup.
        QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo, False)
        self.setStyleSheet("""
            QWidget { background: #202124; color: #e8eaed; font-size: 10pt; }
            QLineEdit, QTextEdit, QComboBox, QTableWidget, QListWidget { background: #2b2d31; border: 1px solid #4b4f56; padding: 4px; }
            QComboBox:focus { border: 1px solid #4b4f56; outline: none; }
            QComboBox QAbstractItemView { background: #2b2d31; color: #e8eaed; border: 1px solid #4b4f56; outline: none; }
            QComboBox QAbstractItemView::item { background: #2b2d31; color: #e8eaed; border: 0; padding: 4px; }
            QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:selected:active { background: #34373d; color: #e8eaed; border: 0; outline: none; }
            QComboBox QAbstractItemView::item:pressed { background: #2f343a; color: #e8eaed; border: 0; outline: none; }
            QComboBox QAbstractItemView::item:focus { border: 0; outline: none; }
            QTableWidget { gridline-color: #3b3e45; alternate-background-color: #2b2d31; selection-background-color: #34373d; selection-color: #e8eaed; }
            QTableWidget::item { background: #2b2d31; border: 0; padding: 4px; }
            QTableWidget::item:hover { background: #34373d; }
            QTableWidget::item:selected, QAbstractItemView::item:selected { background: #34373d; color: #e8eaed; border: 0; }
            QTableWidget::item:focus, QAbstractItemView::item:focus { border: 0; outline: none; }
            QTableWidget:focus, QListWidget:focus { border: 1px solid #4b4f56; outline: none; }
            QTableWidget QHeaderView, QTableWidget QHeaderView::section, QTableCornerButton::section { background: #2b2d31; color: #e8eaed; border: 0; border-bottom: 1px solid #4b4f56; padding: 5px; }
            QListWidget::item { background: #2b2d31; color: #e8eaed; border: 0; padding: 4px 6px; margin: 0; }
            QListWidget::item:hover { background: #34373d; }
            QListWidget::item:selected { background: #3c6eaa; color: #ffffff; }
            QListWidget::item:selected:hover { background: #5287c6; }
            QListWidget::item:pressed { background: #2f5d91; }
            QScrollBar:vertical { background: #202124; width: 12px; margin: 0; border: 1px solid #3b3e45; }
            QScrollBar:horizontal { background: #202124; height: 12px; margin: 0; border: 1px solid #3b3e45; }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #7a7f89; border-radius: 4px; margin: 2px; }
            QScrollBar::handle:vertical { min-height: 24px; }
            QScrollBar::handle:horizontal { min-width: 24px; }
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #aeb4bf; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal,
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { background: #202124; border: 0; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 12px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 12px; }
            QScrollBar::corner { background: #202124; }
            QSlider::groove:horizontal { background: #34373d; height: 6px; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #3c6eaa; border-radius: 3px; }
            QSlider::add-page:horizontal { background: #34373d; border-radius: 3px; }
            QSlider::handle:horizontal { background: #7a7f89; width: 14px; margin: -4px 0; border-radius: 7px; }
            QSlider::handle:horizontal:hover { background: #aeb4bf; }
            QSlider::handle:horizontal:pressed { background: #5287c6; }
            QPushButton { background: #3c6eaa; border: 0; border-radius: 3px; padding: 6px 10px; }
            QPushButton:hover { background: #5287c6; }
            QPushButton:pressed { background: #2f5d91; }
            QPushButton:disabled { background: #303238; color: #777c86; }
            QMenuBar { background: #202124; color: #e8eaed; border-bottom: 1px solid #4b4f56; padding: 2px; }
            QMenuBar::item { background: transparent; color: #e8eaed; padding: 5px 10px; }
            QMenuBar::item:selected { background: #34373d; color: #e8eaed; }
            QMenuBar::item:pressed { background: #2f343a; color: #e8eaed; }
            QMenuBar::item:disabled { background: transparent; color: #777c86; }
            QMenu { background: #2b2d31; color: #e8eaed; border: 1px solid #4b4f56; padding: 4px; }
            QMenu::item { background: transparent; color: #e8eaed; padding: 6px 22px 6px 10px; }
            QMenu::item:selected { background: #34373d; color: #e8eaed; }
            QMenu::item:pressed { background: #2f343a; color: #e8eaed; }
            QMenu::item:disabled { background: transparent; color: #777c86; }
        """)
        self._build_menu_bar()
        self.update_service = UpdateService(UpdateConfig(), APP_VERSION)
        self._update_worker = None
        self._update_release: ReleaseInfo | None = None
        self._prepared_update: PreparedUpdate | None = None
        self._update_download_dir: Path | None = None
        self._settings_dialog = None
        self._update_dialog = None
        self._auto_update_check_scheduled = False
        self._update_install_pending = False
        self._theme_id = normalize_theme(self.db.get_setting("theme") or INITIAL_THEME)
        self.audio = AudioManager(self)
        saved_game = self.db.get_setting("selected_game")
        self._build_ui(); self._apply_theme(self._theme_id); self._refresh_games(preserve_game_id=UUID(saved_game) if saved_game else None)
        pending = self.journals.pending()
        self.recovery_required = bool(pending) or settings_recovery_error is not None
        if settings_recovery_error:
            QMessageBox.warning(self, self.i18n.tr("dialog.recovery_title"), settings_recovery_error)
        if pending:
            QMessageBox.warning(self, self.i18n.tr("dialog.recovery_title"), self.i18n.tr("dialog.recovery_message", count=len(pending)))
        self.monitor = FilesystemMonitor(
            ExternalSyncService(self.catalog, self.maintenance),
            lambda: self.operation_active or self.recovery_required or QApplication.activeModalWidget() is not None,
            self,
        )
        self.monitor.synchronized.connect(self._external_changes_synced)
        self._auto_save_deadlines = {}
        self.auto_save_timer = QTimer(self)
        self.auto_save_timer.setInterval(1000)
        self.auto_save_timer.timeout.connect(self._poll_auto_saves)
        self._reset_auto_save_schedule()
        self.global_hotkeys = GlobalHotkeyManager(self._global_hotkey_triggered, self)
        self._pending_hotkey_settings = None
        self._pending_hotkey_notify = False
        self._apply_application_settings(self._app_settings)

    # Handle window close.
    def closeEvent(self, event):
        if self.operation_active:
            if self.active_worker is not None and getattr(self.active_worker, "cancellable", False):
                self.active_worker.cancel()
                self.status_label.setText(self.i18n.tr("status.cancel_create"))
                event.ignore()
                return
            QMessageBox.information(self, self.i18n.tr("dialog.cancel_operation_title"), self.i18n.tr("dialog.cancel_operation_message"))
            event.ignore(); return
        if hasattr(self, "monitor"):
            self.monitor.stop()
            if self.monitor.running:
                self.monitor.idle.connect(self.close)
                event.ignore()
                return
        if hasattr(self, "global_hotkeys"):
            self.global_hotkeys.close()
        if hasattr(self, "auto_save_timer"):
            self.auto_save_timer.stop()
        self.instance_lock.release()
        super().closeEvent(event)

    # Build menu bar.
    def _build_menu_bar(self):
        """Create the application menu bar and connect its actions."""
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(False)

        self.file_menu = menu_bar.addMenu("")
        self.new_game_action = self.file_menu.addAction("")
        self.new_game_action.triggered.connect(self._add_game)
        self.import_export_action = self.file_menu.addAction("")
        self.import_export_action.triggered.connect(self._show_import_export)
        self.file_menu.addSeparator()
        self.exit_action = self.file_menu.addAction("")
        self.exit_action.triggered.connect(lambda: self.close())

        self.options_menu = menu_bar.addMenu("")
        self.themes_action = self.options_menu.addAction("")
        self.themes_action.triggered.connect(self._show_themes)
        self.settings_action = self.options_menu.addAction("")
        self.settings_action.triggered.connect(self._show_settings)

        self.about_menu = menu_bar.addMenu("")
        self.about_action = self.about_menu.addAction("")
        self.about_action.triggered.connect(self._show_about)
        self.check_updates_action = self.about_menu.addAction("")
        self.check_updates_action.triggered.connect(self._check_updates_from_about)
        self.auto_save_global_button = QToolButton(menu_bar)
        self.auto_save_global_button.setObjectName("autoSaveMenuItem")
        self.auto_save_global_button.setAutoRaise(True)
        self.auto_save_global_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.auto_save_global_button.clicked.connect(self._toggle_auto_save_global)
        self.auto_save_global_button.show()
        self.support_menu_item = QToolButton(menu_bar)
        self.support_menu_item.setObjectName("supportMenuItem")
        self.support_menu_item.setAutoRaise(True)
        self.support_menu_item.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.support_menu_item.clicked.connect(self._open_support)
        self.menu_divider = QWidget(menu_bar)
        self.menu_divider.setObjectName("menuDivider")
        self.menu_divider.setFixedSize(1, 16)
        self.menu_divider.show()
        menu_bar.setCornerWidget(self.support_menu_item, Qt.Corner.TopRightCorner)
        self._retranslate_menu()

    # Position Auto-Save beside About.
    def _position_auto_save_menu_item(self):
        menu_bar = self.menuBar()
        about_rect = menu_bar.actionGeometry(self.about_menu.menuAction())
        if not about_rect.isValid():
            return
        divider_x = about_rect.right() + 4
        button_x = divider_x + 1
        divider_y = max(0, (menu_bar.height() - 16) // 2)
        self.menu_divider.setGeometry(divider_x, divider_y, 1, 16)
        self.auto_save_global_button.setGeometry(
            QRect(button_x, 0, self.auto_save_global_button.sizeHint().width(), menu_bar.height())
        )
        self.menu_divider.raise_()
        self.auto_save_global_button.raise_()

    # Handle retranslate menu.
    def _retranslate_menu(self):
        t = self.i18n.tr
        self.file_menu.setTitle(t("menu.file"))
        self.new_game_action.setText(t("menu.new_game"))
        self.import_export_action.setText(t("menu.import_export"))
        self.exit_action.setText(t("menu.exit"))
        self.options_menu.setTitle(t("menu.options"))
        self.themes_action.setText(t("menu.themes"))
        self.settings_action.setText(t("menu.settings"))
        self.about_menu.setTitle(t("menu.about"))
        self.about_action.setText(t("menu.about_item"))
        self.check_updates_action.setText(t("menu.check_updates"))
        auto_save_available = bool(self.auto_saves.list_enabled())
        status_key = (
            "main.auto_save_global_on" if self._auto_save_global_enabled
            else "main.auto_save_global_off"
        ) if auto_save_available else "main.auto_save_global_unavailable"
        self.auto_save_global_button.setText(t(status_key))
        tooltip_key = (
            "main.auto_save_global_tooltip" if auto_save_available
            else "main.auto_save_global_unavailable_tooltip"
        )
        self.auto_save_global_button.setToolTip(t(tooltip_key))
        self.auto_save_global_button.setProperty("autoSaveEnabled", self._auto_save_global_enabled)
        self.auto_save_global_button.setProperty("autoSaveAvailable", auto_save_available)
        self.auto_save_global_button.style().unpolish(self.auto_save_global_button)
        self.auto_save_global_button.style().polish(self.auto_save_global_button)
        self.support_menu_item.setText(t("menu.support"))
        self.support_menu_item.setToolTip(t("menu.support_tooltip"))
        QTimer.singleShot(0, self._position_auto_save_menu_item)

    # Toggle global Auto-Save.
    def _toggle_auto_save_global(self, _checked=False):
        if not self.auto_saves.list_enabled():
            self._show_auto_save_settings()
            return
        self._auto_save_global_enabled = not self._auto_save_global_enabled
        self.db.set_setting("auto_save_global_enabled", self._auto_save_global_enabled)
        self._retranslate_menu()
        self._sync_auto_save_button_color()
        self._reset_auto_save_schedule()
        self._refresh_games()

    # Sync Auto-Save button color.
    def _sync_auto_save_button_color(self):
        button = getattr(self, "auto_save_button", None)
        if button is None:
            return
        auto_save_available = bool(self.current_game) and any(
            entry["enabled"] for entry in self.auto_saves.list_project(self.current_game.id)
        )
        button.setProperty("autoSaveEnabled", self._auto_save_global_enabled)
        button.setProperty("autoSaveAvailable", auto_save_available)
        button.style().unpolish(button)
        button.style().polish(button)

    # Open project support page.
    def _open_support(self):
        QDesktopServices.openUrl(QUrl("https://ko-fi.com/mmachida"))

    # Show import/export dialog.
    def _show_import_export(self):
        dialog = ImportExportDialog(self, translator=self.i18n, theme_id=self._theme_id)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        from PySide6.QtWidgets import QFileDialog

        if dialog.action == "export":
            target, _ = QFileDialog.getSaveFileName(self, self.i18n.tr("dialog.export_settings_title"), "Saverino Manager.saverino", self.i18n.tr("dialog.export_settings_filter"))
            if not target:
                return
            try:
                self.db.export_to(Path(target))
                QMessageBox.information(self, self.i18n.tr("dialog.export_settings_title"), self.i18n.tr("dialog.export_settings_success"))
            except Exception as exc:
                QMessageBox.warning(self, self.i18n.tr("dialog.unable_title"), self.i18n.tr("dialog.export_settings_error", error=exc))
        elif dialog.action == "import":
            source, _ = QFileDialog.getOpenFileName(self, self.i18n.tr("dialog.import_settings_title"), "", self.i18n.tr("dialog.import_settings_filter"))
            if not source:
                return
            if QMessageBox.question(self, self.i18n.tr("dialog.import_settings_title"), self.i18n.tr("dialog.import_settings_confirmation"), QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
                return
            try:
                self.db.import_from(Path(source))
                QMessageBox.information(self, self.i18n.tr("dialog.import_settings_title"), self.i18n.tr("dialog.import_settings_success"))
            except Exception as exc:
                QMessageBox.warning(self, self.i18n.tr("dialog.unable_title"), self.i18n.tr("dialog.import_settings_error", error=exc))

    # Show themes dialog.
    def _show_themes(self):
        dialog = ThemeDialog(self, translator=self.i18n, current_theme=self._theme_id, themes=THEMES)
        dialog.theme_selected.connect(self._apply_theme)
        dialog.exec()

    # Apply selected theme.
    def _apply_theme(self, theme_id: object):
        self._theme_id = normalize_theme(theme_id)
        self.setStyleSheet(theme_stylesheet(self._theme_id))
        if hasattr(self, "table"):
            palette = self.table.palette()
            text_color = QColor(get_theme(self._theme_id).colors["text"])
            palette.setColor(QPalette.ColorRole.Text, text_color)
            palette.setColor(QPalette.ColorRole.HighlightedText, text_color)
            self.table.setPalette(palette)
            if self.current_profile:
                self._refresh_snapshots()
        if hasattr(self, "games"):
            colors = get_theme(self._theme_id).colors
            palette = self.games.palette()
            palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["list_selection_text"]))
            self.games.setPalette(palette)
        if hasattr(self, "db"):
            self.db.set_setting("theme", self._theme_id)
            if hasattr(self, "games"):
                selection_text = get_theme(self._theme_id).colors["list_selection_text"]
                for row in range(self.games.count()):
                    self.games.item(row).setData(Qt.ItemDataRole.UserRole + 2, selection_text)
                self.games.viewport().update()

    # Load app settings.
    def _load_app_settings(self) -> dict[str, object]:
        stored = self.db.get_setting("app_settings", {})
        values = dict(DEFAULT_APP_SETTINGS)
        if isinstance(stored, dict):
            values.update(stored)
            if "automatically_check_for_updates" not in stored and "check_updates_on_startup" in stored:
                values["automatically_check_for_updates"] = bool(stored["check_updates_on_startup"])
        if not LanguageManager.normalize(stored.get("language") if isinstance(stored, dict) else None):
            values["language"] = LanguageManager.detect_system_language()
        return values

    # Show settings.
    def _show_settings(self):
        # Libere temporariamente as combinações atuais para que o usuário
        # consiga pressioná-las ao editar os campos da própria janela.
        self.global_hotkeys.clear()
        original_language = self.i18n.language
        dialog = SettingsDialog(self, settings=self._app_settings, translator=self.i18n)
        self._settings_dialog = dialog
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._cancel_update_worker()
            self.i18n.set_language(original_language)
            self.global_hotkeys.apply(self._app_settings)
            self._settings_dialog = None
            return
        self._cancel_update_worker()
        values = dict(DEFAULT_APP_SETTINGS)
        values.update(dialog.values())
        self._app_settings = values
        self.db.set_setting("app_settings", self._app_settings)
        self._apply_application_settings(self._app_settings, notify=True)
        self._settings_dialog = None

    # Get active changed.
    def _language_changed(self, _language: str):
        if not hasattr(self, "file_menu"):
            return
        self._retranslate_menu()
        self._retranslate_ui()

    # Handle retranslate ui.
    def _retranslate_ui(self):
        t = self.i18n.tr
        self.setWindowTitle(t("app.title"))
        if not hasattr(self, "games"):
            return
        self.games_header.setText(t("main.games"))
        self.game_search.setPlaceholderText(t("main.search_games"))
        self.game_search.setToolTip(t("main.search_games_tooltip"))
        self.add_game_button.setText(t("main.add_game"))
        self.game_settings_button.setText(t("main.game_settings"))
        self.game_settings_button.setToolTip(t("main.game_settings_tooltip"))
        self.profiles.setToolTip(t("main.profile_tooltip"))
        self.auto_save_button.setText(t("main.auto_save"))
        self.auto_save_button.setToolTip(t("main.auto_save_tooltip"))
        self._sync_auto_save_button_color()
        self.refresh_button.setText(t("main.refresh"))
        self.refresh_button.setToolTip(t("main.refresh_tooltip"))
        self.add_profile_button.setText(t("main.new_profile"))
        self.profile_settings_button.setText(t("main.profile_settings"))
        self.profile_settings_button.setToolTip(t("main.profile_settings_tooltip"))
        self.delete_profile_button.setText(t("main.delete_profile"))
        self.delete_profile_button.setToolTip(t("main.delete_profile_tooltip"))
        catalog_pt = getattr(self.i18n, "_catalogs", {}).get("pt-BR", {})
        profile_buttons = (
            (self.profile_settings_button, "main.profile_settings"),
            (self.add_profile_button, "main.new_profile"),
        )
        current_texts = [button.text() for button, _key in profile_buttons]
        for button, key in profile_buttons:
            button.setText(catalog_pt.get(key, button.text()))
        equalize_button_widths([self.profile_settings_button, self.add_profile_button])
        for (button, _key), text in zip(profile_buttons, current_texts):
            button.setText(text)
        self.create_button.setText(t("main.create_save"))
        self.create_button.setToolTip(t("main.create_save_tooltip"))
        self.overwrite_button.setText(t("main.overwrite_save"))
        self.restore_button.setText(t("main.load_save"))
        self.open_source_button.setText(t("main.open_source"))
        self.open_backups_button.setText(t("main.open_backups"))
        self.cancel_button.setText(t("main.cancel_operation"))
        self.snapshot_search.setPlaceholderText(t("main.search_saves"))
        self.snapshot_search.setToolTip(t("main.search_saves_tooltip"))
        self.table.setToolTip(t("main.save_list_tooltip"))
        if not self.operation_active:
            self.status_label.setText(t("main.ready"))
        else:
            current_status = self.status_label.text()
            catalogs = getattr(self.i18n, "_catalogs", {})
            for key in ("status.creating", "status.overwriting", "status.loading", "status.refreshing", "status.cancelling", "status.configure_game", "status.auto_saving"):
                if current_status in (
                    catalogs.get("en-US", {}).get(key),
                    catalogs.get("pt-BR", {}).get(key),
                ):
                    self.status_label.setText(t(key))
                    break
        self.table.setHorizontalHeaderLabels([
            t("table.number"), t("table.name"), t("table.description"), t("table.created"),
        ])
        self._game_changed(self.games.currentRow())
        self._snapshot_selection_changed()

    # Check updates from about.
    def _check_updates_from_about(self):
        """Open the update window and start the manual check immediately."""
        self._open_update_dialog()

    # Open update dialog.
    def _open_update_dialog(self, *, release: ReleaseInfo | None = None):
        """Show the update flow either for a manual or an automatic check."""
        if self._update_worker is not None:
            return
        dialog = UpdateDialog(self, translator=self.i18n)
        self._update_dialog = dialog
        dialog.download_update_requested.connect(lambda: self._start_update_download(dialog))
        dialog.cancel_update_requested.connect(self._cancel_update_worker)
        if release is None:
            self._start_update_check(dialog, automatic=False)
        else:
            dialog.show_update_available(release)
        dialog.exec()
        self._cancel_update_worker()
        self._update_dialog = None

    # Start update check.
    def _start_update_check(self, dialog=None, *, automatic: bool):
        if not self.update_service.configured:
            if dialog is not None:
                dialog.set_update_status(self.i18n.tr("updates.status_not_configured"))
            return
        if self._update_worker is not None:
            if dialog is not None:
                dialog.set_update_status(self.i18n.tr("updates.status_busy"))
            return
        # Do not let a release from an earlier query remain actionable while
        # this request is in flight or after a failed/latest-version result.
        self._update_release = None
        if dialog is not None:
            dialog.set_update_checking(True)
            dialog.set_update_status(self.i18n.tr("updates.status_checking"))
        worker = ServiceWorker(self.update_service.check_latest, with_progress=False)
        self._update_worker = worker
        worker.signals.finished.connect(lambda release: self._update_check_finished(release, dialog, automatic))
        worker.signals.error.connect(lambda message: self._update_check_failed(message, dialog, automatic))
        self.thread_pool.start(worker)

    # Update check finished.
    def _update_check_finished(self, release, dialog, automatic: bool):
        self._update_worker = None
        self._record_update_check(dialog)
        if dialog is not None:
            dialog.set_update_checking(False)
        if release is None:
            if dialog is not None:
                dialog.set_update_status(self.i18n.tr("updates.status_latest"))
            return
        self._update_release = release
        if dialog is not None:
            dialog.show_update_available(release)
        elif automatic:
            self._open_update_dialog(release=release)

    # Update check failed.
    def _update_check_failed(self, message: str, dialog, automatic: bool):
        self._update_worker = None
        self._record_update_check(dialog)
        if dialog is not None:
            dialog.set_update_checking(False)
            dialog.set_update_status(message)
        elif automatic:
            logging.getLogger("save_manager").info("Verificação automática de atualizações falhou: %s", message)

    # Handle record update check.
    def _record_update_check(self, dialog=None) -> str:
        checked_at = datetime.now().astimezone().isoformat()
        self._app_settings["last_update_check_at"] = checked_at
        self.db.set_setting("app_settings", self._app_settings)
        if dialog is not None and hasattr(dialog, "_last_update_check_at"):
            dialog._last_update_check_at = checked_at
        return checked_at

    # Start update download.
    def _start_update_download(self, dialog):
        release = self._update_release
        if release is None or self._update_worker is not None:
            return
        if self.recovery_required:
            dialog.set_update_status(self.i18n.tr("dialog.recovery_message", count=len(self.journals.pending())))
            return
        try:
            download_dir = Path(tempfile.mkdtemp(prefix="save-manager-update-", dir=self.policy.data_root))
        except OSError as exc:
            dialog.set_update_status(self.i18n.tr("updates.status_download_folder_error", error=exc))
            return
        self._update_download_dir = download_dir
        dialog.set_update_checking(True)
        dialog.set_update_status(self.i18n.tr("updates.status_downloading", version=release.version_text))

        worker = None

        # Handle operation.
        def operation(*, cancel):
            return self.update_service.download_and_prepare(
                release,
                download_dir,
                progress=lambda completed, total: worker.signals.progress.emit((completed, total)),
                cancel=cancel,
            )

        worker = ServiceWorker(operation, cancellable=True, with_progress=False)
        self._update_worker = worker
        worker.signals.progress.connect(lambda item: dialog.set_update_progress(*item))
        worker.signals.finished.connect(lambda prepared: self._update_download_finished(prepared, dialog))
        worker.signals.error.connect(lambda message: self._update_download_failed(message, dialog))
        self.thread_pool.start(worker)

    # Update download finished.
    def _update_download_finished(self, prepared: PreparedUpdate, dialog):
        self._update_worker = None
        self._prepared_update = prepared
        dialog.set_update_checking(False)
        dialog.set_update_status(self.i18n.tr("updates.status_ready", version=prepared.release.version_text))
        answer = QMessageBox.question(
            dialog,
            self.i18n.tr("dialog.install_update_title"),
            self.i18n.tr("dialog.install_update"),
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._launch_prepared_update(dialog)
        else:
            self._cleanup_prepared_update()

    # Update download failed.
    def _update_download_failed(self, message: str, dialog):
        self._update_worker = None
        dialog.set_update_checking(False)
        dialog.set_update_status(message)
        self._cleanup_prepared_update()

    # Cancel update worker.
    def _cancel_update_worker(self):
        worker = self._update_worker
        if worker is not None and getattr(worker, "cancellable", False):
            worker.cancel()

    # Handle cleanup prepared update.
    def _cleanup_prepared_update(self):
        if self._update_download_dir is not None:
            import shutil

            shutil.rmtree(self._update_download_dir, ignore_errors=True)
        self._update_download_dir = None
        self._prepared_update = None

    # Launch prepared update.
    def _launch_prepared_update(self, dialog):
        prepared = self._prepared_update
        if prepared is None:
            return
        if self.operation_active:
            dialog.set_update_status(self.i18n.tr("updates.status_wait_operation"))
            QTimer.singleShot(100, lambda: self._launch_prepared_update(dialog))
            return
        if self.monitor.running:
            dialog.set_update_status(self.i18n.tr("updates.status_wait_sync"))
            QTimer.singleShot(100, lambda: self._launch_prepared_update(dialog))
            return
        self._update_install_pending = True
        for button in (self.create_button, self.overwrite_button, self.restore_button, self.trash_button, self.add_profile_button, self.refresh_button):
            button.setEnabled(False)
        root = default_install_root()
        if getattr(sys, "frozen", False):
            helper = root / "SaveManagerUpdater.exe"
            application = Path(sys.executable).resolve()
            app_arguments: list[str] = []
        else:
            helper = root / "scripts" / "update_helper_entry.py"
            application = Path(sys.executable).resolve()
            app_arguments = ["-m", "save_manager"]
        if not helper.is_file():
            dialog.set_update_status(self.i18n.tr("updates.status_helper_missing"))
            self._update_install_pending = False
            self._refresh_action_states()
            return
        try:
            check_install_prerequisites(prepared.archive_path, root)
        except Exception as exc:
            dialog.set_update_status(str(exc))
            self._update_install_pending = False
            self._refresh_action_states()
            return
        command = [
            str(application),
            str(helper),
            "--archive", str(prepared.archive_path),
            "--checksum", str(prepared.checksum_path),
            "--install-root", str(root),
            "--app", str(application),
            "--pid", str(os.getpid()),
            "--version", prepared.release.version_text,
            "--package-id", self.update_service.config.package_id,
            "--arguments", json.dumps(app_arguments),
        ]
        try:
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            subprocess.Popen(command, cwd=str(root), creationflags=flags, close_fds=True)
        except OSError as exc:
            dialog.set_update_status(self.i18n.tr("updates.status_launch_error", error=exc))
            self._update_install_pending = False
            self._refresh_action_states()
            return
        dialog.set_update_status(self.i18n.tr("updates.status_started"))
        self.close()

    # Apply application settings.
    def _apply_application_settings(self, settings, *, notify=False):
        self.audio.configure(settings)
        startup_enabled = bool(settings.get("initialize_with_windows", False))
        startup_updated = not startup_enabled
        if self.startup_service.supported:
            try:
                startup_updated = self.startup_service.set_enabled(startup_enabled)
            except OSError:
                startup_updated = False
                logging.getLogger("save_manager").exception("Could not update Windows startup registration")
        if notify and not startup_updated:
            QMessageBox.warning(
                self,
                self.i18n.tr("dialog.unable_title"),
                self.i18n.tr("settings.startup_update_failed"),
            )
        always_on_top = bool(settings.get("always_on_top", False))
        flag = Qt.WindowType.WindowStaysOnTopHint
        current = bool(self.windowFlags() & flag)
        if current != always_on_top:
            visible = self.isVisible()
            if visible:
                # Changing window flags recreates the native window on
                # Windows.  Release registrations before that transition and
                # wait for the new HWND before registering them again.
                self.global_hotkeys.clear()
            self.setWindowFlag(flag, always_on_top)
            if visible:
                self._pending_hotkey_settings = dict(settings)
                self._pending_hotkey_notify = notify
                QTimer.singleShot(0, self.show)
                return
        self._apply_global_hotkeys(settings, notify=notify)

    # Apply global hotkeys.
    def _apply_global_hotkeys(self, settings, *, notify=False):
        failures = self.global_hotkeys.apply(settings)
        if not failures:
            return
        reason_keys = {
            "disponíveis somente no Windows": "settings.hotkeys_windows_only",
            "combinação não suportada": "settings.hotkey_unsupported",
            "combinação repetida": "settings.hotkey_duplicate",
            "combinação já está em uso por outro programa": "settings.hotkey_in_use",
        }
        label_keys = {
            "Hotkeys globais": "settings.global_hotkeys",
            "Criar save": "settings.create_hotkey",
            "Carregar Save": "settings.load_hotkey",
        }
        details = "\n".join(
            f"• {self.i18n.tr(label_keys[label]) if label in label_keys else label}: "
            f"{self.i18n.tr(reason_keys[reason]) if reason in reason_keys else reason}."
            for label, reason in failures
        )
        logging.getLogger("save_manager").warning("Global hotkeys were not registered: %s", details.replace("\n", " "))
        if notify:
            QMessageBox.warning(self, self.i18n.tr("dialog.global_hotkeys_title"), self.i18n.tr("dialog.global_hotkeys_error", details=details))

    # Handle hotkey triggered.
    def _global_hotkey_triggered(self, action: str):
        # O Windows pode entregar a mesma combinação mais de uma vez para a
        # fila nativa do Qt.  Enquanto a operação ou o diálogo de confirmação
        # ainda estiver ativo, ignore novas mensagens para não repetir a ação.
        if self.operation_active or self._update_install_pending or QApplication.activeModalWidget() is not None:
            return
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        if action == "create_save":
            self._create_snapshot_automatic()
        elif action == "load_save":
            self._restore_snapshot()

    # Show about.
    def _show_about(self):
        dialog = AboutDialog(self, translator=self.i18n, github_url=self.update_service.config.repository_url)
        dialog.exec()

    # Build ui.
    def _build_ui(self):
        left = QWidget(); left_layout = QVBoxLayout(left)
        self.games_header = QLabel(); self.games_header.setAlignment(Qt.AlignmentFlag.AlignVCenter); left_layout.addWidget(self.games_header)
        self.game_search = QLineEdit(); self.game_search.textChanged.connect(lambda _text: self._refresh_games()); left_layout.addWidget(self.game_search)
        self.games = QListWidget(); self.games.setMouseTracking(True); self.games.setWordWrap(False); self.games.setTextElideMode(Qt.TextElideMode.ElideRight); self.games.setItemDelegate(ProjectListDelegate(self.games)); self.games.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove); self.games.model().rowsMoved.connect(lambda *_args: self._games_reordered()); self._configure_smooth_scroll(self.games); self.games.currentRowChanged.connect(self._game_changed); self.games.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.games.customContextMenuRequested.connect(self._show_game_menu); left_layout.addWidget(self.games)
        self.add_game_button = QPushButton(); self.add_game_button.clicked.connect(self._add_game); left_layout.addWidget(self.add_game_button)
        right = QWidget(); layout = QVBoxLayout(right)
        game_header = QHBoxLayout()
        self.game_title = QLabel(); game_header.addWidget(self.game_title, 1)
        self.auto_save_button = QPushButton()
        self.auto_save_button.setObjectName("autoSaveSettingsButton")
        self.auto_save_button.clicked.connect(self._show_auto_save_settings)
        game_header.addWidget(self.auto_save_button)
        self.game_settings_button = QPushButton()
        self.game_settings_button.clicked.connect(self._edit_game_settings)
        game_header.addWidget(self.game_settings_button)
        # Start with the same height as the header control.  The final value
        # is synchronized after the window is shown, once style metrics are
        # available.
        self.games_header.setFixedHeight(self.game_settings_button.sizeHint().height())
        layout.addLayout(game_header)
        profile_row = QHBoxLayout(); self.profiles = ProfileComboBox(); self.profiles.view().setMouseTracking(True); self._configure_smooth_scroll(self.profiles.view()); self.profiles.currentIndexChanged.connect(self._profile_changed); self.profiles.activated.connect(self._profile_activated); profile_row.addWidget(self.profiles, 1)
        self.profile_settings_button = QPushButton(); self.profile_settings_button.setStyleSheet("QPushButton { padding-left: 4px; padding-right: 4px; }"); self.profile_settings_button.clicked.connect(self._show_profile_settings); profile_row.addWidget(self.profile_settings_button)
        self.add_profile_button = QPushButton(); self.add_profile_button.setStyleSheet("QPushButton { padding-left: 4px; padding-right: 4px; }"); self.add_profile_button.clicked.connect(self._add_profile); profile_row.addWidget(self.add_profile_button)
        self.delete_profile_button = QPushButton()
        self.delete_profile_button.setObjectName("destructiveButton")
        self.delete_profile_button.clicked.connect(self._delete_profile)
        profile_row.addWidget(self.delete_profile_button)
        layout.addLayout(profile_row)
        self.path_label = QLabel(); self.path_label.setWordWrap(True); layout.addWidget(self.path_label)
        actions = QHBoxLayout()
        self.create_button = QPushButton(); self.create_button.clicked.connect(self._create_snapshot); actions.addWidget(self.create_button)
        self.overwrite_button = QPushButton(); self.overwrite_button.clicked.connect(self._overwrite_snapshot); actions.addWidget(self.overwrite_button)
        self.restore_button = QPushButton(); self.restore_button.setObjectName("loadButton"); self.restore_button.clicked.connect(self._restore_snapshot); actions.addWidget(self.restore_button)
        self.trash_button = QPushButton(); self.trash_button.setObjectName("destructiveButton"); self.trash_button.clicked.connect(self._trash_snapshot); actions.addWidget(self.trash_button)
        self.delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        self.delete_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.delete_shortcut.activated.connect(self.trash_button.click)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self._cancel_operation)
        self.cancel_button.setVisible(False)
        actions.addWidget(self.cancel_button)
        self.open_source_button = QPushButton(); self.open_source_button.clicked.connect(lambda: self._open_folder("source")); actions.addWidget(self.open_source_button)
        self.open_backups_button = QPushButton(); self.open_backups_button.clicked.connect(lambda: self._open_folder("backup")); actions.addWidget(self.open_backups_button)
        layout.addLayout(actions)
        self.snapshot_search = QLineEdit(); self.snapshot_search.textChanged.connect(lambda _text: self._refresh_snapshots()); layout.addWidget(self.snapshot_search)
        self.table = SnapshotTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "Name", "Description", "Created"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.setAlternatingRowColors(False)
        self.table.setMouseTracking(True)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.verticalHeader().setVisible(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_snapshot_menu)
        # O ajuste inicial preenche a área visível; os redimensionamentos
        # manuais continuam livres, mas a soma das colunas nunca ultrapassa
        # a viewport.
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in range(self.table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setSortIndicator(3, Qt.SortOrder.AscendingOrder)
        self.table.setColumnWidth(0, 42)
        self.table.setColumnWidth(1, 118)
        self.table.setColumnWidth(2, 442)
        self.table.setColumnWidth(3, 160)
        # As colunas sempre são ajustadas para caber na viewport; não há
        # rolagem horizontal para esconder a última coluna.
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._configure_smooth_scroll(self.table)
        self._snapshot_columns_user_resized = False
        self._snapshot_resizing_columns = False
        self._snapshot_resized_column = None
        self._normalizing_snapshot_selection = False
        header.sectionResized.connect(self._snapshot_section_resized)
        self.table.verticalScrollBar().rangeChanged.connect(
            lambda *_args: QTimer.singleShot(0, self._fit_snapshot_columns)
        )
        self.table.setItemDelegate(DescriptionDelegate(self.table))
        self.table.itemChanged.connect(self._snapshot_item_changed)
        self.table.itemSelectionChanged.connect(self._snapshot_selection_changed)
        layout.addWidget(self.table)
        QTimer.singleShot(0, self._fit_snapshot_columns)
        status_row = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.status_label.setFixedHeight(self.add_game_button.sizeHint().height())
        status_row.addWidget(self.status_label, 1)
        self.refresh_button = QPushButton()
        self.refresh_button.clicked.connect(self._refresh_saves_from_disk)
        status_row.addWidget(self.refresh_button)
        self.version_label = QLabel(f"v{APP_VERSION}")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.version_label.setFixedHeight(self.add_game_button.sizeHint().height())
        status_row.addWidget(self.version_label)
        layout.addLayout(status_row)
        self._snapshot_selection_changed()
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        left.setFixedWidth(220)
        split.setSizes([220, 740])
        split.handle(1).setCursor(Qt.CursorShape.ArrowCursor)
        self.setCentralWidget(split)
        self._retranslate_ui()

    @staticmethod
    # Configure smooth scroll.
    def _configure_smooth_scroll(view):
        """Use small pixel steps so wheel scrolling feels gradual."""
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.verticalScrollBar().setSingleStep(8)
        view.horizontalScrollBar().setSingleStep(8)

    # Handle window display.
    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._align_panel_geometry)
        QTimer.singleShot(0, self._position_auto_save_menu_item)
        if not self._auto_update_check_scheduled:
            self._auto_update_check_scheduled = True
            QTimer.singleShot(1500, self._maybe_auto_check_updates)
        if hasattr(self, "global_hotkeys"):
            # A native handle can be created or replaced when a hidden
            # top-level window is first shown.  Register again after that
            # transition so WM_HOTKEY always targets the live window.
            QTimer.singleShot(0, self._apply_hotkeys_after_show)

    # Maybe auto check updates.
    def _maybe_auto_check_updates(self):
        if not bool(self._app_settings.get("automatically_check_for_updates", True)):
            return
        if not self.update_service.configured:
            return
        self._start_update_check(None, automatic=True)

    # Apply hotkeys after show.
    def _apply_hotkeys_after_show(self):
        settings = self._pending_hotkey_settings
        notify = self._pending_hotkey_notify
        self._pending_hotkey_settings = None
        self._pending_hotkey_notify = False
        if settings is None:
            settings = self._app_settings
            notify = False
        self._apply_global_hotkeys(settings, notify=notify)

    # Align panel geometry.
    def _align_panel_geometry(self):
        """Keep the two panel lists aligned after Qt applies the widget style."""
        if not hasattr(self, "table"):
            return
        left_layout = self.games.parentWidget().layout()
        right_layout = self.table.parentWidget().layout()
        left_layout.activate()
        right_layout.activate()

        games_label = left_layout.itemAt(0).widget()
        if games_label is not None:
            target_height = self.profiles.geometry().top() - left_layout.contentsMargins().top() - left_layout.spacing()
            if target_height >= 0 and games_label.height() != target_height:
                games_label.setFixedHeight(target_height)
                left_layout.activate()

        # The footer row and add-game button close both panel layouts.
        if self.status_label is not None:
            add_game = left_layout.itemAt(3).widget()
            if add_game is not None and self.status_label.height() != add_game.height():
                self.status_label.setFixedHeight(add_game.height())
                self.refresh_button.setFixedHeight(add_game.height())
                right_layout.activate()
            delta = self.table.geometry().bottom() - self.games.geometry().bottom()
            if delta:
                footer_height = max(0, self.status_label.height() + delta)
                self.status_label.setFixedHeight(footer_height)
                self.refresh_button.setFixedHeight(footer_height)
                right_layout.activate()

    # Handle window resize.
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "table"):
            QTimer.singleShot(0, self._fit_snapshot_columns)
        if hasattr(self, "auto_save_global_button"):
            QTimer.singleShot(0, self._position_auto_save_menu_item)

    # Handle snapshot section resized.
    def _snapshot_section_resized(self, _logical_index, _old_size, _new_size):
        if self._snapshot_resizing_columns:
            self.table.resizeRowsToContents()
            return
        self._snapshot_columns_user_resized = True
        self._snapshot_resized_column = _logical_index
        self.table.resizeRowsToContents()
        # Keep the final column against the viewport edge after a manual
        # resize, even when the other columns no longer fill the table.
        self._fit_snapshot_columns()
        # A section resize can update the viewport geometry one event later
        # (for example when the vertical scrollbar changes visibility).
        QTimer.singleShot(0, self._fit_snapshot_columns)

    # Reset snapshot columns.
    def _reset_snapshot_columns(self):
        if not hasattr(self, "table"):
            return
        header = self.table.horizontalHeader()
        defaults = (42, 118, 442, 160)
        self._snapshot_columns_user_resized = False
        self._snapshot_resized_column = None
        self._snapshot_resizing_columns = True
        try:
            for column, width in enumerate(defaults):
                header.resizeSection(column, width)
        finally:
            self._snapshot_resizing_columns = False
        self._fit_snapshot_columns()

    # Fit snapshot creation column.
    def _fit_snapshot_creation_column(self):
        if not hasattr(self, "table") or self._snapshot_columns_user_resized:
            return
        header = self.table.horizontalHeader()
        label = self.table.horizontalHeaderItem(3)
        texts = [label.text() if label else self.i18n.tr("table.created")]
        texts.extend(
            self.table.item(row, 3).text()
            for row in range(self.table.rowCount())
            if self.table.item(row, 3)
        )
        width = max(self.table.fontMetrics().horizontalAdvance(text) for text in texts) + 20
        width = max(110, width)
        if header.sectionSize(3) == width:
            return
        self._snapshot_resizing_columns = True
        try:
            header.resizeSection(3, width)
        finally:
            self._snapshot_resizing_columns = False

    # Fit snapshot columns.
    def _fit_snapshot_columns(self):
        if not hasattr(self, "table"):
            return
        viewport_width = self.table.viewport().width()
        if viewport_width <= 0:
            return
        header = self.table.horizontalHeader()
        sizes = [header.sectionSize(column) for column in range(self.table.columnCount())]
        total = sum(sizes)
        self._snapshot_resizing_columns = True
        try:
            if total > viewport_width:
                overflow = total - viewport_width
                minimums = {0: 32, 1: 90, 2: 160, 3: 120}
                # Preserve the column that is being dragged.  The remaining
                # space is taken from the final column first, then from the
                # other columns, so the middle divider remains usable.
                resized = self._snapshot_resized_column
                order = [column for column in (3, 2, 1, 0) if column != resized]
                if resized is not None:
                    order.append(resized)
                for column in order:
                    current = header.sectionSize(column)
                    reducible = max(0, current - minimums[column])
                    reduction = min(reducible, overflow)
                    if reduction:
                        header.resizeSection(column, current - reduction)
                        overflow = max(0, sum(header.sectionSize(index) for index in range(self.table.columnCount())) - viewport_width)
                    if overflow <= 0:
                        break
                # Styles can clamp a section a few pixels above the logical
                # minimum.  A final pass closes that rounding gap so the
                # horizontal scrollbar never becomes necessary.
                if overflow > 0:
                    for column in order:
                        current = header.sectionSize(column)
                        if current <= 1:
                            continue
                        header.resizeSection(column, max(1, current - overflow))
                        overflow = max(0, sum(header.sectionSize(index) for index in range(self.table.columnCount())) - viewport_width)
                        if overflow <= 0:
                            break
            elif total < viewport_width:
                extra = viewport_width - total
                # The default layout keeps the description as the flexible
                # column.  After a manual resize, the final column absorbs
                # the remaining space so it stays flush with the right edge.
                target = 3 if self._snapshot_columns_user_resized else 2
                header.resizeSection(target, sizes[target] + extra)
        finally:
            self._snapshot_resizing_columns = False

    # Refresh games.
    def _refresh_games(self, preserve_game_id=None):
        if preserve_game_id is None and getattr(self, "current_game", None) is not None:
            preserve_game_id = self.current_game.id
        self.games.blockSignals(True)
        self.games.clear()
        query = self.game_search.text().casefold().strip() if hasattr(self, "game_search") else ""
        all_games = self.db.list_games()
        saved_order = self.db.get_setting("game_order", [])
        by_id = {str(game.id): game for game in all_games}
        ordered_games = [by_id.pop(str(game_id)) for game_id in saved_order if str(game_id) in by_id]
        ordered_games.extend(by_id.values())
        self.visible_games = [game for game in ordered_games if not query or query in game.name.casefold()]
        auto_save_projects = {
            str(entry["project_id"]) for entry in self.auto_saves.list_enabled()
        } if self._auto_save_global_enabled else set()
        selection_text = get_theme(self._theme_id).colors["list_selection_text"]
        for game in self.visible_games:
            display_name = game.name if len(game.name) <= MAX_GAME_MENU_NAME_LENGTH else game.name[:MAX_GAME_MENU_NAME_LENGTH - 1] + "…"
            self.games.addItem(display_name)
            item = self.games.item(self.games.count() - 1)
            item.setToolTip(game.name)
            item.setData(Qt.ItemDataRole.UserRole, str(game.id))
            item.setData(Qt.ItemDataRole.UserRole + 2, selection_text)
            if str(game.id) in auto_save_projects:
                item.setData(Qt.ItemDataRole.UserRole + 1, True)
                item.setToolTip(f"{game.name}\n{self.i18n.tr('autosave.project_active_tooltip')}")
        if self.games.count():
            row = next((index for index, game in enumerate(self.visible_games) if game.id == preserve_game_id), 0)
            self.games.setCurrentRow(row)
            self.games.blockSignals(False)
            self._game_changed(row)
        else:
            self.games.blockSignals(False)
            self._game_changed(-1)

    # Persist game drag order.
    def _games_reordered(self):
        ordered_ids = [str(self.games.item(index).data(Qt.ItemDataRole.UserRole)) for index in range(self.games.count())]
        visible_ids = set(ordered_ids)
        all_ids = [str(game.id) for game in self.db.list_games()]
        self.db.set_setting("game_order", ordered_ids + [game_id for game_id in all_ids if game_id not in visible_ids])
        self._refresh_games(preserve_game_id=self.current_game.id if self.current_game else None)

    # Handle game changed.
    def _game_changed(self, row: int):
        games = getattr(self, "visible_games", self.db.list_games())
        self.current_game = games[row] if 0 <= row < len(games) else None
        self._sync_auto_save_button_color()
        if self.current_game: self.db.set_setting("selected_game", str(self.current_game.id))
        self.game_title.setText(self.current_game.name if self.current_game else self.i18n.tr("main.first_game"))
        self.game_settings_button.setEnabled(self.current_game is not None)
        configured = self.current_game and self.db.get_game_settings(self.current_game.id)
        self.add_profile_button.setEnabled(bool(configured))
        self.add_profile_button.setToolTip("" if configured else self.i18n.tr("main.configure_profile_tooltip"))
        saved_profile = self.current_profile.id if self.current_profile else self.db.get_setting("selected_profile")
        self.profiles.blockSignals(True)
        self.profiles.clear()
        if self.current_game:
            for p in self.db.list_profiles(self.current_game.id): self.profiles.addItem(p.name, p.id)
            if saved_profile:
                for index in range(self.profiles.count()):
                    if str(self.profiles.itemData(index)) == str(saved_profile): self.profiles.setCurrentIndex(index); break
        self.profiles.blockSignals(False)
        self._profile_changed(self.profiles.currentIndex())

    # Handle profile changed.
    def _profile_changed(self, index: int):
        self._reset_snapshot_columns()
        self.current_profile = None
        if index >= 0 and self.profiles.itemData(index): self.current_profile = self.db.get_profile(UUID(str(self.profiles.itemData(index))))
        if self.current_profile: self.db.set_setting("selected_profile", str(self.current_profile.id))
        if self.current_profile:
            try: effective = self.policy.resolve(self.current_profile, str(self.current_profile.game_id), game_name=self.current_game.name if self.current_game else None, validate_origin=False)
            except Exception as exc: self.path_label.setText(str(exc))
            else: self.path_label.setText(f"{self.i18n.tr('main.source', path=effective.source)}\n{self.i18n.tr('main.backups', path=effective.namespace)}")
            self._refresh_snapshots()
        else:
            settings = self.db.get_game_settings(self.current_game.id) if self.current_game else None
            if settings:
                self.path_label.setText(f"{self.i18n.tr('main.game_save', path=settings.source_path)}\n{self.i18n.tr('main.create_profile_hint')}")
            else:
                self.path_label.setText(self.i18n.tr("main.configure_game_hint"))
            self.table.setRowCount(0)
        self._refresh_action_states()

    # Handle profile activated.
    def _profile_activated(self, _index: int):
        """Close the native popup after a user confirms an item."""
        QTimer.singleShot(0, self.profiles.hidePopup)

    # Refresh saves from disk.
    def _refresh_saves_from_disk(self):
        if self._manual_refresh_active or self._manual_refresh_pending:
            return
        if self.operation_active or self._update_install_pending or not self.current_profile:
            return
        if self.monitor.running:
            self._manual_refresh_pending = True
            self._refresh_action_states()
            QTimer.singleShot(50, self._retry_manual_refresh)
            return
        profile_id = self.current_profile.id
        self._manual_refresh_active = True
        worker = ServiceWorker(self.maintenance.refresh_profile, profile_id, with_progress=False)
        self.active_worker = worker
        self.operation_active = True
        self.cancel_button.setVisible(False)
        self.status_label.setText(self.i18n.tr("status.refreshing"))
        self._refresh_action_states()
        worker.signals.finished.connect(lambda _result, profile_id=profile_id: self._manual_refresh_finished(profile_id))
        worker.signals.error.connect(self._manual_refresh_failed)
        self.thread_pool.start(worker)

    # Retry a refresh after synchronization.
    def _retry_manual_refresh(self):
        self._manual_refresh_pending = False
        self._refresh_saves_from_disk()

    # Handle refresh completion.
    def _manual_refresh_finished(self, profile_id):
        self._manual_refresh_active = False
        if self.current_profile and self.current_profile.id == profile_id:
            self._refresh_snapshots()
        self._operation_finished(self.i18n.tr("status.refreshed"))

    # Handle refresh failure.
    def _manual_refresh_failed(self, message):
        self._manual_refresh_active = False
        self._operation_finished(self.i18n.tr("status.failure"))
        QMessageBox.warning(
            self,
            self.i18n.tr("dialog.unable_title"),
            self.i18n.tr("dialog.refresh_failed", error=message),
        )

    # Refresh snapshots.
    def _refresh_snapshots(self, *, scroll_to_end=False):
        selected = self._selected_snapshot()
        selected_id = str(selected.id) if selected else None
        scroll = self.table.verticalScrollBar().value()
        snaps = self.db.list_snapshots(self.current_profile.id) if self.current_profile else []
        snaps = [snap for snap in snaps if snap.status != SnapshotStatus.MISSING]
        query = self.snapshot_search.text().casefold().strip() if hasattr(self, "snapshot_search") else ""
        snaps = [snap for snap in snaps if not query or query in snap.name.casefold() or query in snap.notes.casefold()]
        snaps.sort(key=lambda snap: (snap.created_at, str(snap.id)))
        self.visible_snapshots = snaps
        project_schedules = (
            self.auto_saves.list_project(self.current_game.id)
            if self.current_game and self.current_profile
            else []
        )
        auto_save_snapshots = {
            str(entry["snapshot_id"])
            for entry in project_schedules
            if entry["target_type"] in ("file", "folder")
            and entry["profile_id"] == str(self.current_profile.id)
            and entry.get("snapshot_id")
        }
        auto_save_color = get_theme(self._theme_id).colors["auto_save_on"]
        header = self.table.horizontalHeader()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        if sort_column <= 0:
            sort_column, sort_order = 3, Qt.SortOrder.AscendingOrder
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        header.setSortIndicator(sort_column, sort_order)
        self.table.setRowCount(0)
        for row_number, snap in enumerate(snaps, start=1):
            row = self.table.rowCount(); self.table.insertRow(row)
            created = self._format_local_datetime(snap.created_at)
            values = (row_number, snap.name, snap.notes, created)
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col not in (1, 2):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if str(snap.id) in auto_save_snapshots:
                    item.setForeground(QColor(auto_save_color))
                    item.setData(Qt.ItemDataRole.UserRole + 1, auto_save_color)
                self.table.setItem(row, col, item)
            self.table.item(row, 1).setData(Qt.ItemDataRole.UserRole, str(snap.id))
        self.table.renumber_rows()
        # Reaplica a ordenação salva e renumera depois dela, sem deixar a
        # própria coluna # participar da ordenação durante a atualização.
        self.table.setSortingEnabled(True)
        self.table.setSortingEnabled(False)
        self.table.renumber_rows()
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self._fit_snapshot_creation_column()
        self._fit_snapshot_columns()
        self.table.resizeRowsToContents()
        for row in range(self.table.rowCount()):
            if self.table.item(row, 1).data(Qt.ItemDataRole.UserRole) == selected_id:
                self.table.setCurrentCell(row, 1)
                break
        scrollbar = self.table.verticalScrollBar()
        if scroll_to_end:
            scrollbar.setValue(scrollbar.maximum())
            # Reaplica depois do layout para garantir o fim quando a nova
            # linha altera o tamanho total da tabela.
            QTimer.singleShot(0, lambda: scrollbar.setValue(scrollbar.maximum()))
        else:
            scrollbar.setValue(scroll)
        self._snapshot_selection_changed()

    # Handle snapshot selection rows.
    def _snapshot_selection_rows(self) -> list[int]:
        if not hasattr(self, "table"):
            return []
        return sorted({item.row() for item in self.table.selectedItems() if item.row() >= 0})

    # Handle snapshot selection changed.
    def _snapshot_selection_changed(self):
        if self._normalizing_snapshot_selection:
            return
        rows = self._snapshot_selection_rows()
        if len(rows) > 1:
            # A seleção inicial do Qt pode conter apenas uma célula por linha.
            # Completar as células mantém o destaque de cada linha e deixa o
            # número de itens selecionados inequívoco para as ações.
            self._normalizing_snapshot_selection = True
            try:
                for row in rows:
                    for column in range(self.table.columnCount()):
                        item = self.table.item(row, column)
                        if item is not None:
                            item.setSelected(True)
            finally:
                self._normalizing_snapshot_selection = False
        count = len(rows)
        blocked = bool(getattr(self, "_update_install_pending", False))
        enabled = count > 0 and not blocked
        self.overwrite_button.setEnabled(enabled)
        self.restore_button.setEnabled(enabled)
        self.trash_button.setEnabled(enabled)
        self.create_button.setEnabled(self.current_profile is not None and not blocked)
        self.refresh_button.setEnabled(self.current_profile is not None and not blocked and not self._manual_refresh_active and not self._manual_refresh_pending)
        self.open_backups_button.setEnabled(self.current_profile is not None and not blocked)
        self.open_source_button.setEnabled(self._source_is_configured())
        delete_label = self.i18n.tr("main.delete")
        self.trash_button.setText(f"{delete_label} ({count})" if count > 1 else delete_label)

    # Refresh action states.
    def _refresh_action_states(self):
        self._snapshot_selection_changed()
        blocked = (
            (self.operation_active and not self._manual_refresh_active)
            or self._update_install_pending
        )
        self.auto_save_button.setEnabled(self.current_game is not None and not blocked)
        self.profile_settings_button.setEnabled(self.current_profile is not None and not blocked)
        self.delete_profile_button.setEnabled(self.current_profile is not None and not blocked)

    # Check source is configured.
    def _source_is_configured(self) -> bool:
        if not self.current_game:
            return False
        if self.db.get_game_settings(self.current_game.id):
            return True
        return bool(self.db.list_profiles(self.current_game.id, True))

    # Handle selected snapshots.
    def _selected_snapshots(self):
        if not self.current_profile:
            return []
        snapshots = []
        for row in self._snapshot_selection_rows():
            item = self.table.item(row, 1)
            if not item:
                continue
            try:
                snapshot = self.db.get_snapshot(UUID(str(item.data(Qt.ItemDataRole.UserRole))))
            except (TypeError, ValueError):
                snapshot = None
            if snapshot:
                snapshots.append(snapshot)
        return snapshots

    # Format local datetime.
    def _format_local_datetime(self, value: str) -> str:
        """Display the catalog instant in the local timezone and active locale."""
        try:
            instant = datetime.fromisoformat(value)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=datetime.now().astimezone().tzinfo)
            date_format = "%m/%d/%Y %H:%M" if self.i18n.language == "en-US" else "%d/%m/%Y %H:%M"
            return instant.astimezone().strftime(date_format)
        except (TypeError, ValueError, OverflowError):
            return str(value).replace("T", " ")[:16]

    # Handle snapshot item changed.
    def _snapshot_item_changed(self, item: QTableWidgetItem):
        if item.column() not in (1, 2) or not self.current_profile:
            return
        id_item = self.table.item(item.row(), 1)
        if not id_item:
            return
        snapshot = None
        try:
            snapshot = self.db.get_snapshot(UUID(str(id_item.data(Qt.ItemDataRole.UserRole))))
            if not snapshot:
                return
            if item.column() == 1:
                new_name, new_notes = item.text(), snapshot.notes
                if new_name == snapshot.name:
                    return
            else:
                new_name, new_notes = snapshot.name, item.text()
                if new_notes == snapshot.notes:
                    return
            self.maintenance.rename_snapshot(snapshot.id, new_name, notes=new_notes)
            self.table.resizeRowsToContents()
        except Exception as exc:
            self.table.blockSignals(True)
            if snapshot:
                item.setText(snapshot.name if item.column() == 1 else snapshot.notes)
            else:
                item.setText("")
            self.table.blockSignals(False)
            title = self.i18n.tr("dialog.cannot_rename") if item.column() == 1 else self.i18n.tr("dialog.cannot_edit_description")
            QMessageBox.warning(self, title, str(exc))

    # Synchronize external changes.
    def _sync_external_changes(self):
        self.monitor.request()

    # Handle external changes synced.
    def _external_changes_synced(self, result):
        if getattr(result, "imported_snapshots", set()):
            self.audio.play("import")
        auto_save_changed = False
        if result.removed_profiles:
            for profile_id in result.removed_profiles:
                self.auto_saves.remove_profile(profile_id)
            auto_save_changed = True
        for profile_id in result.snapshots:
            for entry in self.auto_saves.list_all():
                if entry["target_type"] == "profile" or entry["profile_id"] != str(profile_id):
                    continue
                snapshot = self.db.get_snapshot(UUID(str(entry["snapshot_id"])))
                if (entry["enabled"] and (snapshot is None
                        or snapshot.status in (SnapshotStatus.MISSING, SnapshotStatus.TRASHED))):
                    self.auto_saves.set_enabled(str(entry["id"]), False)
                    auto_save_changed = True
        if auto_save_changed:
            self._reset_auto_save_schedule()
            self._retranslate_menu()
        removed_current = bool(self.current_profile and self.current_profile.id in result.removed_profiles)
        if removed_current:
            self.current_profile = None
            self.db.set_setting("selected_profile", None)
            self.profiles.hidePopup()
        if (result.profiles or auto_save_changed) and self.current_game:
            self._refresh_games(self.current_game.id)
        if removed_current:
            return
        if self.current_profile:
            if self.current_profile.id in result.snapshots:
                self._refresh_snapshots()
            availability = result.availability.get(self.current_profile.id)
            if availability:
                try:
                    paths = self.policy.resolve(self.current_profile, game_name=self.current_game.name if self.current_game else None, validate_origin=False)
                except Exception as exc:
                    self.path_label.setText(str(exc))
                    return
                source_state = "" if availability[0] else self.i18n.tr("status.source_unavailable")
                backup_state = "" if availability[1] else self.i18n.tr("status.source_unavailable")
                self.path_label.setText(f"{self.i18n.tr('main.source', path=paths.source)}{source_state}\n{self.i18n.tr('main.backups', path=paths.namespace)}{backup_state}")

    # Defer during sync.
    def _defer_during_sync(self, callback):
        if self.operation_active:
            return True
        if self.monitor.running:
            QTimer.singleShot(50, callback)
            return True
        return False

    # Add game.
    def _add_game(self):
        if self._defer_during_sync(self._add_game): return
        if self._update_install_pending: return
        from uuid import uuid4
        validation_id = uuid4()
        dialog = GameSettingsDialog(self, translator=self.i18n, validate=lambda settings: self.catalog.game_settings.validate(validation_id, settings))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                game = self.catalog.create_game(dialog.name.text(), dialog.settings())
                self._refresh_games(game.id)
                self.monitor.request()
            except Exception as exc: QMessageBox.warning(self, self.i18n.tr("dialog.unable_title"), str(exc))

    # Edit game settings.
    def _edit_game_settings(self):
        if self._defer_during_sync(self._edit_game_settings): return
        if self._update_install_pending or not self.current_game or self.recovery_required: return
        game = self.current_game
        old = self.db.get_game_settings(game.id)
        profiles = self.db.list_profiles(game.id, True)
        initial = old
        if initial is None and profiles:
            first = profiles[0]
            initial = GameSettings(first.source_type, first.source_path, first.storage_mode, first.backup_base_path)
        if initial and initial.storage_mode == StorageMode.APP:
            # Catálogos anteriores usavam a raiz interna do app. Exibi-los
            # como destino personalizado permite migrar para a nova estrutura.
            initial = GameSettings(initial.source_type, initial.source_path, StorageMode.CUSTOM, str(self.policy.app_backup_root))
        dialog = GameSettingsDialog(self, game_name=game.name, settings=initial, translator=self.i18n,
                                    validate=lambda settings: self.catalog.game_settings.validate(game.id, settings),
                                    validate_name=lambda name: self.catalog.game_name_exists(name, excluding=game.id),
                                    conflicting=old is None and bool(profiles))
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        settings = dialog.settings()
        new_game_name = dialog.name.text().strip()
        game_name_changed = new_game_name != game.name
        settings_changed = old != settings
        destination_migration_needed = self.catalog.game_settings.destination_migration_required(game.id, settings)
        if not settings_changed and not game_name_changed and not destination_migration_needed: return
        copy_backups = True
        move_backups = False
        transfer_action = None
        if (settings_changed or destination_migration_needed) and profiles:
            destination_changed = destination_migration_needed or any(
                self._backup_destination_changed(previous_profile, settings)
                for previous_profile in ([old] if old else profiles)
            )
            if destination_changed:
                message = self.i18n.tr("dialog.destination_change_message", count=len(profiles), name=game.name, path=settings.source_path)
                prompt = QMessageBox(self)
                prompt.setIcon(QMessageBox.Icon.Question)
                prompt.setWindowTitle(self.i18n.tr("dialog.destination_change_title"))
                prompt.setText(message)
                copy_button = prompt.addButton(
                    self.i18n.tr("dialog.destination_copy"), QMessageBox.ButtonRole.ActionRole
                )
                move_button = prompt.addButton(
                    self.i18n.tr("dialog.destination_move"), QMessageBox.ButtonRole.ActionRole
                )
                cancel_button = prompt.addButton(
                    self.i18n.tr("dialog.destination_cancel"), QMessageBox.ButtonRole.RejectRole
                )
                prompt.setDefaultButton(copy_button)
                prompt.setEscapeButton(cancel_button)
                equalize_button_widths(prompt.buttons())
                prompt.exec()
                answer = prompt.clickedButton()
                if answer is cancel_button or answer not in (copy_button, move_button):
                    return
                transfer_action = "move" if answer is move_button else "copy"
                move_backups = transfer_action == "move"
            else:
                message = self.i18n.tr("dialog.reconfigure_message", count=len(profiles), name=game.name, path=settings.source_path)
                if QMessageBox.question(
                    self,
                    self.i18n.tr("dialog.reconfigure_title"),
                    message,
                    QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                    QMessageBox.StandardButton.Cancel,
                ) != QMessageBox.StandardButton.Yes:
                    return
        try:
            self._unwatch_project_backup_paths(game)
        except Exception as exc:
            QMessageBox.warning(self, self.i18n.tr("dialog.unable_title"), str(exc))
            return
        if settings_changed or destination_migration_needed:
            def apply_game_settings(progress=None):
                result = self.catalog.configure_game(
                    game.id,
                    settings,
                    copy_backups=copy_backups,
                    move_backups=move_backups,
                    progress=progress,
                )
                if game_name_changed:
                    self.catalog.rename_game(game.id, new_game_name)
                return result

            worker = ServiceWorker(apply_game_settings)
        else:
            worker = ServiceWorker(self.catalog.rename_game, game.id, new_game_name, with_progress=False)
        self.active_worker = worker
        self.operation_active = True
        self.status_label.setText(self.i18n.tr("status.configure_game"))
        self._game_settings_progress_dialog = None
        if transfer_action:
            progress_dialog = QProgressDialog(self)
            progress_dialog.setWindowTitle(self.i18n.tr("dialog.destination_progress_title"))
            progress_dialog.setLabelText(self.i18n.tr("dialog.destination_progress_message"))
            progress_dialog.setRange(0, 100)
            progress_dialog.setValue(0)
            progress_dialog.setCancelButton(None)
            progress_dialog.setMinimumDuration(0)
            progress_dialog.setAutoClose(False)
            progress_dialog.setAutoReset(False)
            progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            progress_bar = progress_dialog.findChild(QProgressBar)
            if progress_bar:
                progress_bar.setFormat("%p%")
            self._game_settings_progress_dialog = progress_dialog
            progress_dialog.show()

        # Update transfer progress.
        def update_game_settings_progress(item):
            self.status_label.setText(self.i18n.tr("status.copying_backup", path=item.relative))
            progress_dialog = self._game_settings_progress_dialog
            if progress_dialog is not None and hasattr(item, "percent"):
                progress_dialog.setValue(item.percent)
                if item.relative:
                    progress_dialog.setLabelText(
                        self.i18n.tr("dialog.destination_progress_file", path=item.relative)
                    )

        worker.signals.progress.connect(update_game_settings_progress)
        worker.signals.finished.connect(self._game_settings_saved)
        worker.signals.error.connect(self._game_settings_failed)
        self.thread_pool.start(worker)

    @staticmethod
    # Handle backup destination changed.
    def _backup_destination_changed(previous, settings) -> bool:
        """Compara o destino efetivo, inclusive o modo ao lado da origem."""
        if previous.storage_mode != settings.storage_mode:
            return True
        if settings.storage_mode == StorageMode.BESIDE_SOURCE:
            old_parent = Path(previous.source_path).absolute().parent
            new_parent = Path(settings.source_path).absolute().parent
            return str(old_parent).casefold() != str(new_parent).casefold()
        old_base = Path(previous.backup_base_path or "").absolute()
        new_base = Path(settings.backup_base_path or "").absolute()
        return str(old_base).casefold() != str(new_base).casefold()

    # Handle game settings saved.
    def _game_settings_saved(self, result):
        progress_dialog = getattr(self, "_game_settings_progress_dialog", None)
        self._game_settings_progress_dialog = None
        if progress_dialog is not None:
            progress_dialog.setValue(100)
            progress_dialog.close()
        self._refresh_games()
        self.monitor.request()
        self._operation_finished(self.i18n.tr("status.settings_applied"))
        transfer_action = getattr(result, "transfer_action", None)
        if transfer_action == "move" and not getattr(result, "transfer_complete", False):
            QMessageBox.warning(
                self,
                self.i18n.tr("dialog.destination_complete_title"),
                self.i18n.tr("dialog.destination_move_partial"),
            )
        elif transfer_action and not getattr(result, "files_transferred", False):
            QMessageBox.information(
                self,
                self.i18n.tr("dialog.destination_complete_title"),
                self.i18n.tr("dialog.destination_already_available"),
            )
        elif transfer_action == "copy":
            QMessageBox.information(
                self,
                self.i18n.tr("dialog.destination_complete_title"),
                self.i18n.tr("dialog.destination_copy_complete"),
            )
        elif transfer_action == "move":
            QMessageBox.information(
                self,
                self.i18n.tr("dialog.destination_complete_title"),
                self.i18n.tr("dialog.destination_move_complete"),
            )

    # Handle game settings failure.
    def _game_settings_failed(self, message):
        progress_dialog = getattr(self, "_game_settings_progress_dialog", None)
        self._game_settings_progress_dialog = None
        if progress_dialog is not None:
            progress_dialog.close()
        self._operation_finished(self.i18n.tr("status.failure"))
        self.monitor.request()
        QMessageBox.warning(self, self.i18n.tr("dialog.unable_title"), message)

    # Show project Auto-Save settings.
    def _show_auto_save_settings(self):
        if self._defer_during_sync(self._show_auto_save_settings):
            return
        if self._update_install_pending or not self.current_game:
            return
        game = self.current_game
        entries = self.auto_saves.list_project(game.id)
        names = {}
        for entry in entries:
            if entry["target_type"] == "profile":
                target = self.db.get_profile(UUID(str(entry["profile_id"])))
            else:
                target = self.db.get_snapshot(UUID(str(entry["snapshot_id"])))
            names[str(entry["id"])] = target.name if target else str(entry.get("target_name", ""))
        dialog = ProjectAutoSaveDialog(
            self,
            project_name=game.name,
            entries=entries,
            names=names,
            translator=self.i18n,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.auto_saves.update_project(game.id, dialog.values())
            self._auto_save_configuration_changed(game.id)
        except Exception as exc:
            QMessageBox.warning(self, self.i18n.tr("autosave.error_title"), str(exc))

    # Toggle snapshot Auto-Save.
    def _toggle_snapshot_auto_save(self, snapshot_id: UUID):
        if self._defer_during_sync(lambda: self._toggle_snapshot_auto_save(snapshot_id)):
            return
        if self.operation_active or self._update_install_pending:
            return
        entry = self.auto_saves.for_snapshot(snapshot_id)
        if entry:
            snapshot = self.db.get_snapshot(snapshot_id)
            display_name = snapshot.name if snapshot else str(entry.get("target_name", ""))
            if not self._confirm_auto_save_removal(display_name):
                return
            self.auto_saves.remove(str(entry["id"]))
            project_id = UUID(str(entry["project_id"]))
        else:
            dialog = AutoSaveIntervalDialog(self, translator=self.i18n)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            entry = self.auto_saves.add_snapshot(snapshot_id, dialog.interval.value())
            project_id = UUID(str(entry["project_id"]))
        self._auto_save_configuration_changed(project_id)

    # Confirm Auto-Save removal.
    def _confirm_auto_save_removal(self, name: str) -> bool:
        return ask_question(
            self,
            self.i18n.tr,
            self.i18n.tr("autosave.remove_title"),
            self.i18n.tr("autosave.remove_question", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Yes

    # Show profile settings.
    def _show_profile_settings(self):
        if self._defer_during_sync(self._show_profile_settings):
            return
        if self._update_install_pending or not self.current_game or not self.current_profile:
            return
        profile = self.current_profile
        try:
            paths = self.policy.resolve(profile, str(profile.game_id), game_name=self.current_game.name, validate_origin=False)
        except Exception as exc:
            QMessageBox.warning(self, self.i18n.tr("dialog.unable_title"), str(exc))
            return
        dialog = ProfileSettingsDialog(
            self,
            profile=profile,
            source_path=str(paths.source),
            backup_path=str(paths.namespace),
            auto_save=self.auto_saves.for_profile(profile.id),
            translator=self.i18n,
        )

        # Enable or update profile schedule.
        def enable_auto_save(interval_seconds):
            try:
                entry = self.auto_saves.configure_profile(profile.id, interval_seconds, enabled=True)
                dialog.set_auto_save(entry)
                self._auto_save_configuration_changed(profile.game_id)
            except Exception as exc:
                QMessageBox.warning(dialog, self.i18n.tr("autosave.error_title"), str(exc))

        # Remove profile schedule.
        def remove_auto_save():
            entry = self.auto_saves.for_profile(profile.id)
            if entry:
                self.auto_saves.remove(str(entry["id"]))
            dialog.set_auto_save(None)
            self._auto_save_configuration_changed(profile.game_id)

        dialog.auto_save_enable_requested.connect(enable_auto_save)
        dialog.auto_save_remove_requested.connect(remove_auto_save)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.name.text().strip() != profile.name:
            try:
                self.catalog.rename_profile(profile.id, dialog.name.text())
            except Exception as exc:
                QMessageBox.warning(self, self.i18n.tr("dialog.cannot_save"), str(exc))
                return
        self._game_changed(self.games.currentRow())
        for index in range(self.profiles.count()):
            if str(self.profiles.itemData(index)) == str(profile.id):
                self.profiles.setCurrentIndex(index)
                break

    # Refresh Auto-Save indicators.
    def _auto_save_configuration_changed(self, project_id: UUID):
        self._reset_auto_save_schedule()
        self._retranslate_menu()
        self._refresh_games(project_id)

    # Reset Auto-Save timers.
    def _reset_auto_save_schedule(self):
        self._auto_save_deadlines = {}
        self._sync_auto_save_timer(reset=True)

    # Sync active Auto-Save timer.
    def _sync_auto_save_timer(self, *, reset=False):
        timer = getattr(self, "auto_save_timer", None)
        if timer is None:
            return
        entries = self.auto_saves.list_enabled() if self._auto_save_global_enabled else []
        if not entries:
            self._auto_save_deadlines.clear()
            timer.stop()
            return
        now = time.monotonic()
        active_ids = {str(entry["id"]) for entry in entries}
        self._auto_save_deadlines = {
            entry_id: deadline
            for entry_id, deadline in self._auto_save_deadlines.items()
            if entry_id in active_ids
        }
        for entry in entries:
            entry_id = str(entry["id"])
            if reset or entry_id not in self._auto_save_deadlines:
                self._auto_save_deadlines[entry_id] = now + int(entry["interval_seconds"])
        if not self.operation_active:
            timer.start()

    # Run due Auto-Save.
    def _poll_auto_saves(self):
        if not self._auto_save_global_enabled:
            self._auto_save_deadlines.clear()
            self.auto_save_timer.stop()
            return
        entries = self.auto_saves.list_enabled()
        if not entries:
            self._auto_save_deadlines.clear()
            self.auto_save_timer.stop()
            return
        if (self.operation_active or self._update_install_pending or self.recovery_required
                or self.monitor.running or QApplication.activeModalWidget() is not None):
            return
        active_ids = {str(entry["id"]) for entry in entries}
        self._auto_save_deadlines = {
            entry_id: due for entry_id, due in self._auto_save_deadlines.items() if entry_id in active_ids
        }
        now = time.monotonic()
        for entry in entries:
            entry_id = str(entry["id"])
            due = self._auto_save_deadlines.setdefault(
                entry_id,
                now + int(entry["interval_seconds"]),
            )
            if now < due:
                continue
            self._auto_save_deadlines[entry_id] = now + int(entry["interval_seconds"])
            if self._start_auto_save(entry):
                return

    # Start one Auto-Save.
    def _start_auto_save(self, entry) -> bool:
        if not self._auto_save_global_enabled or not entry.get("enabled"):
            return False
        try:
            project_id = UUID(str(entry["project_id"]))
            profile_id = UUID(str(entry["profile_id"]))
        except (TypeError, ValueError):
            logging.getLogger("save_manager").warning("Ignoring invalid Auto-Save target %s", entry.get("id"))
            return False
        project = self.db.get_game(project_id)
        profile = self.db.get_profile(profile_id)
        if (not project or project.archived_at or not profile or profile.archived_at
                or profile.game_id != project_id):
            self.auto_saves.set_enabled(str(entry["id"]), False)
            self._sync_auto_save_timer()
            self._retranslate_menu()
            self._refresh_games(project_id)
            return False
        if entry["target_type"] == "profile":
            name = self._next_auto_save_name(profile_id)
            operation = lambda: self.snapshots.create(profile_id, name)
            complete_status = "status.auto_save_created"
        else:
            try:
                snapshot_id = UUID(str(entry["snapshot_id"]))
            except (TypeError, ValueError):
                self.auto_saves.set_enabled(str(entry["id"]), False)
                self._sync_auto_save_timer()
                self._retranslate_menu()
                return False
            snapshot = self.db.get_snapshot(snapshot_id)
            if (not snapshot or snapshot.profile_id != profile_id
                    or snapshot.status in (SnapshotStatus.MISSING, SnapshotStatus.TRASHED)):
                self.auto_saves.set_enabled(str(entry["id"]), False)
                self._sync_auto_save_timer()
                self._retranslate_menu()
                self._refresh_games(project_id)
                return False
            name = snapshot.name
            operation = lambda: self.snapshots.overwrite(profile_id, snapshot_id)
            complete_status = "status.auto_save_overwritten"
        worker = ServiceWorker(operation, with_progress=False)
        self.auto_save_timer.stop()
        self.active_worker = worker
        self.operation_active = True
        self.cancel_button.setVisible(False)
        self.status_label.setText(self.i18n.tr("status.auto_saving"))
        self._refresh_action_states()
        worker.signals.finished.connect(
            lambda result, record=dict(entry), save_name=name, status_key=complete_status, target_profile=profile_id:
                self._auto_save_finished(result, record, target_profile, save_name, status_key)
        )
        worker.signals.error.connect(lambda message, record=dict(entry): self._auto_save_failed(record, message))
        self.thread_pool.start(worker)
        return True

    # Finish an Auto-Save.
    def _auto_save_finished(self, result, entry, profile_id, name, status_key):
        self._auto_save_deadlines[str(entry["id"])] = (
            time.monotonic() + int(entry["interval_seconds"])
        )
        if self.current_profile and self.current_profile.id == profile_id:
            self._refresh_snapshots()
        if bool(self._app_settings.get("sound_effect_auto_save_enabled", False)):
            self.audio.play("auto_save")
        self._operation_finished(self.i18n.tr(status_key, name=name))

    # Log Auto-Save failure.
    def _auto_save_failed(self, entry, message):
        self._auto_save_deadlines[str(entry["id"])] = (
            time.monotonic() + int(entry["interval_seconds"])
        )
        logging.getLogger("save_manager").warning(
            "Auto-Save %s failed: %s",
            entry.get("id", "unknown"),
            message,
        )
        self._operation_finished(self.i18n.tr("status.auto_save_failed"))

    # Find next Auto-Save name.
    def _next_auto_save_name(self, profile_id: UUID) -> str:
        existing = {snapshot.name.casefold() for snapshot in self.db.list_snapshots(profile_id)}
        number = 1
        while f"AutoSave_{number:02d}".casefold() in existing:
            number += 1
        return f"AutoSave_{number:02d}"

    # Show game menu.
    def _show_game_menu(self, position):
        item = self.games.itemAt(position)
        if item is None:
            return
        self.games.setCurrentItem(item)
        menu = QMenu(self)
        settings_action = menu.addAction(self.i18n.tr("context.game_settings"))
        settings_action.triggered.connect(self._edit_game_settings)
        delete_action = menu.addAction(self.i18n.tr("context.delete_game"))
        delete_action.triggered.connect(self._delete_game)
        menu.exec(self.games.viewport().mapToGlobal(position))

    # Show snapshot menu.
    def _show_snapshot_menu(self, position):
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        if index.row() not in self._snapshot_selection_rows():
            self.table.setCurrentCell(index.row(), index.column())
        count = len(self._snapshot_selection_rows())
        if count == 0:
            return
        menu = QMenu(self.table)
        if index.column() == 1:
            rename = menu.addAction(self.i18n.tr("context.rename"))
            rename.setEnabled(count == 1)
            rename.triggered.connect(lambda: self._edit_snapshot_name(index.row()))
        elif index.column() == 2:
            edit_description = menu.addAction(self.i18n.tr("context.edit_description"))
            edit_description.setEnabled(count == 1)
            edit_description.triggered.connect(lambda: self._edit_snapshot_cell(index.row(), 2))
        try:
            snapshot_id = UUID(str(self.table.item(index.row(), 1).data(Qt.ItemDataRole.UserRole)))
        except (AttributeError, TypeError, ValueError):
            snapshot_id = None
        if snapshot_id is not None:
            schedule = self.auto_saves.for_snapshot(snapshot_id)
            auto_save_label = "context.remove_auto_save" if schedule else "context.auto_save"
            auto_save_action = menu.addAction(self.i18n.tr(auto_save_label))
            auto_save_action.triggered.connect(
                lambda _checked=False, target_id=snapshot_id: self._toggle_snapshot_auto_save(target_id)
            )
        overwrite = menu.addAction(self.i18n.tr("main.overwrite_save"))
        overwrite.setEnabled(count == 1)
        overwrite.triggered.connect(lambda: self._overwrite_snapshot())
        restore = menu.addAction(self.i18n.tr("main.load_save"))
        restore.setEnabled(count == 1)
        restore.triggered.connect(lambda: self._restore_snapshot())
        delete_label = self.i18n.tr("main.delete")
        delete = menu.addAction(delete_label if count == 1 else f"{delete_label} ({count})")
        delete.triggered.connect(lambda: self._trash_snapshot())
        self._snapshot_context_menu = menu
        menu.exec(self.table.viewport().mapToGlobal(position))

    # Edit snapshot name.
    def _edit_snapshot_name(self, row: int):
        self._edit_snapshot_cell(row, 1)

    # Edit snapshot cell.
    def _edit_snapshot_cell(self, row: int, column: int):
        if len(self._snapshot_selection_rows()) != 1:
            return
        item = self.table.item(row, column)
        if item is None:
            return
        self.table.setCurrentCell(row, column)
        self.table.editItem(item)

    # Delete game.
    def _delete_game(self):
        if self._defer_during_sync(self._delete_game): return
        if not self.current_game:
            return
        game = self.current_game
        dialog = QDialog(self)
        dialog.setWindowTitle(self.i18n.tr("dialog.delete_game_title"))
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        message = QLabel(self.i18n.tr("dialog.delete_game_question", name=game.name), dialog)
        message.setWordWrap(True)
        layout.addWidget(message)
        prompt = QLabel(self.i18n.tr("dialog.delete_game_prompt"), dialog)
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        confirmation = QLineEdit(dialog)
        confirmation.setPlaceholderText("delete")
        confirmation.setToolTip(self.i18n.tr("dialog.delete_confirmation_tooltip"))
        layout.addWidget(confirmation)
        buttons = QDialogButtonBox(dialog)
        delete_button = buttons.addButton(self.i18n.tr("common.delete"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.i18n.tr("common.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        delete_button.setEnabled(False)
        confirmation.textChanged.connect(lambda text: delete_button.setEnabled(text == "delete"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        equalize_button_widths(buttons.buttons())
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or confirmation.text() != "delete":
            return
        try:
            self._unwatch_project_backup_paths(game)
            self.catalog.delete_game(game.id)
            self.auto_saves.remove_project(game.id)
            self._reset_auto_save_schedule()
            self._retranslate_menu()
            self.current_game = None
            self.current_profile = None
            self.db.set_setting("selected_game", None)
            self.db.set_setting("selected_profile", None)
            self._refresh_games()
            self.monitor.request()
        except Exception as exc:
            self.monitor.request()
            QMessageBox.warning(self, self.i18n.tr("dialog.cannot_delete_game"), str(exc))

    # Unwatch project backups before deletion.
    def _unwatch_project_backup_paths(self, game):
        for profile in self.db.list_profiles(game.id, True):
            paths = self.policy.resolve(
                profile,
                str(game.id),
                game_name=game.name,
                validate_origin=False,
            )
            managed_root = paths.namespace.parent
            if managed_root == paths.backup_base:
                managed_root = paths.namespace
            self.monitor.remove_paths_under(managed_root)

    # Add profile.
    def _add_profile(self):
        if self._defer_during_sync(self._add_profile): return
        if self._update_install_pending: return
        if not self.current_game: return
        settings = self.db.get_game_settings(self.current_game.id)
        if not settings:
            QMessageBox.information(self, self.i18n.tr("dialog.cannot_configure"), self.i18n.tr("dialog.configure_message"))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(self.i18n.tr("dialog.new_profile"))
        dialog.setMinimumWidth(520)
        dialog.resize(620, 230)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        name = QLineEdit()
        name.setPlaceholderText(self.i18n.tr("dialog.profile_placeholder"))
        name.setMinimumHeight(30)
        form.addRow(self.i18n.tr("dialog.profile_name_label"), name)
        explanation = QLabel(self.i18n.tr("dialog.profile_explanation"))
        explanation.setWordWrap(True)
        form.addRow(explanation)

        preview_profile = Profile(self.current_game.id, "Perfil", settings.source_type, settings.source_path, settings.storage_mode, settings.backup_base_path)
        base = self.policy.resolve_backup_base(preview_profile)
        destination = self.policy.namespace_parent(preview_profile, base, self.current_game.name, str(self.current_game.id))

        source_value = QLabel(str(settings.source_path))
        source_value.setWordWrap(True)
        source_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        source_value.setStyleSheet("color: #b9bec8;")
        form.addRow(self.i18n.tr("dialog.shared_source_label"), source_value)

        destination_value = QLabel(str(destination))
        destination_value.setWordWrap(True)
        destination_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        destination_value.setStyleSheet("color: #b9bec8;")
        form.addRow(self.i18n.tr("dialog.profiles_folder_label"), destination_value)
        layout.addLayout(form)
        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                created = self.catalog.create_profile(self.current_game.id, name.text())
                self._game_changed(self.games.currentRow())
                for index in range(self.profiles.count()):
                    if str(self.profiles.itemData(index)) == str(created.id):
                        self.profiles.setCurrentIndex(index)
                        break
            except Exception as exc: QMessageBox.warning(self, self.i18n.tr("dialog.cannot_save"), str(exc))

    # Delete profile and backups.
    def _delete_profile(self):
        if self._defer_during_sync(self._delete_profile):
            return
        if self.operation_active or self._update_install_pending or not self.current_profile:
            return
        profile = self.current_profile
        answer = QMessageBox.question(
            self,
            self.i18n.tr("dialog.delete_profile_title"),
            self.i18n.tr("dialog.delete_profile", name=profile.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            game = self.current_game
            namespace = self.policy.resolve(
                profile,
                str(profile.game_id),
                game_name=game.name if game else None,
                validate_origin=False,
            ).namespace
            self.monitor.remove_paths_under(namespace)
            self.catalog.delete_profile(profile.id)
            self.auto_saves.remove_profile(profile.id)
            self._reset_auto_save_schedule()
            self._retranslate_menu()
            self.current_profile = None
            self.db.set_setting("selected_profile", None)
            self._game_changed(self.games.currentRow())
            self.monitor.request()
        except Exception as exc:
            self.monitor.request()
            QMessageBox.warning(self, self.i18n.tr("dialog.cannot_delete_profile"), str(exc))

    # Create snapshot.
    def _create_snapshot(self):
        if self._defer_during_sync(self._create_snapshot): return
        if self.operation_active or self._update_install_pending:
            return
        if self.recovery_required:
            QMessageBox.warning(self, self.i18n.tr("dialog.recovery_title"), self.i18n.tr("dialog.recovery_create_short")); return
        if not self.current_profile: return
        self._start_snapshot_creation(self._next_hotkey_snapshot_name())

    # Start snapshot creation.
    def _start_snapshot_creation(self, name: str):
        """Start a snapshot worker after the caller has chosen its name."""
        if not self.current_profile:
            return
        worker = ServiceWorker(self.snapshots.create, self.current_profile.id, name, cancellable=True)
        self.active_worker = worker
        self.operation_active = True; self.cancel_button.setVisible(True); self.status_label.setText(self.i18n.tr("status.creating")); worker.signals.progress.connect(lambda item: self.status_label.setText(self.i18n.tr("status.copying", path=getattr(item, 'relative', ''))))
        worker.signals.finished.connect(lambda _result: self._refresh_snapshots(scroll_to_end=True))
        worker.signals.finished.connect(lambda _result: self.audio.play("import"))
        worker.signals.finished.connect(lambda _result, snapshot_name=name: self._operation_finished(self.i18n.tr("status.save_created", name=snapshot_name)))
        worker.signals.error.connect(lambda message: (self._operation_finished(self.i18n.tr("status.failure")), QMessageBox.warning(self, self.i18n.tr("dialog.unable_title"), message)))
        self.thread_pool.start(worker)

    # Find next hotkey snapshot name.
    def _next_hotkey_snapshot_name(self) -> str:
        """Return the first unused automatic name for a global create hotkey."""
        if not self.current_profile:
            return "save_01"
        snapshots = self.db.list_snapshots(self.current_profile.id)
        existing = {snapshot.name.casefold() for snapshot in snapshots}
        number = len(snapshots) + 1
        while f"save_{number:02d}".casefold() in existing:
            number += 1
        return f"save_{number:02d}"

    # Create snapshot automatic.
    def _create_snapshot_automatic(self):
        """Create a save from the global hotkey without opening a name prompt."""
        self._create_snapshot()

    # Overwrite snapshot.
    def _overwrite_snapshot(self):
        if self._defer_during_sync(self._overwrite_snapshot): return
        if self.operation_active or self._update_install_pending:
            return
        if self.recovery_required:
            QMessageBox.warning(self, self.i18n.tr("dialog.recovery_title"), self.i18n.tr("dialog.recovery_overwrite_short")); return
        if not self.current_profile:
            QMessageBox.information(self, self.i18n.tr("dialog.no_save_title"), self.i18n.tr("dialog.select_save_overwrite")); return
        if len(self._snapshot_selection_rows()) > 1:
            QMessageBox.information(self, self.i18n.tr("dialog.multiple_saves_title"), self.i18n.tr("dialog.single_save_overwrite")); return
        snap = self._selected_snapshot()
        if not snap:
            QMessageBox.information(self, self.i18n.tr("dialog.no_save_title"), self.i18n.tr("dialog.select_save_overwrite")); return
        question = self.i18n.tr("dialog.confirm_overwrite", name=snap.name, path=self.current_profile.source_path)
        if QMessageBox.question(self, self.i18n.tr("dialog.confirm_overwrite_title"), question, QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            return
        worker = ServiceWorker(self.snapshots.overwrite, self.current_profile.id, snap.id, cancellable=True)
        self.active_worker = worker
        self.operation_active = True; self.cancel_button.setVisible(True); self.status_label.setText(self.i18n.tr("status.overwriting"))
        worker.signals.progress.connect(lambda item: self.status_label.setText(self.i18n.tr("status.copying", path=getattr(item, 'relative', ''))))
        worker.signals.finished.connect(lambda _result: self._refresh_snapshots())
        worker.signals.finished.connect(lambda _result: self.audio.play("import"))
        worker.signals.finished.connect(lambda _result, snapshot_name=snap.name: self._operation_finished(self.i18n.tr("status.save_overwritten", name=snapshot_name)))
        worker.signals.error.connect(lambda message: (self._operation_finished(self.i18n.tr("status.failure")), QMessageBox.warning(self, self.i18n.tr("dialog.unable_title"), message)))
        self.thread_pool.start(worker)

    # Handle selected snapshot.
    def _selected_snapshot(self):
        rows = self._snapshot_selection_rows()
        if len(rows) != 1 or not self.current_profile:
            return None
        row = rows[0]
        item = self.table.item(row, 1)
        if not item:
            return None
        try:
            return self.db.get_snapshot(UUID(str(item.data(Qt.ItemDataRole.UserRole))))
        except (TypeError, ValueError):
            return None

    # Restore snapshot.
    def _restore_snapshot(self):
        if self._defer_during_sync(self._restore_snapshot): return
        # Uma chamada adiada pode chegar enquanto a confirmação anterior
        # ainda estiver aberta; nesse caso não crie um segundo diálogo.
        if QApplication.activeModalWidget() is not None:
            return
        if self.operation_active or self._update_install_pending:
            return
        if self.recovery_required:
            QMessageBox.warning(self, self.i18n.tr("dialog.recovery_title"), self.i18n.tr("dialog.recovery_load_short")); return
        if len(self._snapshot_selection_rows()) > 1:
            QMessageBox.information(self, self.i18n.tr("dialog.multiple_saves_title"), self.i18n.tr("dialog.single_save_load")); return
        snap = self._selected_snapshot()
        if not snap:
            QMessageBox.information(self, self.i18n.tr("dialog.no_save_title"), self.i18n.tr("dialog.select_save_load")); return
        skip_confirmation = bool(self._app_settings.get("load_save_hotkey_without_confirmation", False))
        if not skip_confirmation and QMessageBox.question(self, self.i18n.tr("dialog.confirm"), self.i18n.tr("dialog.confirm_load", path=self.current_profile.source_path), QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes: return
        worker = ServiceWorker(self.restore_service.restore, self.current_profile.id, snap.id)
        self.active_worker = worker
        self.operation_active = True; self.cancel_button.setVisible(False); self.status_label.setText(self.i18n.tr("status.loading"))
        worker.signals.finished.connect(lambda _result: self._refresh_snapshots())
        worker.signals.finished.connect(lambda _result: self.audio.play("load"))
        worker.signals.finished.connect(lambda _result, snapshot_name=snap.name: self._operation_finished(self.i18n.tr("status.save_loaded", name=snapshot_name)))
        worker.signals.error.connect(lambda message: (self._operation_finished(self.i18n.tr("status.failure")), QMessageBox.warning(self, self.i18n.tr("dialog.unable_title"), message)))
        self.thread_pool.start(worker)

    # Handle operation finished.
    def _operation_finished(self, message: str):
        self.operation_active = False; self.active_worker = None; self.cancel_button.setVisible(False); self.status_label.setText(message)
        self._refresh_action_states()
        self._sync_auto_save_timer()
        self.monitor.request()

    # Cancel operation.
    def _cancel_operation(self):
        if self.active_worker is not None and getattr(self.active_worker, "cancellable", False):
            self.active_worker.cancel()
            self.status_label.setText(self.i18n.tr("status.cancelling"))

    # Open folder.
    def _open_folder(self, kind: str):
        try:
            current_game = getattr(self, "current_game", None)
            profile = self.current_profile
            if profile:
                paths = self.policy.resolve(profile, str(profile.game_id), game_name=current_game.name if current_game else None)
                target = paths.namespace if kind == "backup" else (paths.source.parent if profile.source_type == SourceType.FILE else paths.source)
            else:
                if not current_game:
                    return
                if kind == "backup":
                    return
                settings = self.db.get_game_settings(current_game.id)
                if settings:
                    source_path, source_type = settings.source_path, settings.source_type
                else:
                    profiles = self.db.list_profiles(current_game.id, True)
                    if not profiles:
                        return
                    source_path, source_type = profiles[0].source_path, profiles[0].source_type
                source = Path(source_path)
                target = source.parent if source_type == SourceType.FILE else source
            open_in_explorer(target)
        except Exception as exc:
            QMessageBox.warning(self, self.i18n.tr("dialog.cannot_open_folder"), str(exc))

    # Rename snapshot.
    def _rename_snapshot(self):
        if self._defer_during_sync(self._rename_snapshot): return
        snap = self._selected_snapshot()
        if not snap: return
        name, ok = QInputDialog.getText(self, self.i18n.tr("dialog.rename_save_title"), self.i18n.tr("dialog.name_label"), text=snap.name)
        if ok:
            try: self.maintenance.rename_snapshot(snap.id, name); self._refresh_snapshots()
            except Exception as exc: QMessageBox.warning(self, self.i18n.tr("dialog.cannot_rename"), str(exc))

    # Trash snapshot.
    def _trash_snapshot(self):
        if self._defer_during_sync(self._trash_snapshot): return
        if self.operation_active or self._update_install_pending:
            return
        snapshots = self._selected_snapshots()
        if not snapshots: return
        if len(snapshots) == 1:
            question = self.i18n.tr("dialog.delete_save_one")
        else:
            question = self.i18n.tr("dialog.delete_save_many", count=len(snapshots))
        if QMessageBox.question(
            self,
            self.i18n.tr("dialog.delete_save_title"),
            question,
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Yes:
            try:
                for snapshot in snapshots:
                    self.maintenance.trash_snapshot(snapshot.id)
                    self.auto_saves.remove_snapshot(snapshot.id)
                if self.current_game:
                    self._auto_save_configuration_changed(self.current_game.id)
                self._refresh_snapshots()
            except Exception as exc: QMessageBox.warning(self, self.i18n.tr("dialog.cannot_delete_save"), str(exc))


try:
    from PySide6.QtWidgets import QInputDialog
except ImportError:
    QInputDialog = object  # type: ignore[misc,assignment]
