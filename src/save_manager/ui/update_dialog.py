"""Janela modal usada para verificar e instalar atualizações."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..i18n import LanguageManager
from ..version import APP_VERSION
from .dialogs import equalize_button_widths


class UpdateDialog(QDialog):
    """Exibe o resultado da consulta e controla o download do pacote."""

    download_update_requested = Signal()
    cancel_update_requested = Signal()

    # Initialize the object.
    def __init__(self, parent=None, *, translator=None):
        super().__init__(parent)
        self.translator = translator or LanguageManager(parent=self)
        self.setWindowTitle(self.translator.tr("updates.title"))
        self.setMinimumWidth(500)
        self.resize(540, 390)

        root = QVBoxLayout(self)
        updates = QGroupBox()
        self.updates_group = updates
        updates_form = QFormLayout(updates)
        self.current_version = QLabel(APP_VERSION)
        self.current_version_label = QLabel()
        updates_form.addRow(self.current_version_label, self.current_version)

        update_actions = QHBoxLayout()
        self.cancel_update_button = QPushButton()
        self.cancel_update_button.setVisible(False)
        self.cancel_update_button.clicked.connect(self.cancel_update_requested.emit)
        update_actions.addWidget(self.cancel_update_button)
        updates_form.addRow(update_actions)

        self.update_status = QLabel()
        self._status_text = ""
        self.update_status.setWordWrap(True)
        self.status_label = QLabel()
        updates_form.addRow(self.status_label, self.update_status)

        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.setTextVisible(True)
        self.update_progress.setVisible(False)
        updates_form.addRow(self.update_progress)

        self.update_details = QWidget()
        details_layout = QVBoxLayout(self.update_details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self.update_notes = QTextEdit()
        self.update_notes.setReadOnly(True)
        self.update_notes.setMinimumHeight(120)
        self.update_notes.setPlaceholderText("")
        details_layout.addWidget(self.update_notes)
        details_actions = QHBoxLayout()
        self.download_install_button = QPushButton()
        self.download_install_button.setEnabled(False)
        self.download_install_button.clicked.connect(self.download_update_requested.emit)
        details_actions.addWidget(self.download_install_button)
        self.later_button = QPushButton()
        self.later_button.clicked.connect(self.reject)
        details_actions.addWidget(self.later_button)
        details_layout.addLayout(details_actions)
        self.update_details.setVisible(False)
        updates_form.addRow(self.update_details)
        root.addWidget(updates)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui(self.translator.language)

    # Set update status.
    def set_update_status(self, text: str) -> None:
        self._status_text = str(text)
        self.update_status.setText(self._status_text)

    # Set update checking.
    def set_update_checking(self, checking: bool) -> None:
        self.cancel_update_button.setVisible(checking)
        self.update_progress.setVisible(checking)
        self.buttons.setEnabled(not checking)
        if checking:
            self.update_details.setVisible(False)

    # Set update progress.
    def set_update_progress(self, completed: int, total: int) -> None:
        if total > 0:
            self.update_progress.setRange(0, total)
            self.update_progress.setValue(max(0, min(total, completed)))
            self.update_progress.setFormat(f"{completed / total:.0%}")
        else:
            self.update_progress.setRange(0, 0)
            self.update_progress.setFormat(self.translator.tr("updates.downloading"))

    # Show update available.
    def show_update_available(self, release) -> None:
        self.update_details.setVisible(True)
        self.update_notes.setPlainText(str(getattr(release, "notes", "") or self.translator.tr("updates.no_release_notes")))
        self._status_text = self.translator.tr(
            "updates.status_available",
            version=getattr(release, "version_text", release),
            current=APP_VERSION,
        )
        self.update_status.setText(self._status_text)
        self.download_install_button.setEnabled(True)
        self.later_button.setEnabled(True)

    # Handle retranslate ui.
    def retranslate_ui(self, _language: str | None = None) -> None:
        t = self.translator.tr
        self.setWindowTitle(t("updates.title"))
        self.updates_group.setTitle(t("updates.section"))
        self.current_version_label.setText(t("updates.current_version"))
        self.cancel_update_button.setText(t("updates.cancel"))
        self.cancel_update_button.setToolTip(t("updates.cancel_tooltip"))
        self.status_label.setText(t("updates.status_label"))
        self.update_notes.setPlaceholderText(t("updates.release_notes"))
        self.update_notes.setToolTip(t("updates.release_notes_tooltip"))
        self.download_install_button.setText(t("updates.download_install"))
        self.download_install_button.setToolTip(t("updates.download_install_tooltip"))
        self.later_button.setText(t("common.later"))
        self.later_button.setToolTip(t("updates.later_tooltip"))
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText(t("common.close"))
        equalize_button_widths([
            self.cancel_update_button,
            self.download_install_button,
            self.later_button,
            *self.buttons.buttons(),
        ])
        if not self._status_text:
            self._status_text = t("updates.status_not_checked")
        else:
            # Statuses are supplied by MainWindow as rendered strings. Match
            # them against both catalogs so changing the language while this
            # dialog is open also updates an in-flight operation.
            status_keys = (
                "updates.status_not_checked", "updates.status_checking",
                "updates.status_latest", "updates.status_postponed",
                "updates.status_wait_operation", "updates.status_wait_sync",
                "updates.status_helper_missing", "updates.status_started",
            )
            catalogs = getattr(self.translator, "_catalogs", {})
            for key in status_keys:
                if self._status_text in (
                    catalogs.get("en-US", {}).get(key),
                    catalogs.get("pt-BR", {}).get(key),
                ):
                    self._status_text = t(key)
                    break
        self.update_status.setText(self._status_text)
