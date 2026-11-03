"""About dialog with the bundled application logo."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout

from ..i18n import LanguageManager
from ..version import APP_VERSION
from .assets import resolve_asset
from .dialogs import equalize_button_widths

GITHUB_URL = "https://github.com/mmachida/Saverino-Manager"
DONATION_URL = "https://ko-fi.com/mmachida"


class AboutDialog(QDialog):
    """Display application identity and project links."""

    # Initialize the dialog.
    def __init__(self, parent=None, *, translator=None, github_url: str = GITHUB_URL, donation_url: str = DONATION_URL):
        super().__init__(parent)
        self.translator = translator or LanguageManager(parent=self)
        self.github_url = github_url
        self.donation_url = donation_url
        icon_path = resolve_asset("icon.ico")
        if icon_path is not None:
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumWidth(470)
        self.resize(520, 235)

        root = QVBoxLayout(self)
        content = QHBoxLayout()
        self.logo = QLabel()
        self.logo.setObjectName("about_logo")
        self.logo.setAccessibleName(self.translator.tr("dialog.about_logo_alt"))
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo.setFixedSize(88, 88)
        self._load_logo()
        content.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignTop)

        details = QVBoxLayout()
        self.app_name = QLabel()
        self.app_name.setObjectName("about_app_name")
        details.addWidget(self.app_name)
        self.version = QLabel()
        details.addWidget(self.version)
        self.description = QLabel()
        self.description.setWordWrap(True)
        details.addWidget(self.description)
        self.github = QLabel()
        self.github.setOpenExternalLinks(True)
        self.github.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        details.addWidget(self.github)
        self.donation = QLabel()
        self.donation.setOpenExternalLinks(True)
        self.donation.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        details.addWidget(self.donation)
        details.addStretch(1)
        content.addLayout(details, 1)
        root.addLayout(content)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons, 0, Qt.AlignmentFlag.AlignRight)
        self.translator.language_changed.connect(self.retranslate_ui)
        self.retranslate_ui(self.translator.language)

    # Load a bundled logo when available.
    def _load_logo(self) -> None:
        path = self._logo_path()
        if path is None:
            return
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            self.logo.setPixmap(pixmap.scaled(self.logo.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    # Find the application logo.
    @staticmethod
    def _logo_path() -> Path | None:
        return resolve_asset("logo.png")

    # Refresh translated dialog labels.
    def retranslate_ui(self, _language: str | None = None) -> None:
        t = self.translator.tr
        self.setWindowTitle(t("dialog.about_title"))
        self.logo.setAccessibleName(t("dialog.about_logo_alt"))
        self.app_name.setText(t("app.title"))
        self.version.setText(t("dialog.about_version", version=APP_VERSION))
        self.description.setText(t("dialog.about_description"))
        self.github.setText(f'<a href="{self.github_url}">{t("dialog.about_github")}</a>')
        self.github.setToolTip(t("dialog.about_github_tooltip"))
        self.donation.setText(f'<a href="{self.donation_url}">{t("dialog.about_donation")}</a>')
        self.donation.setToolTip(t("dialog.about_donation_tooltip"))
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText(t("common.close"))
        equalize_button_widths(self.buttons.buttons())
