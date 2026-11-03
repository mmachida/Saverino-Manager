"""Edit a profile name and its automatic save schedule."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..domain.models import Profile
from ..i18n import LanguageManager
from .auto_save_dialog import AutoSaveIntervalDialog
from .dialogs import ask_question, equalize_button_widths

PROFILE_NAME_MAX = 100


class ProfileSettingsDialog(QDialog):
    # Report auto-save enable.
    auto_save_enable_requested = Signal(int)
    # Report auto-save removal.
    auto_save_remove_requested = Signal()

    # Initialize profile dialog.
    def __init__(self, parent=None, *, profile: Profile, source_path: str, backup_path: str,
                 auto_save=None, translator=None):
        super().__init__(parent)
        self.profile = profile
        self.translator = translator or LanguageManager(parent=self)
        self._auto_save = dict(auto_save) if auto_save else None
        self.setMinimumWidth(520)
        self.resize(620, 245)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        form = QFormLayout()
        self.name_label = QLabel()
        self.name = QLineEdit(profile.name)
        self.name.setMaxLength(PROFILE_NAME_MAX)
        self.name.setObjectName("profile_name")
        form.addRow(self.name_label, self.name)
        self.source_label = QLabel()
        self.source_value = self._path_label(source_path)
        form.addRow(self.source_label, self.source_value)
        self.backup_label = QLabel()
        self.backup_value = self._path_label(backup_path)
        form.addRow(self.backup_label, self.backup_value)
        layout.addLayout(form)

        auto_row = QHBoxLayout()
        self.auto_save_status = QLabel()
        auto_row.addWidget(self.auto_save_status, 1)
        self.auto_save_button = QPushButton()
        self.auto_save_button.clicked.connect(self._handle_auto_save_action)
        auto_row.addWidget(self.auto_save_button)
        layout.addLayout(auto_row)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    # Create path label.
    @staticmethod
    def _path_label(value):
        label = QLabel(str(value))
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    # Handle profile schedule action.
    def _handle_auto_save_action(self):
        if self._auto_save is not None:
            self._remove_auto_save()
            return
        dialog = AutoSaveIntervalDialog(self, interval_seconds=60, translator=self.translator)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.auto_save_enable_requested.emit(dialog.interval.value())

    # Confirm schedule removal.
    def _remove_auto_save(self):
        if self._auto_save is None:
            return
        answer = ask_question(
            self,
            self.translator.tr,
            self.translator.tr("autosave.remove_title"),
            self.translator.tr("autosave.remove_question", name=self.profile.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.auto_save_remove_requested.emit()

    # Update profile schedule display.
    def set_auto_save(self, entry=None):
        self._auto_save = dict(entry) if entry else None
        self._update_auto_save_state()

    # Update schedule controls.
    def _update_auto_save_state(self):
        t = self.translator.tr
        if self._auto_save is None:
            self.auto_save_status.setText(t("autosave.inactive"))
            self.auto_save_button.setText(t("autosave.enable_profile"))
            self.auto_save_button.setObjectName("")
        else:
            self.auto_save_status.setText(
                t("autosave.configured_interval", seconds=self._auto_save["interval_seconds"])
            )
            self.auto_save_button.setText(t("autosave.remove_profile"))
            self.auto_save_button.setObjectName("destructiveButton")
        self.auto_save_button.style().unpolish(self.auto_save_button)
        self.auto_save_button.style().polish(self.auto_save_button)
        self.auto_save_status.setToolTip(t("autosave.profile_status_tooltip"))
        self.auto_save_button.setToolTip(
            t("autosave.interval_tooltip") if self._auto_save is None else t("autosave.remove_tooltip")
        )

    # Translate dialog text.
    def retranslate_ui(self, _language=None):
        t = self.translator.tr
        self.setWindowTitle(t("profile_settings.title", name=self.profile.name))
        self.name_label.setText(t("dialog.profile_name_label"))
        self.name.setToolTip(t("profile_settings.name_tooltip"))
        self.source_label.setText(t("dialog.shared_source_label"))
        self.backup_label.setText(t("dialog.profiles_folder_label"))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(t("common.save"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("common.cancel"))
        self._update_auto_save_state()
        equalize_button_widths(self.buttons.buttons())
