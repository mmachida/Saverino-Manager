from __future__ import annotations

import json
import ntpath
import os
import tempfile
from pathlib import Path
from typing import Any

from ..domain.errors import IntegrityError
from ..filesystem.inventory import Inventory
from ..filesystem.policy import is_windows_component_invalid

CURRENT_FORMAT = 1


# Build from inventory.
def manifest_from_inventory(*, snapshot_id: str, profile_id: str, game_id: str, name: str, notes: str, kind: str, created_at: str, source_type: str, original_source_path: str, inventory: Inventory) -> dict[str, Any]:
    return {
        "format_version": CURRENT_FORMAT, "snapshot_id": snapshot_id, "profile_id": profile_id, "game_id": game_id,
        "name": name, "notes": notes, "kind": kind, "created_at": created_at, "source_type": source_type,
        "original_source_path": original_source_path, "original_basename": Path(original_source_path).name,
        "total_bytes": inventory.total_bytes, "file_count": len(inventory.files),
        "directories": [e.relative for e in inventory.entries if e.kind == "directory"],
        "files": [{"path": e.relative, "size": e.size, "sha256": e.sha256} for e in inventory.files],
    }


# Write manifest.
def write_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


# Validate manifest.
def validate_manifest(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("format_version") != CURRENT_FORMAT:
        raise IntegrityError("Versão de manifesto desconhecida")
    if "snapshot_id" not in data and "profile_id" in data:
        for key in ("profile_id", "game_id", "name", "source_type", "source_path"):
            if not data.get(key):
                raise IntegrityError("Manifesto de perfil incompleto")
        return data
    required = ("snapshot_id", "profile_id", "game_id", "source_type", "files", "directories")
    if any(key not in data for key in required):
        raise IntegrityError("Manifesto incompleto")
    seen: set[str] = set()
    try:
        paths = list(data["directories"]) + [item["path"] for item in data["files"]]
    except (KeyError, TypeError) as exc:
        raise IntegrityError("Manifesto contém entradas inválidas") from exc
    for rel in paths:
        p = Path(str(rel).replace("\\", "/"))
        if p.is_absolute() or ntpath.isabs(str(rel)) or ".." in p.parts or any(is_windows_component_invalid(part) for part in p.parts) or not rel or rel.casefold() in seen:
            raise IntegrityError("Manifesto contém traversal ou colisão")
        seen.add(rel.casefold())
    for item in data["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("size"), int) or item["size"] < 0 or not isinstance(item.get("sha256"), str) or len(item["sha256"]) != 64:
            raise IntegrityError("Manifesto contém metadados de arquivo inválidos")
    if int(data.get("file_count", len(data["files"]))) != len(data["files"]):
        raise IntegrityError("Contagem de arquivos do manifesto não confere")
    return data


# Read manifest.
def read_manifest(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            return validate_manifest(json.load(stream))
    except (OSError, ValueError, TypeError) as exc:
        raise IntegrityError(f"Manifesto inválido: {path}") from exc
