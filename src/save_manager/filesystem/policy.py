from __future__ import annotations

import json
import ntpath
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..domain.errors import PathPolicyError
from ..domain.models import Profile, SourceType, StorageMode

INTERNAL_NAMESPACE_NAMES = frozenset({".save-manager", ".staging", ".trash", "save-manager", "staging", "trash"})


# Handle is unc.
def _is_unc(raw: str) -> bool:
    return raw.startswith(("\\\\", "//"))


# Normalize.
def _norm(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(str(path))))


# Check reparse point.
def is_reparse(path: Path) -> bool:
    junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(junction and junction())


# Handle is windows component.
def is_windows_component_invalid(component: str) -> bool:
    stem = component.rstrip(" .").split(".", 1)[0].upper()
    return not component or component.endswith((".", " ")) or stem in {"CON", "PRN", "AUX", "NUL"} or (stem[:3] in {"COM", "LPT"} and stem[3:].isdigit() and 1 <= int(stem[3:]) <= 9)


# Normalize path component.
def safe_component(value: str, fallback: str = "item") -> str:
    """Normaliza um nome humano para um único componente de caminho Windows."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip().rstrip(" .")
    if not cleaned or is_windows_component_invalid(cleaned):
        cleaned = f"_{cleaned or fallback}"
    return cleaned[:120]


# Mark hidden windows.
def mark_hidden_windows(path: Path) -> None:
    """Oculta metadados internos no Explorer sem alterar o layout dos saves."""
    if os.name != "nt":
        return
    try:
        import ctypes

        get_attrs = ctypes.windll.kernel32.GetFileAttributesW
        set_attrs = ctypes.windll.kernel32.SetFileAttributesW
        attrs = get_attrs(str(path))
        if attrs not in (-1, 0xFFFFFFFF):
            set_attrs(str(path), attrs | 0x2)
    except (AttributeError, OSError, TypeError):
        return


# Handle is within.
def is_within(path: Path, parent: Path) -> bool:
    try:
        _norm(path).relative_to(_norm(parent))
        return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class ResolvedPaths:
    source: Path
    backup_base: Path
    namespace: Path


class PathPolicy:
    # Initialize the object.
    def __init__(self, app_root: Path, data_root: Path | None = None):
        self.app_root = _norm(app_root)
        self.data_root = _norm(data_root) if data_root is not None else self.app_root / "data"
        self.app_backup_root = self.data_root.parent / "Saverino Backup"
        self.legacy_app_folder_backup_root = self.data_root.parent / "Saverino backup"
        self.app_folder_backup_root = self.app_root / "Saverino backup"

    # Validate source.
    def validate_source(
        self,
        source: str | Path,
        source_type: SourceType,
        repositories: list[Path] | None = None,
        *,
        allow_missing: bool = False,
    ) -> Path:
        raw = str(source)
        if _is_unc(raw):
            raise PathPolicyError("Caminhos UNC/rede não são suportados no MVP")
        if len(raw) > 32767:
            raise PathPolicyError("Caminho excede o limite suportado pelo Windows")
        path = _norm(Path(source))
        if (path == self.app_root or is_within(path, self.data_root)
                or is_within(path, self.app_backup_root)
                or is_within(path, self.legacy_app_folder_backup_root)
                or is_within(path, self.app_folder_backup_root)):
            raise PathPolicyError("A origem não pode ser a pasta interna do aplicativo")
        if source_type == SourceType.DIRECTORY and path.parent == path:
            raise PathPolicyError("A raiz de uma unidade não pode ser origem")
        for repo in repositories or []:
            if path == _norm(repo) or is_within(path, repo) or is_within(repo, path):
                raise PathPolicyError("A origem colide com um repositório gerenciado")
        if path.exists():
            if source_type == SourceType.FILE and not path.is_file():
                raise PathPolicyError("A origem selecionada não é um arquivo")
            if source_type == SourceType.DIRECTORY and not path.is_dir():
                raise PathPolicyError("A origem selecionada não é uma pasta")
            if any(part.exists() and is_reparse(part) for part in (path, *path.parents)):
                raise PathPolicyError("Links simbólicos/junctions não são suportados")
            if not os.access(path, os.R_OK):
                raise PathPolicyError("A origem não pode ser lida")
        elif not allow_missing and (path.parent == path or not path.parent.exists()):
            raise PathPolicyError("A origem não existe e seu diretório pai não está disponível")
        return path

    # Resolve backup base.
    def resolve_backup_base(self, profile: Profile, source: Path | None = None) -> Path:
        src = source or _norm(Path(profile.source_path))
        if profile.storage_mode in (StorageMode.APP, StorageMode.APP_FOLDER):
            backup_root = (
                self.app_backup_root
                if profile.storage_mode == StorageMode.APP
                else self.app_folder_backup_root
            )
            if backup_root.exists() and (not backup_root.is_dir() or is_reparse(backup_root)):
                raise PathPolicyError("A pasta padrão de backups não é utilizável")
            return backup_root
        if profile.storage_mode == StorageMode.BESIDE_SOURCE:
            return src.parent
        raw = str(profile.backup_base_path)
        if _is_unc(raw):
            raise PathPolicyError("Destino UNC/rede não é suportado no MVP")
        base = _norm(Path(raw))
        if base == self.data_root or is_within(base, self.data_root):
            raise PathPolicyError("O destino não pode ficar dentro dos dados internos do aplicativo")
        if base.exists() and not base.is_dir():
            raise PathPolicyError("O destino de backups precisa ser uma pasta")
        if base.exists() and is_reparse(base):
            raise PathPolicyError("Links simbólicos/junctions não são suportados no destino")
        return base

    # Resolve.
    def resolve(self, profile: Profile, game_id: str | None = None, *, game_name: str | None = None, use_stored_namespace: bool = True, validate_origin: bool = True) -> ResolvedPaths:
        source = self.validate_source(profile.source_path, profile.source_type) if validate_origin else _norm(Path(profile.source_path))
        base = self.resolve_backup_base(profile, source)
        stored_namespace = self._stored_namespace(profile, base) if use_stored_namespace else None
        game_parent = self.namespace_parent(profile, base, game_name, game_id)
        namespace = stored_namespace or game_parent / safe_component(profile.name, f"profile_{profile.id}")
        if namespace.exists() and is_reparse(namespace):
            raise PathPolicyError("Links simbólicos/junctions não são suportados no perfil")
        if stored_namespace is None and namespace.exists():
            owner = self._namespace_owner(namespace)
            if owner != str(profile.id):
                namespace = game_parent / f"{safe_component(profile.name)}__{str(game_id or profile.game_id)[:8]}"
        if is_within(source, namespace) or is_within(namespace, source):
            raise PathPolicyError("O namespace de backups não pode ficar dentro da origem")
        return ResolvedPaths(source, base, namespace)

    # Resolve parent.
    def namespace_parent(self, profile: Profile, base: Path, game_name: str | None = None, game_id: str | None = None) -> Path:
        """Retorna a pasta que agrupa os perfis de um jogo.

        APP mantém o layout legado; os modos novos agrupam por projeto.
        """
        if profile.storage_mode == StorageMode.APP:
            return base
        resolved_name = game_name or self._stored_game_name(profile)
        if not resolved_name:
            resolved_name = f"game_{str(game_id or profile.game_id)[:8]}"
        return base / f"Saverino - {safe_component(resolved_name)}"

    # Get stored game name.
    def _stored_game_name(self, profile: Profile) -> str | None:
        try:
            value = json.loads((self.profile_data_dir(profile) / "profile.json").read_text(encoding="utf-8")).get("game_name")
            return str(value) if value else None
        except (OSError, TypeError, ValueError):
            return None

    # Handle profile data dir.
    def profile_data_dir(self, profile: Profile) -> Path:
        return self.data_root / "profiles" / str(profile.id)

    # Get metadata dir.
    def metadata_dir(self, profile: Profile) -> Path:
        """Metadados ficam em data/, nunca dentro da pasta visível de backups."""
        return self.profile_data_dir(profile)

    # Get staging dir.
    def staging_dir(self, profile: Profile, operation_id: str) -> Path:
        return self.data_root / "operations" / "staging" / str(profile.id) / operation_id

    # Trash dir.
    def trash_dir(self, profile: Profile) -> Path:
        return self.data_root / "trash" / str(profile.id)

    # Get stored namespace.
    def _stored_namespace(self, profile: Profile, base: Path) -> Path | None:
        path = self.profile_data_dir(profile) / "profile.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw_namespace = data.get("namespace")
            if not raw_namespace:
                return None
            stored = Path(str(raw_namespace))
            if _norm(stored.parent) == _norm(base) or _norm(stored.parent.parent) == _norm(base):
                return stored
            if (profile.storage_mode == StorageMode.APP_FOLDER
                    and stored != self.legacy_app_folder_backup_root
                    and is_within(stored, self.legacy_app_folder_backup_root)):
                return stored
        except (OSError, TypeError, ValueError):
            pass
        return None

    # Resolve owner.
    def _namespace_owner(self, namespace: Path) -> str | None:
        for folder in (".save-manager", "save-manager"):
            legacy_marker = namespace / folder / "profile-id"
            try:
                if legacy_marker.exists():
                    return legacy_marker.read_text(encoding="ascii").strip()
                profile_manifest = namespace / folder / "profile.json"
                if profile_manifest.exists():
                    data = json.loads(profile_manifest.read_text(encoding="utf-8"))
                    return str(data.get("profile_id"))
            except OSError:
                return None
            except (TypeError, ValueError):
                continue
        profiles = self.data_root / "profiles"
        try:
            for manifest_path in profiles.glob("*/profile.json"):
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if _norm(Path(str(data.get("namespace", "")))) == _norm(namespace):
                    return str(data.get("profile_id"))
        except (OSError, TypeError, ValueError):
            return None
        return None

    # Validate manifest relative.
    def validate_manifest_relative(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or any(part in ("", ".", "..") or is_windows_component_invalid(part) for part in path.parts) or ntpath.isabs(relative):
            raise PathPolicyError("Manifesto contém caminho inválido")
        return path
