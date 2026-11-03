from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..filesystem.inventory import build_inventory
from ..filesystem.policy import INTERNAL_NAMESPACE_NAMES


# Match matches manifest.
def inventory_matches_manifest(path: Path, source_type: str, manifest: dict) -> bool:
    """Compara um payload físico com o inventário imutável do manifesto."""
    try:
        inventory = build_inventory(path, source_type, hashes=True)
    except (OSError, RuntimeError, ValueError):
        return False
    expected_files = manifest.get("files", [])
    if source_type == "file":
        actual = next(iter(inventory.files), None)
        expected = next(iter(expected_files), None)
        return bool(
            actual
            and isinstance(expected, dict)
            and expected.get("size") == actual.size
            and expected.get("sha256") == actual.sha256
        )
    actual_files = {entry.relative: entry for entry in inventory.files}
    expected_by_path = {
        str(item.get("path", "")).replace("\\", "/"): item
        for item in expected_files
        if isinstance(item, dict)
    }
    if set(expected_by_path) != set(actual_files):
        return False
    expected_dirs = {str(item).replace("\\", "/") for item in manifest.get("directories", [])}
    actual_dirs = {entry.relative for entry in inventory.entries if entry.kind == "directory"}
    if expected_dirs != actual_dirs:
        return False
    return all(
        item.get("size") == actual_files[relative].size
        and item.get("sha256") == actual_files[relative].sha256
        for relative, item in expected_by_path.items()
    )


# Find matching payloads.
def matching_payloads(namespace: Path, source_type: str, manifest: dict, claimed: Iterable[str] = ()) -> list[Path]:
    """Localiza payloads renomeados diretamente dentro de um namespace."""
    claimed_names = {str(name).casefold() for name in claimed}
    try:
        children = sorted(namespace.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return []
    result: list[Path] = []
    for child in children:
        if child.name.casefold() in INTERNAL_NAMESPACE_NAMES or child.name.casefold() in claimed_names:
            continue
        if source_type == "file" and not child.is_file():
            continue
        if source_type == "directory" and not child.is_dir():
            continue
        if inventory_matches_manifest(child, source_type, manifest):
            result.append(child)
    return result
