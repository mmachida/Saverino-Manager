"""Identidade estável durante renomes, sem abrir ou modificar o payload."""

from pathlib import Path

from .policy import is_reparse


# Handle filesystem identity.
def filesystem_identity(path: Path) -> list[int] | None:
    try:
        if any(is_reparse(part) for part in (path, *path.parents)):
            return None
        info = path.stat(follow_symlinks=False)
        if not info.st_ino:
            return None
        # O horário de criação evita confundir IDs reutilizados após exclusão.
        return [info.st_dev, info.st_ino, getattr(info, "st_birthtime_ns", 0)]
    except OSError:
        return None


# List visible children.
def visible_children(path: Path, source_type: str) -> list[Path]:
    """Preserva a grafia real (inclusive caixa no Windows) e recusa links."""
    from .policy import INTERNAL_NAMESPACE_NAMES

    if any(is_reparse(part) for part in (path, *path.parents)):
        return []
    return [
        child for child in path.iterdir()
        if child.name.casefold() not in INTERNAL_NAMESPACE_NAMES
        and not is_reparse(child)
        and (child.is_file() if source_type == "file" else child.is_dir())
    ]
