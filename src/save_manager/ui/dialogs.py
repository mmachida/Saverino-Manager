"""Shared localized message box helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .themes import get_theme, normalize_theme

_ORIGINAL_QUESTION = QMessageBox.question


# Match action button widths.
def equalize_button_widths(buttons: Iterable[QAbstractButton | None]) -> None:
    """Give a dialog's action buttons one shared width."""
    widgets = [button for button in buttons if button is not None]
    if not widgets:
        return
    for button in widgets:
        button.ensurePolished()
    width = max(button.sizeHint().width() for button in widgets)
    for button in widgets:
        button.setFixedWidth(width)


class ActionCard(QFrame):
    """Clickable card used for the import/export choices."""

    clicked = Signal()

    # Initialize card.
    def __init__(self, title: str, description: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("actionCard")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setProperty("pressed", False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(250, 170)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("actionCardTitle")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.description_label = QLabel(description, self)
        self.description_label.setObjectName("actionCardDescription")
        self.description_label.setWordWrap(True)
        self.description_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self.description_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_label)
        layout.addStretch(1)
        layout.addWidget(self.description_label)

    # Handle card click.
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setProperty("pressed", True)
            self.style().unpolish(self)
            self.style().polish(self)
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    # Handle card keyboard activation.
    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ImportExportDialog(QDialog):
    """Present import and export as large action cards."""

    # Initialize dialog.
    def __init__(self, parent: QWidget | None = None, *, translator=None, theme_id: str | None = None):
        super().__init__(parent)
        self.translator = translator
        self.theme_id = normalize_theme(theme_id if theme_id is not None else getattr(parent, "_theme_id", "default"))
        self.action: str | None = None
        self.setWindowTitle(self._tr("dialog.import_export_title"))
        self.setMinimumWidth(600)
        self.resize(660, 310)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(14)
        self.question_label = QLabel()
        self.question_label.setObjectName("importExportQuestion")
        root.addWidget(self.question_label)
        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.export_card = ActionCard("", "", self)
        self.import_card = ActionCard("", "", self)
        self.export_card.clicked.connect(lambda: self._choose("export"))
        self.import_card.clicked.connect(lambda: self._choose("import"))
        cards.addWidget(self.export_card, 1)
        cards.addWidget(self.import_card, 1)
        root.addLayout(cards)
        self.note_label = QLabel()
        self.note_label.setWordWrap(True)
        self.note_label.setObjectName("importExportNote")
        root.addWidget(self.note_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self._apply_theme_stylesheet()
        if self.translator is not None:
            self.translator.language_changed.connect(self._retranslate_ui)
        self._retranslate_ui()

    # Apply the active theme.
    def _apply_theme_stylesheet(self) -> None:
        colors = get_theme(self.theme_id).colors
        self.setStyleSheet(
            f"""
            QFrame#actionCard {{ background: {colors['panel']}; border: 1px solid {colors['border']}; border-radius: 5px; }}
            QFrame#actionCard:hover {{ background: {colors['hover']}; border-color: {colors['primary_hover']}; }}
            QFrame#actionCard[pressed="true"] {{ background: {colors['primary_pressed']}; border-color: {colors['primary']}; }}
            QFrame#actionCard:focus {{ border: 1px solid {colors['primary']}; }}
            QLabel#actionCardTitle {{ background: transparent; border: 0; color: {colors['text']}; font-size: 13pt; font-weight: 600; }}
            QLabel#actionCardDescription {{ background: transparent; border: 0; color: {colors['muted']}; font-size: 10pt; }}
            QLabel#importExportNote {{ background: {colors['warning_background']}; border: 1px solid {colors['warning_border']}; border-radius: 4px; color: {colors['warning_text']}; font-weight: 600; padding: 8px 10px; }}
            """,
        )

    # Translate text.
    def _tr(self, key: str) -> str:
        return self.translator.tr(key) if self.translator is not None else key

    # Choose an action.
    def _choose(self, action: str) -> None:
        self.action = action
        self.accept()

    # Retranslate dialog.
    def _retranslate_ui(self) -> None:
        self.setWindowTitle(self._tr("dialog.import_export_title"))
        self.question_label.setText(self._tr("dialog.import_export_question"))
        self.export_card.title_label.setText(self._tr("dialog.import_export_export_title"))
        self.export_card.description_label.setText(self._tr("dialog.import_export_export_description"))
        self.export_card.setToolTip(self._tr("dialog.import_export_export_tooltip"))
        self.import_card.title_label.setText(self._tr("dialog.import_export_import_title"))
        self.import_card.description_label.setText(self._tr("dialog.import_export_import_description"))
        self.import_card.setToolTip(self._tr("dialog.import_export_import_tooltip"))
        self.note_label.setText(self._tr("dialog.import_export_note"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self._tr("common.cancel"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setToolTip(self._tr("settings.cancel_tooltip"))
        equalize_button_widths(self.buttons.buttons())


# Show a localized question.
def ask_question(
    parent: QWidget | None,
    translator: Callable[..., str],
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton,
    default: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
) -> QMessageBox.StandardButton:
    """Display a question whose standard buttons use the app catalog."""
    # Preserve test doubles that replace the Qt static helper.
    if QMessageBox.question is not _ORIGINAL_QUESTION:
        return QMessageBox.question(parent, title, text, buttons, default)

    dialog = QMessageBox(parent)
    dialog.setIcon(QMessageBox.Icon.Question)
    dialog.setWindowTitle(title)
    dialog.setText(text)
    dialog.setStandardButtons(buttons)
    if default != QMessageBox.StandardButton.NoButton:
        dialog.setDefaultButton(default)
    labels = {
        QMessageBox.StandardButton.Yes: "common.yes",
        QMessageBox.StandardButton.No: "common.no",
        QMessageBox.StandardButton.Cancel: "common.cancel",
    }
    for button, key in labels.items():
        widget = dialog.button(button)
        if widget is not None:
            widget.setText(translator(key))
    equalize_button_widths(dialog.buttons())
    return QMessageBox.StandardButton(dialog.exec())
