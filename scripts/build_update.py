"""Gera o ZIP e o checksum de uma distribuição do Saverino Manager.

Uso típico: ``python scripts/build_update.py --source dist/release``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from save_manager.updates.config import platform_package_id
from save_manager.version import APP_VERSION

EXCLUDED_TOP_LEVEL = {
    "data",
    "backups",
    "saverino backup",
    "logs",
    ".git",
    ".save-manager-update-backups",
}


# Handle files.
def _files(source: Path) -> list[tuple[str, Path]]:
    result = []
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(source).as_posix()
        if relative == ".save-manager-update-state.json" or relative.startswith("."):
            continue
        if relative.split("/", 1)[0].casefold() in EXCLUDED_TOP_LEVEL:
            continue
        result.append((relative, path))
    return result


# Build package.
def build_package(source: Path, output: Path, version: str = APP_VERSION, package_id: str | None = None) -> tuple[Path, Path]:
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_dir():
        raise ValueError(f"Pasta de build não encontrada: {source}")
    package_id = package_id or platform_package_id()
    files = _files(source)
    if not files:
        raise ValueError("A pasta de build não contém arquivos do programa.")
    output.mkdir(parents=True, exist_ok=True)
    package_name = f"{package_id}.zip"
    archive_path = output / package_name
    manifest = {
        "format": 1,
        "version": version.removeprefix("v"),
        "package_id": package_id,
        "files": [relative for relative, _ in files],
    }
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("update-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        for relative, path in files:
            archive.write(path, relative)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = output / f"{package_id}.sha256"
    checksum_path.write_text(f"{digest}  {package_name}\n", encoding="utf-8")
    return archive_path, checksum_path


# Run.
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Saverino Manager update package")
    parser.add_argument("--source", type=Path, required=True, help="pasta com o aplicativo empacotado")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "releases")
    parser.add_argument("--version", default=APP_VERSION)
    parser.add_argument("--package-id", default=platform_package_id())
    args = parser.parse_args(argv)
    archive, checksum = build_package(args.source, args.output, args.version, args.package_id)
    print(f"Created {archive}")
    print(f"Created {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
