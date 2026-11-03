"""Cópia de repositórios com verificação integral, preservando a origem."""

from dataclasses import dataclass
from pathlib import Path

from ..domain.errors import IntegrityError
from .inventory import build_inventory
from .operations import copy_bytes
from .policy import is_reparse, is_within


@dataclass(frozen=True, slots=True)
class MigrationProgress:
    percent: int
    relative: str


# Copy directory verified.
def copy_directory_verified(source: Path, target: Path, *, progress=None) -> None:
    if is_within(target, source) or any(is_reparse(path) for path in (source, *source.parents, target, *target.parents)):
        raise IntegrityError("Caminho inválido para copiar os backups")
    before = build_inventory(source, "directory", hashes=False)
    target.mkdir(parents=True, exist_ok=False)
    weights = [max(entry.size, 1) if entry.kind == "file" else 1 for entry in before.entries]
    total_units = sum(weights)
    completed_units = 0

    # Report copy progress.
    def report(units: int, relative: str) -> None:
        if progress:
            percent = round(min(units, total_units) * 90 / total_units) if total_units else 0
            progress(MigrationProgress(percent, relative))

    for entry, weight in zip(before.entries, weights):
        original, copied = source / entry.relative, target / entry.relative
        if any(is_reparse(path) for path in (original, *original.parents)):
            raise IntegrityError("A pasta de backups mudou durante a cópia")
        if entry.kind == "directory":
            copied.mkdir(parents=True, exist_ok=True)
        else:
            copied.parent.mkdir(parents=True, exist_ok=True)
            if entry.size:
                copy_bytes(
                    original,
                    copied,
                    progress=lambda copied_bytes, base=completed_units, relative=entry.relative:
                        report(base + copied_bytes, relative),
                )
            else:
                copy_bytes(original, copied)
        completed_units += weight
        if progress:
            report(completed_units, entry.relative)
    if not before.entries:
        report(0, "")
    after = build_inventory(source, "directory", hashes=False)
    if progress:
        progress(MigrationProgress(95, ""))
    before_verified = build_inventory(source, "directory", hashes=True)
    copied = build_inventory(target, "directory", hashes=True)
    expected = {(entry.relative, entry.kind, entry.size, entry.sha256) for entry in before_verified.entries}
    if expected != {(entry.relative, entry.kind, entry.size, entry.sha256) for entry in copied.entries}:
        raise IntegrityError("A cópia dos backups não passou na verificação")
    if before.entries != after.entries:
        raise IntegrityError("Os backups mudaram durante a cópia; tente novamente")
    if progress:
        progress(MigrationProgress(100, ""))
