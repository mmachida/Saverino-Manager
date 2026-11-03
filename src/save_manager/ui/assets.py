"""Resolve interface assets in source and packaged application layouts."""

from __future__ import annotations

import sys
from pathlib import Path


# Resolve a bundled or source asset.
def resolve_asset(filename: str) -> Path | None:
    """Return the first existing path for an application asset."""
    aliases = {
        "logo.png": ("logo.png", "logo256.png"),
        "icon.ico": ("icon.ico", "logo256.ico"),
    }
    names = aliases.get(filename, (filename,))
    module_path = Path(__file__).resolve()
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        bundle = Path(bundle_root)
        candidates.extend(bundle / "icon" / name for name in names)
        candidates.extend(bundle / "save_manager" / "assets" / name for name in names)
    candidates.extend(module_path.parent.parent / "assets" / name for name in names)
    executable_dir = Path(sys.executable).resolve().parent
    candidates.extend(executable_dir / "icon" / name for name in names)
    candidates.extend(executable_dir / "save_manager" / "assets" / name for name in names)
    candidates.extend(module_path.parents[3] / "icon" / name for name in names)
    return next((candidate for candidate in candidates if candidate.is_file()), None)
