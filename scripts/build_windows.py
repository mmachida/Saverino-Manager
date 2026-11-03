"""Build the Windows app/helper and create its release assets.

PyInstaller is intentionally a build-time dependency. The user's machine only
receives the resulting executables and never needs Python installed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from build_update import build_package

from save_manager.updates.config import APP_EXECUTABLE_NAME, UPDATER_EXECUTABLE_NAME, platform_package_id
from save_manager.version import APP_VERSION


# Run.
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Saverino Manager for Windows")
    parser.add_argument("--version", default=APP_VERSION)
    parser.add_argument("--package-id", default=platform_package_id())
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    work = ROOT / "build" / "pyinstaller"
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    app_spec = work / "SaverinoManager.spec"
    app_spec.write_text(
        f"""# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    [{str(ROOT / '__main__.py')!r}],
    pathex=[],
    binaries=[],
    datas=[
        ({str(ROOT / 'src' / 'save_manager' / 'sfx')!r}, 'save_manager/sfx'),
        ({str(ROOT / 'src' / 'save_manager' / 'locales')!r}, 'save_manager/locales'),
        ({str(ROOT / 'src' / 'save_manager' / 'assets')!r}, 'save_manager/assets'),
        ({str(ROOT / 'icon')!r}, 'icon'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Ignore incompatible ICU binaries inherited from the build environment.
_blocked = {{'icuuc.dll', 'icudt78.dll'}}
a.binaries = [
    item for item in a.binaries
    if item[0].replace('\\\\', '/').rsplit('/', 1)[-1].lower() not in _blocked
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name={APP_EXECUTABLE_NAME.removesuffix('.exe')!r},
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[{str(ROOT / 'icon' / 'logo256.ico')!r}],
)
""",
        encoding="utf-8",
    )
    app_command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(output),
        "--workpath",
        str(work),
        str(app_spec),
    ]
    helper_command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--icon",
        str(ROOT / "icon" / "logo256.ico"),
        "--distpath",
        str(output),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        "--name",
        UPDATER_EXECUTABLE_NAME.removesuffix(".exe"),
        str(ROOT / "scripts" / "update_helper_entry.py"),
    ]
    subprocess.run(app_command, cwd=ROOT, check=True)
    subprocess.run(helper_command, cwd=ROOT, check=True)
    package_source = output / "release"
    if package_source.exists():
        shutil.rmtree(package_source)
    package_source.mkdir()
    for executable in (output / APP_EXECUTABLE_NAME, output / UPDATER_EXECUTABLE_NAME):
        if not executable.is_file():
            raise RuntimeError(f"Build não gerou {executable.name}")
        shutil.copy2(executable, package_source / executable.name)
    archive, checksum = build_package(package_source, output / "releases", args.version, args.package_id)
    print(f"Release assets: {archive}\n{checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
