"""Janela de preferências gerais do aplicativo."""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from ..i18n import LanguageManager
from .dialogs import ask_question, equalize_button_widths

DEFAULT_APP_SETTINGS = {
    "global_hotkeys_enabled": False,
    "create_save_hotkey": "",
    "load_save_hotkey": "",
    "load_save_hotkey_without_confirmation": False,
    "initialize_with_windows": False,
    "always_on_top": False,
    "sound_effect_import_enabled": True,
    "sound_effect_load_enabled": True,
    "sound_effect_auto_save_enabled": False,
    "sound_effect_volume": 50,
    "automatically_check_for_updates": True,
    "last_update_check_at": "",
    "language": "en-US",
}


class SettingsDialog(QDialog):
    """Edita as preferências gerais que ficam salvas no catálogo local."""

    language_changed_requested = Signal(str)

    # Initialize the object.
    def __init__(self, parent=None, *, settings: Mapping[str, object] | None = None, translator=None):
        super().__init__(parent)
        values = dict(DEFAULT_APP_SETTINGS)
        if settings:
            values.update(settings)
        self.translator = translator or LanguageManager(values.get("language"), self)
        self._last_update_check_at = str(values.get("last_update_check_at") or "")
        self.setWindowTitle(self.translator.tr("settings.title"))
        self.setMinimumWidth(460)
        self.resize(500, 430)

        root = QVBoxLayout(self)
        hotkeys = QGroupBox()
        hotkeys_form = QFormLayout(hotkeys)
        self.global_hotkeys = QCheckBox()
        self.global_hotkeys.setChecked(bool(values["global_hotkeys_enabled"]))
        hotkeys_form.addRow(self.global_hotkeys)
        self.load_save_hotkey_without_confirmation = QCheckBox()
        self.load_save_hotkey_without_confirmation.setChecked(bool(values["load_save_hotkey_without_confirmation"]))
        hotkeys_form.addRow(self.load_save_hotkey_without_confirmation)
        self.create_save_hotkey = self._key_editor(values["create_save_hotkey"])
        self.create_save_hotkey.setToolTip(self.translator.tr("settings.create_hotkey_tip"))
        create_hotkey_row = QHBoxLayout()
        create_hotkey_row.setContentsMargins(0, 0, 0, 0)
        create_hotkey_row.addWidget(self.create_save_hotkey)
        self.create_save_hotkey_reset = self._reset_button(self.create_save_hotkey)
        create_hotkey_row.addWidget(self.create_save_hotkey_reset)
        self.create_hotkey_label = QLabel()
        hotkeys_form.addRow(self.create_hotkey_label, create_hotkey_row)
        self.load_save_hotkey = self._key_editor(values["load_save_hotkey"])
        self.load_save_hotkey.setToolTip(self.translator.tr("settings.load_hotkey_tip"))
        load_hotkey_row = QHBoxLayout()
        load_hotkey_row.setContentsMargins(0, 0, 0, 0)
        load_hotkey_row.addWidget(self.load_save_hotkey)
        self.load_save_hotkey_reset = self._reset_button(self.load_save_hotkey)
        load_hotkey_row.addWidget(self.load_save_hotkey_reset)
        self.load_hotkey_label = QLabel()
        hotkeys_form.addRow(self.load_hotkey_label, load_hotkey_row)
        preferences = QGroupBox()
        preferences_form = QFormLayout(preferences)
        self.preferences_group = preferences
        self.language = QComboBox()
        for code in ("en-US", "pt-BR"):
            self.language.addItem(QIcon(str(self.translator.flag_path(code))), self.translator.language_name(code), code)
        selected_language = self.translator.normalize(values.get("language")) or self.translator.language
        self.language.setCurrentIndex(max(0, self.language.findData(selected_language)))
        self.language.currentIndexChanged.connect(
            lambda index: self.language_changed_requested.emit(str(self.language.itemData(index))),
        )
        self.language_changed_requested.connect(self.translator.set_language)
        self.initialize_with_windows = QCheckBox()
        self.initialize_with_windows.setChecked(bool(values["initialize_with_windows"]))
        preferences_form.addRow(self.initialize_with_windows)
        self.always_on_top = QCheckBox()
        self.always_on_top.setChecked(bool(values["always_on_top"]))
        preferences_form.addRow(self.always_on_top)
        self.automatically_check_for_updates = QCheckBox()
        self.automatically_check_for_updates.setChecked(bool(values["automatically_check_for_updates"]))
        preferences_form.addRow(self.automatically_check_for_updates)
        self.language_label = QLabel()
        preferences_form.insertRow(0, self.language_label, self.language)
        root.addWidget(preferences)

        audio = QGroupBox()
        self.audio_group = audio
        audio_form = QFormLayout(audio)
        self.sound_effect_import_enabled = QCheckBox()
        self.sound_effect_import_enabled.setChecked(bool(values["sound_effect_import_enabled"]))
        audio_form.addRow(self.sound_effect_import_enabled)
        self.sound_effect_load_enabled = QCheckBox()
        self.sound_effect_load_enabled.setChecked(bool(values["sound_effect_load_enabled"]))
        audio_form.addRow(self.sound_effect_load_enabled)
        self.sound_effect_auto_save_enabled = QCheckBox()
        self.sound_effect_auto_save_enabled.setChecked(bool(values["sound_effect_auto_save_enabled"]))
        audio_form.addRow(self.sound_effect_auto_save_enabled)
        volume_row = QHBoxLayout()
        self.sound_effect_volume = QSlider(Qt.Orientation.Horizontal)
        self.sound_effect_volume.setRange(0, 100)
        try:
            initial_volume = int(values["sound_effect_volume"])
        except (TypeError, ValueError):
            initial_volume = DEFAULT_APP_SETTINGS["sound_effect_volume"]
        self.sound_effect_volume.setValue(max(0, min(100, initial_volume)))
        self.sound_effect_volume_label = QLabel()
        self.sound_effect_volume_label.setMinimumWidth(42)
        self.sound_effect_volume_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        volume_row.addWidget(self.sound_effect_volume, 1)
        volume_row.addWidget(self.sound_effect_volume_label)
        self.volume_label = QLabel()
        audio_form.addRow(self.volume_label, volume_row)
        self.sound_effect_volume.valueChanged.connect(
            lambda value: self.sound_effect_volume_label.setText(f"{value}%"),
        )
        self.sound_effect_volume_label.setText(f"{self.sound_effect_volume.value()}%")
        root.addWidget(audio)
        self.hotkeys_group = hotkeys
        root.addWidget(hotkeys)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.default_button = QPushButton()
        self.default_button.clicked.connect(self._reset_defaults)
        button_row = QHBoxLayout()
        button_row.addWidget(self.default_button)
        button_row.addStretch(1)
        button_row.addWidget(self.buttons)
        root.addLayout(button_row)
        self.translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui(self.translator.language)

    @staticmethod
    # Create editor.
    def _key_editor(value: object) -> QKeySequenceEdit:
        editor = QKeySequenceEdit()
        text = str(value or "").strip()
        if text:
            editor.setKeySequence(QKeySequence(text))
        return editor

    @staticmethod
    # Reset button.
    def _reset_button(editor: QKeySequenceEdit) -> QPushButton:
        button = QPushButton("↺")
        button.setFixedWidth(32)
        button.setToolTip("Limpar hotkey")
        button.setAccessibleName("Limpar hotkey")
        button.clicked.connect(lambda: editor.setKeySequence(QKeySequence()))
        return button

    # Restore all default settings.
    def _reset_defaults(self) -> None:
        answer = ask_question(
            self,
            self.translator.tr,
            self.translator.tr("settings.default_title"),
            self.translator.tr("settings.default_confirmation"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        defaults = DEFAULT_APP_SETTINGS
        self.global_hotkeys.setChecked(bool(defaults["global_hotkeys_enabled"]))
        self.create_save_hotkey.setKeySequence(QKeySequence(str(defaults["create_save_hotkey"])))
        self.load_save_hotkey.setKeySequence(QKeySequence(str(defaults["load_save_hotkey"])))
        self.load_save_hotkey_without_confirmation.setChecked(bool(defaults["load_save_hotkey_without_confirmation"]))
        self.initialize_with_windows.setChecked(bool(defaults["initialize_with_windows"]))
        self.always_on_top.setChecked(bool(defaults["always_on_top"]))
        self.automatically_check_for_updates.setChecked(bool(defaults["automatically_check_for_updates"]))
        self.sound_effect_import_enabled.setChecked(bool(defaults["sound_effect_import_enabled"]))
        self.sound_effect_load_enabled.setChecked(bool(defaults["sound_effect_load_enabled"]))
        self.sound_effect_auto_save_enabled.setChecked(bool(defaults["sound_effect_auto_save_enabled"]))
        self.sound_effect_volume.setValue(int(defaults["sound_effect_volume"]))
        self._last_update_check_at = str(defaults["last_update_check_at"])

    # Collect preference values.
    def values(self) -> dict[str, object]:
        """Return the values in a JSON-compatible form for CatalogDB."""
        portable = QKeySequence.SequenceFormat.PortableText
        return {
            "global_hotkeys_enabled": self.global_hotkeys.isChecked(),
            "create_save_hotkey": self.create_save_hotkey.keySequence().toString(portable),
            "load_save_hotkey": self.load_save_hotkey.keySequence().toString(portable),
            "load_save_hotkey_without_confirmation": self.load_save_hotkey_without_confirmation.isChecked(),
            "initialize_with_windows": self.initialize_with_windows.isChecked(),
            "always_on_top": self.always_on_top.isChecked(),
            "sound_effect_import_enabled": self.sound_effect_import_enabled.isChecked(),
            "sound_effect_load_enabled": self.sound_effect_load_enabled.isChecked(),
            "sound_effect_auto_save_enabled": self.sound_effect_auto_save_enabled.isChecked(),
            "sound_effect_volume": self.sound_effect_volume.value(),
            "automatically_check_for_updates": self.automatically_check_for_updates.isChecked(),
            "last_update_check_at": self._last_update_check_at,
            "language": str(self.language.currentData()),
        }

    # Handle retranslate ui.
    def retranslate_ui(self, _language: str | None = None) -> None:
        t = self.translator.tr
        self.setWindowTitle(t("settings.title"))
        self.preferences_group.setTitle(t("settings.configuration"))
        self.audio_group.setTitle(t("settings.audio"))
        self.hotkeys_group.setTitle(t("settings.hotkeys"))
        self.language_label.setText(t("settings.language"))
        self.language.setToolTip(t("settings.language_tooltip"))
        self.global_hotkeys.setText(t("settings.global_hotkeys"))
        self.global_hotkeys.setToolTip(t("settings.global_hotkeys_tooltip"))
        self.load_save_hotkey_without_confirmation.setText(t("settings.load_without_confirmation"))
        self.load_save_hotkey_without_confirmation.setToolTip(t("settings.load_without_confirmation_tooltip"))
        self.create_hotkey_label.setText(t("settings.create_hotkey"))
        self.load_hotkey_label.setText(t("settings.load_hotkey"))
        self.create_save_hotkey.setToolTip(t("settings.create_hotkey_tip"))
        self.load_save_hotkey.setToolTip(t("settings.load_hotkey_tip"))
        self.initialize_with_windows.setText(t("settings.initialize_windows"))
        self.initialize_with_windows.setToolTip(t("settings.initialize_windows_tooltip"))
        self.always_on_top.setText(t("settings.always_on_top"))
        self.always_on_top.setToolTip(t("settings.always_on_top_tooltip"))
        self.automatically_check_for_updates.setText(t("settings.auto_updates"))
        self.automatically_check_for_updates.setToolTip(t("settings.auto_updates_tooltip"))
        self.sound_effect_import_enabled.setText(t("settings.import_sound"))
        self.sound_effect_import_enabled.setToolTip(t("settings.import_sound_tooltip"))
        self.sound_effect_load_enabled.setText(t("settings.load_sound"))
        self.sound_effect_load_enabled.setToolTip(t("settings.load_sound_tooltip"))
        self.sound_effect_auto_save_enabled.setText(t("settings.auto_save_sound"))
        self.sound_effect_auto_save_enabled.setToolTip(t("settings.auto_save_sound_tooltip"))
        self.volume_label.setText(t("settings.volume"))
        self.sound_effect_volume.setToolTip(t("settings.volume_tooltip"))
        self.create_save_hotkey_reset.setToolTip(t("settings.clear_hotkey"))
        self.load_save_hotkey_reset.setToolTip(t("settings.clear_hotkey"))
        self.create_save_hotkey_reset.setAccessibleName(t("settings.clear_hotkey"))
        self.load_save_hotkey_reset.setAccessibleName(t("settings.clear_hotkey"))
        self.default_button.setText(t("common.default"))
        self.default_button.setToolTip(t("settings.default_tooltip"))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(t("common.save"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("common.cancel"))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setToolTip(t("settings.save_tooltip"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setToolTip(t("settings.cancel_tooltip"))
        for index, code in enumerate(("en-US", "pt-BR")):
            self.language.setItemText(index, self.translator.language_name(code))
        equalize_button_widths([self.default_button, *self.buttons.buttons()])
