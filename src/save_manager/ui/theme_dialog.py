"""Theme picker dialog and color preview cards."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..i18n import LanguageManager
from .dialogs import equalize_button_widths
from .themes import Theme, get_theme, normalize_theme, theme_stylesheet


class ThemeCard(QFrame):
    """Clickable preview card for one theme."""

    clicked = Signal(str)

    # Initialize card.
    def __init__(self, theme: Theme, translator, *, selected: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self.translator = translator
        self.setObjectName("themeCard")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(270, 152)
        self.set_selected(selected)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(7)
        self.title_label = QLabel()
        self.title_label.setObjectName("themeCardTitle")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.description_label = QLabel()
        self.description_label.setObjectName("themeCardDescription")
        self.description_label.setWordWrap(True)
        self.description_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)
        layout.addStretch(1)

        swatches = QHBoxLayout()
        swatches.setSpacing(5)
        self.swatches: list[QFrame] = []
        for role in ("background", "panel", "primary", "destructive"):
            swatch = QFrame(self)
            swatch.setFixedSize(30, 14)
            swatch.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            swatch.setStyleSheet(
                f"background: {theme.colors[role]}; border: 1px solid {theme.colors['border']}; border-radius: 2px;",
            )
            self.swatches.append(swatch)
            swatches.addWidget(swatch)
        swatches.addStretch(1)
        layout.addLayout(swatches)
        self.retranslate_ui()

    # Set selected state.
    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", bool(selected))
        self._apply_card_style()
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    # Apply the preview palette to the card.
    def _apply_card_style(self) -> None:
        colors = self.theme.colors
        self.setStyleSheet(
            f"""
            QFrame#themeCard {{ background: {colors['panel']}; border: 1px solid {colors['border']}; border-radius: 6px; }}
            QFrame#themeCard:hover {{ background: {colors['hover']}; border-color: {colors['primary_hover']}; }}
            QFrame#themeCard[selected="true"] {{ border: 2px solid {colors['primary']}; }}
            QFrame#themeCard[selected="true"]:hover {{ border-color: {colors['primary_hover']}; }}
            QFrame#themeCard[pressed="true"] {{ background: {colors['pressed']}; border-color: {colors['primary']}; }}
            QLabel#themeCardTitle {{ background: transparent; border: 0; color: {colors['text']}; font-size: 12pt; font-weight: 600; }}
            QLabel#themeCardDescription {{ background: transparent; border: 0; color: {colors['muted']}; font-size: 10pt; }}
            """,
        )

    # Handle card click.
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setProperty("pressed", True)
            self.style().unpolish(self)
            self.style().polish(self)
            self.clicked.emit(self.theme.id)
            QTimer.singleShot(100, self._clear_pressed)
            event.accept()
            return
        super().mousePressEvent(event)

    # Clear transient pressed state.
    def _clear_pressed(self) -> None:
        self.setProperty("pressed", False)
        self._apply_card_style()
        self.style().unpolish(self)
        self.style().polish(self)

    # Handle keyboard activation.
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.theme.id)
            event.accept()
            return
        super().keyPressEvent(event)

    # Refresh translated card text.
    def retranslate_ui(self, _language: str | None = None) -> None:
        self.title_label.setText(self.translator.tr(self.theme.title_key))
        self.description_label.setText(self.translator.tr(self.theme.description_key))
        self.setToolTip(self.translator.tr("dialog.theme_card_tooltip"))


class ThemeDialog(QDialog):
    """Display the available color themes as selectable cards."""

    theme_selected = Signal(str)

    # Initialize dialog.
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        translator=None,
        current_theme: str = "default",
        themes: Iterable[Theme] = (),
    ):
        super().__init__(parent)
        self.translator = translator or LanguageManager(parent=self)
        self.current_theme = normalize_theme(current_theme)
        self.themes = tuple(themes)
        self.cards: dict[str, ThemeCard] = {}
        self.setMinimumWidth(650)
        self.resize(700, 430)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(12)
        self.question_label = QLabel()
        self.question_label.setWordWrap(True)
        root.addWidget(self.question_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for index, theme in enumerate(self.themes):
            card = ThemeCard(theme, self.translator, selected=theme.id == self.current_theme, parent=self)
            card.clicked.connect(self._select_theme)
            self.cards[theme.id] = card
            grid.addWidget(card, index // 2, index % 2)
        root.addLayout(grid, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons, 0, Qt.AlignmentFlag.AlignRight)
        self._apply_dialog_stylesheet()
        self.translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui()

    # Select and apply a theme.
    def _select_theme(self, theme_id: str) -> None:
        self.current_theme = normalize_theme(theme_id)
        for identifier, card in self.cards.items():
            card.set_selected(identifier == self.current_theme)
        self._apply_dialog_stylesheet()
        self.theme_selected.emit(self.current_theme)

    # Apply the current dialog theme.
    def _apply_dialog_stylesheet(self) -> None:
        colors = get_theme(self.current_theme).colors
        self.setStyleSheet(
            theme_stylesheet(self.current_theme)
            + f"""
            QLabel#themeCardTitle {{ background: transparent; border: 0; color: {colors['text']}; font-size: 12pt; font-weight: 600; }}
            QLabel#themeCardDescription {{ background: transparent; border: 0; color: {colors['muted']}; font-size: 10pt; }}
            """,
        )

    # Refresh translated dialog labels.
    def retranslate_ui(self, _language: str | None = None) -> None:
        t = self.translator.tr
        self.setWindowTitle(t("dialog.themes_title"))
        self.question_label.setText(t("dialog.themes_text"))
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText(t("common.close"))
        equalize_button_widths(self.buttons.buttons())
        for card in self.cards.values():
            card.retranslate_ui()
