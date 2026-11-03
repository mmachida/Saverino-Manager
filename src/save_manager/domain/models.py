from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .errors import DomainError

_WINDOWS_FOLDER_INVALID = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL"})


# Validate folder name.
def validate_folder_name(value: str, label: str = "Nome") -> str:
    """Valida um nome que será usado como componente de pasta no Windows."""
    clean = validate_name(value, label)
    return _validate_windows_component(clean, label, "pasta")


class SourceType(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


class StorageMode(StrEnum):
    APP = "app"  # legado; não é oferecido pela interface nova
    APP_FOLDER = "app_folder"
    BESIDE_SOURCE = "beside_source"
    CUSTOM = "custom"


class SnapshotKind(StrEnum):
    MANUAL = "manual"
    SAFETY = "safety"


class SnapshotStatus(StrEnum):
    READY = "ready"
    MISSING = "missing"
    CORRUPT = "corrupt"
    TRASHED = "trashed"


# Get current UTC time.
def utc_now() -> datetime:
    return datetime.now(UTC)


# Get current ISO time.
def iso_now() -> str:
    return utc_now().isoformat()


# Normalize name.
def normalize_name(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


# Validate name.
def validate_name(value: str, label: str = "Nome") -> str:
    if not isinstance(value, str):
        raise DomainError(f"{label} deve ser texto")
    clean = value.strip()
    if not 1 <= len(clean) <= 100:
        raise DomainError(f"{label} deve ter entre 1 e 100 caracteres")
    if any(ord(ch) < 32 for ch in clean):
        raise DomainError(f"{label} não pode conter controles")
    return clean


# Validate file name.
def validate_file_name(value: str, label: str = "Nome do arquivo") -> str:
    """Valida um nome que será usado como arquivo no Windows."""
    clean = validate_name(value, label)
    return _validate_windows_component(clean, label, "arquivo")


# Normalize snapshot display name.
def normalize_snapshot_name(
    value: str,
    source_type: SourceType | str,
    source_path: str | Path | None = None,
) -> str:
    """Preserve the complete snapshot filename."""
    return validate_file_name(value, "Nome do save")


def _validate_windows_component(clean: str, label: str, kind: str) -> str:
    if any(char in _WINDOWS_FOLDER_INVALID or ord(char) < 32 for char in clean):
        raise DomainError(f"{label} contém caracteres inválidos para nome de {kind}")
    if clean.endswith((".", " ")):
        raise DomainError(f"{label} não pode terminar com ponto ou espaço")
    stem = clean.rstrip(" .").split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES or (stem[:3] in {"COM", "LPT"} and stem[3:].isdigit() and 1 <= int(stem[3:]) <= 9):
        raise DomainError(f"{label} usa um nome reservado pelo Windows")
    return clean


# Validate notes.
def validate_notes(value: str | None) -> str:
    clean = (value or "").strip()
    if len(clean) > 2000:
        raise DomainError("Nota deve ter no máximo 2.000 caracteres")
    if any(ord(ch) < 32 and ch not in "\r\n\t" for ch in clean):
        raise DomainError("Nota não pode conter controles")
    return clean


@dataclass(slots=True)
class GameSettings:
    source_type: SourceType
    source_path: str
    storage_mode: StorageMode = StorageMode.BESIDE_SOURCE
    backup_base_path: str = ""

    # Validate initialized fields.
    def __post_init__(self) -> None:
        self.source_type = SourceType(self.source_type)
        self.storage_mode = StorageMode(self.storage_mode)
        if not self.source_path:
            raise DomainError("Selecione o arquivo ou pasta de save do jogo")
        if self.storage_mode == StorageMode.CUSTOM and not self.backup_base_path:
            raise DomainError("Selecione a pasta de backups do jogo")


@dataclass(slots=True)
class Game:
    name: str
    id: UUID = field(default_factory=uuid4)
    created_at: str = field(default_factory=iso_now)
    updated_at: str = field(default_factory=iso_now)
    archived_at: str | None = None

    # Validate initialized fields.
    def __post_init__(self) -> None:
        self.name = validate_name(self.name, "Nome do jogo")

    @property
    # Get normalized name.
    def normalized_name(self) -> str:
        return normalize_name(self.name)


@dataclass(slots=True)
class Profile:
    game_id: UUID
    name: str
    source_type: SourceType
    source_path: str
    storage_mode: StorageMode
    backup_base_path: str
    notes: str = ""
    id: UUID = field(default_factory=uuid4)
    created_at: str = field(default_factory=iso_now)
    updated_at: str = field(default_factory=iso_now)
    archived_at: str | None = None

    # Validate initialized fields.
    def __post_init__(self) -> None:
        self.name = validate_name(self.name, "Nome do perfil")
        self.notes = validate_notes(self.notes)
        self.source_type = SourceType(self.source_type)
        self.storage_mode = StorageMode(self.storage_mode)
        if not self.source_path:
            raise DomainError("Origem é obrigatória")
        if self.storage_mode == StorageMode.CUSTOM and not self.backup_base_path:
            raise DomainError("Destino personalizado é obrigatório")

    @property
    # Get normalized name.
    def normalized_name(self) -> str:
        return normalize_name(self.name)


@dataclass(slots=True)
class Snapshot:
    profile_id: UUID
    name: str
    kind: SnapshotKind = SnapshotKind.MANUAL
    notes: str = ""
    total_bytes: int = 0
    file_count: int = 0
    relative_path: str = ""
    status: SnapshotStatus = SnapshotStatus.READY
    id: UUID = field(default_factory=uuid4)
    created_at: str = field(default_factory=iso_now)
    last_loaded_at: str | None = None

    # Validate initialized fields.
    def __post_init__(self) -> None:
        self.name = validate_name(self.name, "Nome do save")
        self.notes = validate_notes(self.notes)
        self.kind = SnapshotKind(self.kind)
        self.status = SnapshotStatus(self.status)

    @property
    # Get normalized name.
    def normalized_name(self) -> str:
        return normalize_name(self.name)

    # Serialize object data.
    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id), "profile_id": str(self.profile_id), "name": self.name,
            "notes": self.notes, "kind": self.kind.value, "created_at": self.created_at,
            "total_bytes": self.total_bytes, "file_count": self.file_count,
            "relative_path": self.relative_path, "status": self.status.value,
            "last_loaded_at": self.last_loaded_at,
        }


