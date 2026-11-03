"""Dialogs for editing timed backup schedules."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from ..services.auto_save import MAX_INTERVAL_SECONDS, MIN_INTERVAL_SECONDS
from ..i18n import LanguageManager
from .dialogs import ask_question, equalize_button_widths


class AutoSaveIntervalDialog(QDialog):
    # Initialize interval dialog.
    def __init__(self, parent=None, *, interval_seconds=60, translator=None):
        super().__init__(parent)
        self.translator = translator or LanguageManager(parent=self)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        self.prompt = QLabel()
        layout.addWidget(self.prompt)
        self.interval = QSpinBox()
        self.interval.setRange(1, MAX_INTERVAL_SECONDS)
        self.interval.setValue(max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, int(interval_seconds))))
        layout.addWidget(self.interval)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    # Validate minimum interval.
    def _accept_if_valid(self):
        if self.interval.value() < MIN_INTERVAL_SECONDS:
            QMessageBox.warning(
                self,
                self.translator.tr("autosave.minimum_title"),
                self.translator.tr("autosave.minimum_message", seconds=MIN_INTERVAL_SECONDS),
            )
            self.interval.setFocus()
            return
        self.accept()

    # Translate dialog text.
    def retranslate_ui(self, _language=None):
        t = self.translator.tr
        self.setWindowTitle(t("autosave.interval_title"))
        self.prompt.setText(t("autosave.interval_prompt"))
        self.interval.setToolTip(t("autosave.interval_tooltip"))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(t("common.save"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("common.cancel"))
        equalize_button_widths(self.buttons.buttons())


class ProjectAutoSaveDialog(QDialog):
    """Edit project schedules without changing their targets."""

    # Initialize project dialog.
    def __init__(self, parent=None, *, project_name, entries, names, translator=None):
        super().__init__(parent)
        self.translator = translator or LanguageManager(parent=self)
        self.project_name = project_name
        self.entries = [dict(entry) for entry in entries]
        self.names = names
        self.setMinimumSize(680, 330)
        self.resize(760, 390)
        layout = QVBoxLayout(self)
        self.description = QLabel()
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.empty_label = QLabel()
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label, 1)
        self.management_row = QHBoxLayout()
        self.management_row.addStretch(1)
        self.toggle_all_button = QPushButton()
        self.toggle_all_button.clicked.connect(self._toggle_all)
        self.management_row.addWidget(self.toggle_all_button)
        self.remove_all_button = QPushButton()
        self.remove_all_button.setObjectName("destructiveButton")
        self.remove_all_button.clicked.connect(self._remove_all)
        self.management_row.addWidget(self.remove_all_button)
        layout.addLayout(self.management_row)
        self.table = QTableWidget(len(self.entries), 5)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)
        self._populate_rows()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    # Validate intervals before saving.
    def _accept_if_valid(self):
        for row in range(len(self.entries)):
            interval = self.table.cellWidget(row, 2)
            if interval is not None and interval.value() < MIN_INTERVAL_SECONDS:
                QMessageBox.warning(
                    self,
                    self.translator.tr("autosave.minimum_title"),
                    self.translator.tr("autosave.minimum_message", seconds=MIN_INTERVAL_SECONDS),
                )
                interval.setFocus()
                return
        self.accept()

    # Populate schedule rows.
    def _populate_rows(self):
        for row, entry in enumerate(self.entries):
            kind_key = f"autosave.{entry['target_type']}"
            kind = QTableWidgetItem(self.translator.tr(kind_key))
            kind.setFlags(kind.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name = QTableWidgetItem(self.names.get(str(entry["id"]), str(entry.get("target_name", ""))))
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, kind)
            self.table.setItem(row, 1, name)
            interval = QSpinBox()
            interval.setRange(1, MAX_INTERVAL_SECONDS)
            interval.setValue(int(entry["interval_seconds"]))
            interval.setToolTip(self.translator.tr("autosave.interval_tooltip"))
            self.table.setCellWidget(row, 2, interval)
            enabled = QCheckBox()
            enabled.setChecked(bool(entry["enabled"]))
            enabled.setToolTip(self.translator.tr("autosave.enable_tooltip"))
            enabled.stateChanged.connect(self._update_bulk_action_text)
            wrapper = QWidget()
            wrapper_layout = QHBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(0, 0, 0, 0)
            wrapper_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            wrapper_layout.addWidget(enabled)
            self.table.setCellWidget(row, 3, wrapper)
            remove = QPushButton("×")
            remove.setObjectName("destructiveButton")
            remove.setFixedWidth(34)
            remove.setToolTip(self.translator.tr("autosave.remove_tooltip"))
            remove.clicked.connect(lambda _checked=False, entry_id=str(entry["id"]): self._remove_entry(entry_id))
            self.table.setCellWidget(row, 4, remove)

    # Remove a schedule row.
    def _remove_entry(self, entry_id):
        entry = next((item for item in self.entries if str(item["id"]) == entry_id), None)
        if entry is None:
            return
        name = self.names.get(entry_id, str(entry.get("target_name", "")))
        row = next(index for index, item in enumerate(self.entries) if str(item["id"]) == entry_id)
        answer = ask_question(
            self,
            self.translator.tr,
            self.translator.tr("autosave.remove_title"),
            self.translator.tr("autosave.remove_question", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.entries = [item for item in self.entries if str(item["id"]) != entry_id]
        self.table.removeRow(row)
        self._update_empty_state()
        self._update_bulk_action_text()

    # Toggle every schedule.
    def _toggle_all(self):
        checkboxes = [
            wrapper.findChild(QCheckBox)
            for row in range(len(self.entries))
            if (wrapper := self.table.cellWidget(row, 3)) is not None
        ]
        enable = not all(box.isChecked() for box in checkboxes if box is not None)
        for checkbox in checkboxes:
            if checkbox is not None:
                checkbox.setChecked(enable)
        self._update_bulk_action_text()

    # Confirm removing all schedules.
    def _remove_all(self):
        if not self.entries:
            return
        answer = ask_question(
            self,
            self.translator.tr,
            self.translator.tr("autosave.remove_all_title"),
            self.translator.tr("autosave.remove_all_question", count=len(self.entries)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.entries.clear()
        self.table.setRowCount(0)
        self._update_empty_state()
        self._update_bulk_action_text()

    # Collect edited values.
    def values(self):
        result = []
        visible_entries = [entry for entry in self.entries]
        for row, entry in enumerate(visible_entries):
            interval = self.table.cellWidget(row, 2)
            wrapper = self.table.cellWidget(row, 3)
            checkbox = wrapper.findChild(QCheckBox) if wrapper else None
            updated = dict(entry)
            if interval is not None:
                updated["interval_seconds"] = interval.value()
            if checkbox is not None:
                updated["enabled"] = checkbox.isChecked()
            result.append(updated)
        return result

    # Update empty state.
    def _update_empty_state(self):
        empty = not self.entries
        self.description.setVisible(not empty)
        self.empty_label.setVisible(empty)
        self.toggle_all_button.setVisible(not empty)
        self.remove_all_button.setVisible(not empty)
        self.table.setVisible(not empty)

    # Update bulk toggle label.
    def _update_bulk_action_text(self, *_args):
        checkboxes = [
            wrapper.findChild(QCheckBox)
            for row in range(len(self.entries))
            if (wrapper := self.table.cellWidget(row, 3)) is not None
        ]
        all_enabled = bool(checkboxes) and all(
            checkbox is not None and checkbox.isChecked() for checkbox in checkboxes
        )
        self.toggle_all_button.setText(
            self.translator.tr("autosave.disable_all" if all_enabled else "autosave.enable_all")
        )

    # Translate dialog text.
    def retranslate_ui(self, _language=None):
        t = self.translator.tr
        self.setWindowTitle(t("autosave.title", name=self.project_name))
        self.description.setText(t("autosave.description"))
        self.empty_label.setText(t("autosave.empty"))
        self.remove_all_button.setText(t("autosave.remove_all"))
        self.table.setHorizontalHeaderLabels([
            t("autosave.type"), t("autosave.name"), t("autosave.interval"),
            t("autosave.enable"), t("autosave.delete"),
        ])
        for row, entry in enumerate(self.entries):
            kind_key = f"autosave.{entry['target_type']}"
            self.table.item(row, 0).setText(t(kind_key))
            self.table.cellWidget(row, 2).setToolTip(t("autosave.interval_tooltip"))
            wrapper = self.table.cellWidget(row, 3)
            checkbox = wrapper.findChild(QCheckBox) if wrapper else None
            if checkbox:
                checkbox.setToolTip(t("autosave.enable_tooltip"))
            self.table.cellWidget(row, 4).setToolTip(t("autosave.remove_tooltip"))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(t("common.save"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("common.cancel"))
        equalize_button_widths(self.buttons.buttons())
        self._update_empty_state()
        self._update_bulk_action_text()
