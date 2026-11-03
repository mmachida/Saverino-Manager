"""Formulário de origem e destino compartilhados pelo jogo."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

from ..domain.models import GameSettings
from ..i18n import LanguageManager
from .dialogs import equalize_button_widths

MAX_GAME_NAME_LENGTH = 50


class GameSettingsDialog(QDialog):
    # Initialize the object.
    def __init__(self, parent=None, *, game_name=None, settings=None, validate=None, validate_name=None, conflicting=False, translator=None):
        super().__init__(parent)
        self.translator = translator or LanguageManager(parent=self)
        self.game_name = game_name
        self.setWindowTitle(self.translator.tr("dialog.add_game_title") if game_name is None else self.translator.tr("dialog.game_settings_title", name=game_name))
        self.setMinimumWidth(620)
        self.validate_settings = validate
        self.validate_name = validate_name
        form = QFormLayout(self)
        self.name = QLineEdit(game_name or "")
        self.name.setObjectName("game_name")
        self.name.setMaxLength(MAX_GAME_NAME_LENGTH)
        self.name_label = QLabel()
        form.addRow(self.name_label, self.name)
        explanation = QLabel()
        self.explanation = explanation
        explanation.setWordWrap(True)
        form.addRow(explanation)
        if conflicting:
            warning = QLabel()
            self.warning = warning
            warning.setWordWrap(True)
            form.addRow(warning)
        self.source_type = QComboBox()
        self.source_type.setObjectName("source_type")
        self.source_type.addItem("", "file")
        self.source_type.addItem("", "directory")
        self.source = QLineEdit(settings.source_path if settings else "")
        self.source.setObjectName("source_path")
        self.browse_source = QPushButton()
        self.browse_source.clicked.connect(self._choose_source)
        self.source_type.currentIndexChanged.connect(self._type_changed)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source)
        source_row.addWidget(self.browse_source)
        self.source_type_label = QLabel()
        self.game_save_label = QLabel()
        form.addRow(self.source_type_label, self.source_type)
        form.addRow(self.game_save_label, source_row)
        self.storage = QComboBox()
        self.storage.setObjectName("storage_mode")
        self.storage.addItem("", "app_folder")
        self.storage.addItem("", "beside_source")
        self.storage.addItem("", "custom")
        self.backup = QLineEdit(settings.backup_base_path if settings else "")
        self.backup.setObjectName("backup_base_path")
        self.browse_backup = QPushButton()
        self.browse_backup.clicked.connect(self._choose_backup)
        backup_row = QHBoxLayout()
        backup_row.addWidget(self.backup)
        backup_row.addWidget(self.browse_backup)
        self.backup_destination_label = QLabel()
        form.addRow(self.backup_destination_label, self.storage)
        self.backup_label = QLabel()
        form.addRow(self.backup_label, backup_row)
        self.storage.currentIndexChanged.connect(self._storage_changed)
        if settings:
            self.source_type.setCurrentIndex(self.source_type.findData(settings.source_type.value))
            storage_index = self.storage.findData(settings.storage_mode.value)
            self.storage.setCurrentIndex(max(storage_index, 0))
        self._storage_changed()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)
        self.translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui(self.translator.language)

    # Build dialog settings.
    def settings(self):
        return GameSettings(self.source_type.currentData(), self.source.text().strip(),
                            self.storage.currentData(), self.backup.text().strip())

    # Accept.
    def _accept(self):
        try:
            from ..domain.models import validate_folder_name
            name = validate_folder_name(self.name.text(), "Nome do jogo")
            if self.validate_name and self.validate_name(name):
                QMessageBox.warning(
                    self,
                    self.translator.tr("dialog.warning_title"),
                    self.translator.tr("dialog.game_name_exists"),
                )
                return
            settings = self.settings()
            if self.validate_settings:
                self.validate_settings(settings)
        except Exception as exc:
            QMessageBox.warning(self, self.translator.tr("dialog.warning_title"), str(exc))
            return
        self.accept()

    # Handle type changed.
    def _type_changed(self):
        key = "dialog.choose_file" if self.source_type.currentData() == "file" else "dialog.choose_folder"
        self.browse_source.setText(self.translator.tr(key))

    # Handle storage changed.
    def _storage_changed(self):
        custom = self.storage.currentData() == "custom"
        for widget in (self.backup, self.browse_backup, self.backup_label):
            widget.setVisible(custom)

    # Choose source.
    def _choose_source(self):
        if self.source_type.currentData() == "file":
            chosen = QFileDialog.getOpenFileName(self, self.translator.tr("dialog.choose_game_save_file"), self.source.text())[0]
        else:
            chosen = QFileDialog.getExistingDirectory(self, self.translator.tr("dialog.choose_game_save_folder"), self.source.text())
        if chosen:
            self.source.setText(chosen)

    # Choose backup.
    def _choose_backup(self):
        chosen = QFileDialog.getExistingDirectory(self, self.translator.tr("dialog.choose_backup_folder"), self.backup.text())
        if chosen:
            self.backup.setText(chosen)

    # Handle retranslate ui.
    def retranslate_ui(self, _language: str | None = None):
        t = self.translator.tr
        self.setWindowTitle(t("dialog.add_game_title") if self.game_name is None else t("dialog.game_settings_title", name=self.game_name))
        self.name_label.setText(t("dialog.game_name"))
        self.name.setToolTip(t("dialog.game_name_tooltip"))
        self.explanation.setText(t("dialog.game_explanation"))
        if hasattr(self, "warning"):
            self.warning.setText(t("dialog.conflicting_profiles"))
        self.source_type_label.setText(t("dialog.save_type"))
        self.source_type.setToolTip(t("dialog.save_type_tooltip"))
        self.game_save_label.setText(t("dialog.game_save"))
        self.source.setToolTip(t("dialog.game_save_tooltip"))
        self.source_type.setItemText(0, t("dialog.file"))
        self.source_type.setItemText(1, t("dialog.folder"))
        self.backup_destination_label.setText(t("dialog.backup_destination"))
        self.storage.setToolTip(t("dialog.backup_destination_tooltip"))
        self.storage.setItemText(0, t("dialog.app_folder"))
        self.storage.setItemText(1, t("dialog.same_save_folder"))
        self.storage.setItemText(2, t("dialog.custom"))
        self.backup_label.setText(t("dialog.backup_folder"))
        self.backup.setToolTip(t("dialog.backup_folder_tooltip"))
        self.browse_source.setToolTip(t("dialog.game_save_tooltip"))
        self.browse_backup.setToolTip(t("dialog.backup_folder_tooltip"))
        self.browse_backup.setText(t("dialog.browse"))
        self.browse_backup.setAccessibleName(t("dialog.browse"))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(t("common.save"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("common.cancel"))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setToolTip(t("settings.save_tooltip"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setToolTip(t("settings.cancel_tooltip"))
        equalize_button_widths(self.buttons.buttons())
        self._type_changed()
