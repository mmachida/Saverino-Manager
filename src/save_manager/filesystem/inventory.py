from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from ..domain.errors import IntegrityError
from .policy import is_reparse, is_windows_component_invalid


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    relative: str
    kind: str  # file ou directory
    size: int = 0
    mtime_ns: int = 0
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class Inventory:
    root: Path
    source_kind: str
    entries: tuple[InventoryEntry, ...]

    @property
    # List package files.
    def files(self) -> tuple[InventoryEntry, ...]:
        return tuple(item for item in self.entries if item.kind == "file")

    @property
    # Count inventory bytes.
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)

    @property
    # Count inventory files.
    def file_count(self) -> int:
        return len(self.files)


# Estimate required bytes.
def estimate_required_bytes(inventory: Inventory, *, retained_bytes: int = 0) -> int:
    """Estimativa conservadora para staging e retenção do estado atual."""
    return inventory.total_bytes + max(0, retained_bytes)


# Calculate.
def _digest(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


# Build inventory.
def build_inventory(root: Path, source_kind: str, *, hashes: bool = True) -> Inventory:
    root = Path(root)
    if is_reparse(root):
        raise IntegrityError(f"Reparse point não suportado: {root}")
    root = root.resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    if source_kind == "file":
        stat = root.stat()
        return Inventory(root, source_kind, (InventoryEntry(root.name, "file", stat.st_size, stat.st_mtime_ns, _digest(root) if hashes else None),))
    if not root.is_dir():
        raise IntegrityError("Origem não corresponde a uma pasta")
    result: list[InventoryEntry] = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs.sort(key=str.casefold)
        files.sort(key=str.casefold)
        for name in list(dirs):
            path = current_path / name
            if is_reparse(path):
                raise IntegrityError(f"Reparse point não suportado: {path}")
            rel = path.relative_to(root).as_posix()
            if any(is_windows_component_invalid(part) for part in Path(rel).parts):
                raise IntegrityError(f"Nome incompatível com Windows: {rel}")
            result.append(InventoryEntry(rel, "directory", 0, path.stat().st_mtime_ns))
        for name in files:
            path = current_path / name
            if is_reparse(path) or not path.is_file():
                raise IntegrityError(f"Tipo de arquivo não suportado: {path}")
            stat = path.stat()
            rel = path.relative_to(root).as_posix()
            if any(is_windows_component_invalid(part) for part in Path(rel).parts):
                raise IntegrityError(f"Nome incompatível com Windows: {rel}")
            result.append(InventoryEntry(rel, "file", stat.st_size, stat.st_mtime_ns, _digest(path) if hashes else None))
    # Windows não distingue maiúsculas/minúsculas: colisões seriam ambíguas.
    folded: set[str] = set()
    for entry in result:
        key = entry.relative.casefold()
        if key in folded:
            raise IntegrityError(f"Colisão de nomes no inventário: {entry.relative}")
        folded.add(key)
    return Inventory(root, source_kind, tuple(result))
