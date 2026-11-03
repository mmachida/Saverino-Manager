"""Color palettes and stylesheets used by the application themes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Theme:
    """Describe a selectable color theme."""

    id: str
    title_key: str
    description_key: str
    colors: dict[str, str]


# Theme used for a new installation.
INITIAL_THEME = "night_mode"


# Keep the roles shared so future themes only need to supply colors.
_DEFAULT_COLORS = {
    "background": "#f0f0f0",
    "panel": "#ffffff",
    "hover": "#e5e5e5",
    "pressed": "#d6d6d6",
    "primary": "#0078d4",
    "primary_hover": "#106ebe",
    "primary_pressed": "#005a9e",
    "load": "#6f9b70",
    "load_hover": "#80ae81",
    "load_pressed": "#5a815b",
    "load_text": "#ffffff",
    "disabled_load": "#9aa99a",
    "disabled_load_text": "#f1f1f1",
    "button": "#f0f0f0",
    "button_hover": "#e8e8e8",
    "button_pressed": "#d4d4d4",
    "button_text": "#1f1f1f",
    "button_border": "#adadad",
    "button_border_light": "#ffffff",
    "button_border_dark": "#8f8f8f",
    "destructive": "#d6aaa4",
    "destructive_hover": "#e3bbb4",
    "destructive_pressed": "#bd8b84",
    "destructive_text": "#2f201e",
    "border": "#a6a6a6",
    "group_border": "#808080",
    "checkbox_border": "#333333",
    "checkbox_hover_border": "#111111",
    "list_selection": "#d0d0d0",
    "list_selection_hover": "#c4c4c4",
    "list_selection_text": "#1f1f1f",
    "grid": "#d4d4d4",
    "text": "#1f1f1f",
    "muted": "#5f6368",
    "disabled": "#8a8a8a",
    "disabled_button": "#d9d9d9",
    "disabled_destructive": "#a08b87",
    "disabled_destructive_text": "#f1eeee",
    "scroll_handle": "#b3b3b3",
    "scroll_handle_hover": "#858585",
    "warning_background": "#fff4ce",
    "warning_border": "#d6b656",
    "warning_text": "#5b4b00",
    "auto_save_on": "#007a2e",
    "auto_save_off": "#8a4200",
}


# Build complete palette.
def _palette(**overrides: str) -> dict[str, str]:
    """Build a complete palette from the default roles."""
    colors = dict(_DEFAULT_COLORS)
    colors.update(overrides)
    return colors


# These palettes keep the same semantic roles across themes.
THEMES = (
    Theme("default", "theme.default_title", "theme.default_description", _palette()),
    Theme(
        "night_mode",
        "theme.night_mode_title",
        "theme.night_mode_description",
        _palette(
            background="#1e1e1e",
            panel="#252526",
            hover="#333333",
            pressed="#2d2d30",
            primary="#3b82f6",
            primary_hover="#5b9bff",
            primary_pressed="#2563c7",
            load="#3f865e",
            load_hover="#4e9b6e",
            load_pressed="#2f6849",
            load_text="#f2fff5",
            disabled_load="#344d3d",
            disabled_load_text="#aebdb3",
            button="#2d2d30",
            button_hover="#3a3a3a",
            button_pressed="#1f1f1f",
            button_text="#f1f1f1",
            button_border="#5a5a5a",
            button_border_light="#686868",
            button_border_dark="#181818",
            destructive="#a85650",
            destructive_hover="#bc6c65",
            destructive_pressed="#85423e",
            destructive_text="#fff5f3",
            border="#4b4b4b",
            group_border="#666666",
            checkbox_border="#b8b8b8",
            checkbox_hover_border="#ededed",
            list_selection="#3a3a3a",
            list_selection_hover="#474747",
            list_selection_text="#f1f1f1",
            grid="#373737",
            text="#f1f1f1",
            muted="#b4b4b4",
            disabled="#858585",
            disabled_button="#303030",
            disabled_destructive="#583936",
            disabled_destructive_text="#ded6d5",
            scroll_handle="#707070",
            scroll_handle_hover="#969696",
            warning_background="#3b351e",
            warning_border="#8f7b35",
            warning_text="#eadb9a",
            auto_save_on="#70d890",
            auto_save_off="#f2a65a",
        ),
    ),
    Theme(
        "hollow_knight",
        "theme.hollow_knight_title",
        "theme.hollow_knight_description",
        _palette(
            background="#0b1233",
            panel="#121d49",
            hover="#1d3170",
            pressed="#17285d",
            primary="#2f5f9e",
            primary_hover="#477bb8",
            primary_pressed="#244b7f",
            load="#3f857a",
            load_hover="#519b8e",
            load_pressed="#2f6b62",
            load_text="#e8fff8",
            disabled_load="#2b4945",
            disabled_load_text="#bfd2cd",
            button="#2f5f9e",
            button_hover="#477bb8",
            button_pressed="#244b7f",
            button_text="#e8efff",
            button_border="#304c88",
            button_border_light="#304c88",
            button_border_dark="#304c88",
            group_border="#304c88",
            checkbox_border="#9eb5e5",
            checkbox_hover_border="#d8e5ff",
            list_selection="#2f5f9e",
            list_selection_hover="#477bb8",
            list_selection_text="#e8efff",
            destructive="#99536d",
            destructive_hover="#b56d86",
            destructive_pressed="#7d4258",
            destructive_text="#fff7fb",
            border="#304c88",
            grid="#243b70",
            text="#eef2ff",
            muted="#bdcaeb",
            disabled="#7182aa",
            disabled_button="#222f55",
            disabled_destructive="#4a3040",
            disabled_destructive_text="#cfd7ef",
            scroll_handle="#6f8fc5",
            scroll_handle_hover="#9db8e9",
            warning_background="#292d50",
            warning_border="#7284c2",
            warning_text="#dce4ff",
            auto_save_on="#79d8a0",
            auto_save_off="#f0b04f",
        ),
    ),
    Theme(
        "silk_song",
        "theme.silk_song_title",
        "theme.silk_song_description",
        _palette(
            background="#151313",
            panel="#211b1b",
            hover="#302424",
            pressed="#271e1e",
            primary="#a85c4f",
            primary_hover="#ba7061",
            primary_pressed="#82463e",
            load="#728b60",
            load_hover="#829f6d",
            load_pressed="#586d4a",
            load_text="#f4f7ec",
            disabled_load="#3b4934",
            disabled_load_text="#cbd1c2",
            button="#443332",
            button_hover="#57403e",
            button_pressed="#332625",
            button_text="#f2e7e4",
            button_border="#674945",
            button_border_light="#674945",
            button_border_dark="#674945",
            destructive="#994b43",
            destructive_hover="#ac6055",
            destructive_pressed="#783a35",
            destructive_text="#fff3f0",
            border="#57403c",
            group_border="#6c4d48",
            checkbox_border="#c4a8a2",
            checkbox_hover_border="#ead7d2",
            list_selection="#633e3a",
            list_selection_hover="#754b45",
            list_selection_text="#f2e7e4",
            grid="#3d2f2d",
            text="#f2e7e4",
            muted="#c3aaa5",
            disabled="#8a7773",
            disabled_button="#302525",
            disabled_destructive="#523330",
            disabled_destructive_text="#d8c8c5",
            scroll_handle="#705955",
            scroll_handle_hover="#9b7c75",
            warning_background="#3a2c1d",
            warning_border="#88653b",
            warning_text="#e8d1a3",
            auto_save_on="#83d495",
            auto_save_off="#f1a86b",
        ),
    ),
    Theme(
        "elden_ring",
        "theme.elden_ring_title",
        "theme.elden_ring_description",
        _palette(
            background="#100e0a",
            panel="#1d1911",
            hover="#332c1b",
            pressed="#2a2416",
            primary="#8b7030",
            primary_hover="#a18643",
            primary_pressed="#66521f",
            load="#718049",
            load_hover="#84945a",
            load_pressed="#586437",
            load_text="#f3efdc",
            disabled_load="#3c422b",
            disabled_load_text="#d0cbb4",
            button="#8b7030",
            button_hover="#a18643",
            button_pressed="#66521f",
            button_text="#eadfbe",
            button_border="#66552d",
            button_border_light="#66552d",
            button_border_dark="#66552d",
            group_border="#66552d",
            checkbox_border="#c7b77c",
            checkbox_hover_border="#f1dda0",
            list_selection="#8b7030",
            list_selection_hover="#a18643",
            list_selection_text="#f2e8c9",
            destructive="#8b493c",
            destructive_hover="#a35a4c",
            destructive_pressed="#6e382f",
            destructive_text="#fff5df",
            border="#66552d",
            grid="#473c22",
            text="#f2e8c9",
            muted="#c0b58d",
            disabled="#827858",
            disabled_button="#302a1b",
            disabled_destructive="#453029",
            disabled_destructive_text="#ddd4bb",
            scroll_handle="#93804b",
            scroll_handle_hover="#c3a961",
            warning_background="#302914",
            warning_border="#8c7335",
            warning_text="#e7cf83",
            auto_save_on="#a8cf70",
            auto_save_off="#e5aa50",
        ),
    ),
    Theme(
        "batman_arkham_knight",
        "theme.batman_arkham_knight_title",
        "theme.batman_arkham_knight_description",
        _palette(
            background="#111416",
            panel="#22282b",
            hover="#30383b",
            pressed="#282f32",
            primary="#8b6738",
            primary_hover="#a27b49",
            primary_pressed="#674b29",
            load="#5f846a",
            load_hover="#709b7c",
            load_pressed="#4a6753",
            load_text="#edf5ee",
            disabled_load="#35473a",
            disabled_load_text="#c7d3c9",
            button="#8b6738",
            button_hover="#a27b49",
            button_pressed="#674b29",
            button_text="#e6e8e8",
            button_border="#50595d",
            button_border_light="#50595d",
            button_border_dark="#50595d",
            group_border="#50595d",
            checkbox_border="#aeb9bb",
            checkbox_hover_border="#edf0f0",
            list_selection="#8b6738",
            list_selection_hover="#a27b49",
            list_selection_text="#edf0f0",
            destructive="#8e4840",
            destructive_hover="#a15c50",
            destructive_pressed="#6e3732",
            destructive_text="#fff4f1",
            border="#50595d",
            grid="#3b4447",
            text="#edf0f0",
            muted="#bec6c7",
            disabled="#7a8385",
            disabled_button="#343a3c",
            disabled_destructive="#483133",
            disabled_destructive_text="#d9dede",
            scroll_handle="#7d8789",
            scroll_handle_hover="#adb8b9",
            warning_background="#30291d",
            warning_border="#8f6e3d",
            warning_text="#ead49d",
            auto_save_on="#81d39a",
            auto_save_off="#eea96d",
        ),
    ),
)

THEMES_BY_ID = {theme.id: theme for theme in THEMES}


# Normalize theme id.
def normalize_theme(theme_id: object) -> str:
    """Return a supported theme id, falling back to default."""
    value = str(theme_id or "").strip().casefold()
    return value if value in THEMES_BY_ID else "default"


# Get theme definition.
def get_theme(theme_id: object) -> Theme:
    """Return the selected theme definition."""
    return THEMES_BY_ID[normalize_theme(theme_id)]


_STYLESHEET = """
QWidget {{ background: @background@; color: @text@; font-size: 10pt; }}
QLabel#about_logo {{ background: @panel@; border: 1px solid @border@; border-radius: 6px; }}
QLabel#about_app_name {{ font-size: 16pt; font-weight: 600; }}
QLabel#about_version, QLabel#version_label {{ color: @muted@; font-size: 9pt; }}
QLabel#path_value {{ color: @muted@; }}
QLineEdit, QTextEdit, QComboBox, QListWidget {{ background: @panel@; border: 1px solid @border@; padding: 4px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 22px; background: @panel@; border-left: 1px solid @border@; }}
QComboBox::drop-down:hover {{ background: @hover@; }}
QComboBox::drop-down:pressed {{ background: @pressed@; }}
QComboBox::down-arrow {{ image: url("@combo_arrow@"); width: 8px; height: 8px; margin: 0; }}
QGroupBox {{ border: 1px solid @group_border@; border-radius: 4px; margin-top: 8px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: @text@; background: @background@; }}
QCheckBox::indicator {{ width: 13px; height: 13px; background: @panel@; border: 1px solid @checkbox_border@; border-radius: 2px; }}
QCheckBox::indicator:hover {{ background: @hover@; border-color: @checkbox_hover_border@; }}
QCheckBox::indicator:checked {{ background: @primary@; border: 1px solid @primary_hover@; image: url("@checkbox_check@"); }}
QCheckBox::indicator:checked:hover {{ background: @primary_hover@; border-color: @primary_hover@; }}
QCheckBox::indicator:checked:disabled {{ background: @disabled_button@; border-color: @disabled@; image: url("@checkbox_check@"); }}
QCheckBox::indicator:disabled {{ background: @disabled_button@; border-color: @disabled@; }}
QTableWidget {{ background: @panel@; border: 1px solid @border@; padding: 0; }}
QComboBox:focus {{ border: 1px solid @border@; outline: none; }}
QComboBox QAbstractItemView {{ background: @panel@; color: @text@; border: 1px solid @border@; outline: none; }}
QComboBox QAbstractItemView::item {{ background: @panel@; color: @text@; border: 0; padding: 4px; }}
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:selected:active {{ background: @hover@; color: @text@; border: 0; outline: none; }}
QComboBox QAbstractItemView::item:pressed {{ background: @pressed@; color: @text@; border: 0; outline: none; }}
QComboBox QAbstractItemView::item:focus {{ border: 0; outline: none; }}
QTableWidget {{ gridline-color: @grid@; alternate-background-color: @panel@; selection-background-color: @hover@; }}
QTableWidget::item {{ background: @panel@; border: 0; padding: 4px; }}
QTableWidget::item:hover {{ background: @hover@; }}
QTableWidget::item:selected {{ background: @hover@; border: 0; }}
QAbstractItemView::item:selected {{ background: @hover@; color: @text@; border: 0; }}
QTableWidget::item:focus, QAbstractItemView::item:focus {{ border: 0; outline: none; }}
QTableWidget:focus, QListWidget:focus {{ border: 1px solid @border@; outline: none; }}
QTableWidget QHeaderView, QTableWidget QHeaderView::section, QTableCornerButton::section {{ background: @panel@; color: @text@; border: 0; border-bottom: 1px solid @border@; padding: 5px; }}
QListWidget::item {{ background: @panel@; color: @text@; border: 0; padding: 4px 6px; margin: 0; }}
QListWidget::item:hover {{ background: @hover@; }}
QListWidget::item:selected {{ background: @list_selection@; color: @list_selection_text@; }}
QListWidget::item:selected:hover {{ background: @list_selection_hover@; color: @list_selection_text@; }}
QListWidget::item:pressed {{ background: @primary_pressed@; }}
QScrollBar:vertical {{ background: @background@; width: 12px; margin: 0; border: 1px solid @grid@; }}
QScrollBar:horizontal {{ background: @background@; height: 12px; margin: 0; border: 1px solid @grid@; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: @scroll_handle@; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 24px; }}
QScrollBar::handle:horizontal {{ min-width: 24px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: @scroll_handle_hover@; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal,
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ background: @background@; border: 0; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 12px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 12px; }}
QScrollBar::corner {{ background: @background@; }}
QSlider::groove:horizontal {{ background: @hover@; height: 6px; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: @primary@; border-radius: 3px; }}
QSlider::add-page:horizontal {{ background: @hover@; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: @scroll_handle@; width: 14px; margin: -4px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: @scroll_handle_hover@; }}
QSlider::handle:horizontal:pressed {{ background: @primary_hover@; }}
QPushButton {{ background: @button@; color: @button_text@; border: 1px solid @button_border@; border-top-color: @button_border_light@; border-left-color: @button_border_light@; border-right-color: @button_border_dark@; border-bottom-color: @button_border_dark@; border-radius: 3px; padding: 6px 10px; }}
QPushButton:hover {{ background: @button_hover@; }}
QPushButton:pressed {{ background: @button_pressed@; border-top-color: @button_border_dark@; border-left-color: @button_border_dark@; border-right-color: @button_border_light@; border-bottom-color: @button_border_light@; }}
QPushButton:disabled {{ background: @disabled_button@; color: @disabled@; border-color: @button_border_dark@; }}
QPushButton#destructiveButton {{ background: @destructive@; color: @destructive_text@; border: 1px solid @button_border@; border-top-color: @button_border_light@; border-left-color: @button_border_light@; border-right-color: @button_border_dark@; border-bottom-color: @button_border_dark@; }}
QPushButton#destructiveButton:hover {{ background: @destructive_hover@; }}
QPushButton#destructiveButton:pressed {{ background: @destructive_pressed@; border-top-color: @button_border_dark@; border-left-color: @button_border_dark@; border-right-color: @button_border_light@; border-bottom-color: @button_border_light@; }}
QPushButton#destructiveButton:disabled {{ background: @disabled_destructive@; color: @disabled_destructive_text@; }}
QPushButton#loadButton {{ background: @load@; color: @load_text@; border: 1px solid @button_border@; border-top-color: @button_border_light@; border-left-color: @button_border_light@; border-right-color: @button_border_dark@; border-bottom-color: @button_border_dark@; }}
QPushButton#loadButton:hover {{ background: @load_hover@; }}
QPushButton#loadButton:pressed {{ background: @load_pressed@; border-top-color: @button_border_dark@; border-left-color: @button_border_dark@; border-right-color: @button_border_light@; border-bottom-color: @button_border_light@; }}
QPushButton#loadButton:disabled {{ background: @disabled_load@; color: @disabled_load_text@; border-color: @button_border_dark@; }}
QPushButton#autoSaveSettingsButton[autoSaveAvailable="false"] {{ color: @text@; }}
QPushButton#autoSaveSettingsButton[autoSaveAvailable="true"][autoSaveEnabled="true"] {{ color: @auto_save_on@; font-weight: @auto_save_weight@; }}
QPushButton#autoSaveSettingsButton[autoSaveAvailable="true"][autoSaveEnabled="false"] {{ color: @auto_save_off@; font-weight: @auto_save_weight@; }}
QMenuBar {{ background: @background@; color: @text@; border-bottom: 1px solid @border@; padding: 2px; }}
QMenuBar::item {{ background: transparent; color: @text@; padding: 5px 10px; }}
QMenuBar::item:selected {{ background: @hover@; color: @text@; }}
QMenuBar::item:pressed {{ background: @pressed@; color: @text@; }}
QMenuBar::item:disabled {{ background: transparent; color: @disabled@; }}
QToolButton {{ background: transparent; color: @text@; border: 0; padding: 5px 10px; }}
QToolButton:hover {{ background: @hover@; color: @text@; }}
QToolButton:pressed {{ background: @pressed@; color: @text@; }}
QMenuBar QWidget#menuDivider {{ background: @border@; }}
QMenuBar QToolButton#autoSaveMenuItem {{ padding: 3px 8px 7px 8px; }}
QMenuBar QToolButton#autoSaveMenuItem[autoSaveAvailable="false"] {{ color: @text@; }}
QMenuBar QToolButton#autoSaveMenuItem[autoSaveAvailable="true"][autoSaveEnabled="true"] {{ color: @auto_save_on@; font-weight: @auto_save_weight@; }}
QMenuBar QToolButton#autoSaveMenuItem[autoSaveAvailable="true"][autoSaveEnabled="false"] {{ color: @auto_save_off@; font-weight: @auto_save_weight@; }}
QMenu {{ background: @panel@; color: @text@; border: 1px solid @border@; padding: 4px; }}
QMenu::item {{ background: transparent; color: @text@; padding: 6px 22px 6px 10px; }}
QMenu::item:selected {{ background: @hover@; color: @text@; }}
QMenu::item:pressed {{ background: @pressed@; color: @text@; }}
QMenu::item:disabled {{ background: transparent; color: @disabled@; }}
"""


# Render theme stylesheet.
def theme_stylesheet(theme_id: object) -> str:
    """Render the application stylesheet for a theme."""
    colors = get_theme(theme_id).colors
    auto_save_weight = "600" if normalize_theme(theme_id) == "default" else "normal"
    result = _STYLESHEET
    for name, color in colors.items():
        result = result.replace(f"@{name}@", color)
    result = result.replace("@auto_save_weight@", auto_save_weight)
    arrow_name = f"combo-arrow-{normalize_theme(theme_id)}.svg"
    arrow_path = Path(__file__).resolve().parent.parent / "assets" / arrow_name
    result = result.replace("@combo_arrow@", arrow_path.as_posix())
    checkbox_path = Path(__file__).resolve().parent.parent / "assets" / "checkbox-check.svg"
    result = result.replace("@checkbox_check@", checkbox_path.as_posix())
    return result.replace("{{", "{").replace("}}", "}")
