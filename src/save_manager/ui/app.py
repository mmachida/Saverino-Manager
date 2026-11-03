from __future__ import annotations

import sys


# Run the worker.
def run() -> int:
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError as exc:
        print(f"PySide6 could not be loaded: {exc}", file=sys.stderr)
        return 1
    from PySide6.QtGui import QIcon

    from .assets import resolve_asset
    from .main_window import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("Saverino Manager")
    icon_path = resolve_asset("icon.ico")
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))
    try:
        window = MainWindow()
    except RuntimeError as exc:
        QMessageBox.critical(None, "Saverino Manager", str(exc))
        return 1
    window.show()
    # The update helper waits for this marker before deleting the rollback
    # copy.  It is a no-op on ordinary launches.
    from ..updates.config import UpdateConfig
    from ..updates.installer import (
        confirm_pending_startup,
        default_install_root,
        ensure_initial_state,
        recover_pending_install,
    )
    from ..version import APP_VERSION

    try:
        recover_pending_install()
        ensure_initial_state(default_install_root(), UpdateConfig().package_id, APP_VERSION)
    except OSError:
        # A read-only installation will be reported by the update UI before
        # the app is closed; startup itself must remain usable.
        pass
    confirm_pending_startup()
    return app.exec()
