from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..domain.errors import IntegrityError, OperationError
from .inventory import Inventory, build_inventory
from .policy import is_reparse


# Flush.
def _flush(path: Path) -> None:
    try:
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
    except (OSError, ValueError):
        pass


# Copy bytes.
def copy_bytes(source: Path, target: Path, chunk_size: int = 1024 * 1024, cancel=None, progress=None) -> None:
    with source.open("rb") as src, target.open("wb") as dst:
        copied = 0
        while block := src.read(chunk_size):
            if cancel and cancel():
                raise OperationError("Operação cancelada")
            dst.write(block)
            copied += len(block)
            if progress:
                progress(copied)
        dst.flush()
        try:
            os.fsync(dst.fileno())
        except OSError:
            pass


# Copy snapshot payload.
def copy_snapshot_payload(source: Path, source_kind: str, payload: Path, *, progress=None, cancel=None) -> Inventory:
    payload.mkdir(parents=True, exist_ok=False)
    if source_kind == "file":
        target = payload / "save.bin"
        copy_bytes(source, target, cancel=cancel)
        shutil.copystat(source, target, follow_symlinks=False)
        _flush(target)
    else:
        tree = payload / "tree"
        tree.mkdir()
        for item in build_inventory(source, source_kind, hashes=False).entries:
            if cancel and cancel():
                raise OperationError("Operação cancelada")
            src = source / item.relative
            dst = tree / item.relative
            if item.kind == "directory":
                dst.mkdir(parents=True, exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                copy_bytes(src, dst)
                shutil.copystat(src, dst, follow_symlinks=False)
                _flush(dst)
            if progress:
                progress(item)
    return build_inventory(payload / "save.bin" if source_kind == "file" else payload / "tree", source_kind, hashes=True)


# Verify payload.
def verify_payload(payload: Path, source_kind: str, expected: Inventory) -> Inventory:
    actual = build_inventory(payload / "save.bin" if source_kind == "file" else payload / "tree", source_kind, hashes=True)
    exp = {(e.relative, e.kind, e.size, e.sha256) for e in expected.entries}
    got = {(e.relative, e.kind, e.size, e.sha256) for e in actual.entries}
    if exp != got:
        raise IntegrityError("Payload não corresponde ao inventário esperado")
    return actual


# Remove tree safe.
def remove_tree_safe(path: Path, allowed_root: Path) -> None:
    raw_path = Path(path)
    if is_reparse(raw_path):
        raise IntegrityError("Recusa de seguir link durante exclusão")
    path = raw_path.resolve()
    allowed_root = allowed_root.resolve()
    try:
        path.relative_to(allowed_root)
    except ValueError as exc:
        raise OperationError("Recusa de exclusão fora da raiz permitida") from exc
    if path == allowed_root:
        raise OperationError("Recusa de excluir a raiz gerenciada")
    if not path.exists():
        return
    if is_reparse(path):
        raise IntegrityError("Recusa de seguir link durante exclusão")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
