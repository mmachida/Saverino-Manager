from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class JournalStore:
    # Initialize the object.
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # Build operation path.
    def path(self, operation_id: str) -> Path:
        return self.root / f"{operation_id}.json"

    # Write.
    def write(self, operation_id: str, data: dict[str, Any]) -> Path:
        target = self.path(operation_id)
        fd, temp_name = tempfile.mkstemp(prefix=f".{operation_id}.", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name): os.unlink(temp_name)
        return target

    # Read.
    def read(self, operation_id: str) -> dict[str, Any]:
        with self.path(operation_id).open(encoding="utf-8") as stream:
            return json.load(stream)

    # Update phase.
    def update_phase(self, operation_id: str, phase: str, **fields: Any) -> Path:
        data = self.read(operation_id)
        data.update(fields)
        data["phase"] = phase
        return self.write(operation_id, data)

    # Find pending.
    def pending(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.root.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if value.get("phase") not in ("completed", "failed"):
                    result.append(value)
            except (OSError, ValueError):
                result.append({"phase": "recovery_required", "path": str(path)})
        return result

    # Complete.
    def complete(self, operation_id: str) -> None:
        try: self.path(operation_id).unlink()
        except FileNotFoundError: pass
