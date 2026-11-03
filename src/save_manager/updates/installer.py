"""Transação de instalação e recuperação do pacote de atualização."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import (
    APP_EXECUTABLE_NAME,
    LEGACY_APP_EXECUTABLE_NAME,
    LEGACY_UPDATER_EXECUTABLE_NAME,
    UPDATER_EXECUTABLE_NAME,
    UpdateConfig,
)
from .service import MAX_METADATA_BYTES, ReleaseAsset, ReleaseInfo, UpdateError, UpdateService, parse_version

LOGGER = logging.getLogger("save_manager.updates")
STATE_FILENAME = ".save-manager-update-state.json"
BACKUP_DIRECTORY = ".save-manager-update-backups"
CONFIRMATION_ENV = "SAVE_MANAGER_UPDATE_STATE"
ACTIVE_UPDATE_STATUSES = frozenset({"installing", "awaiting_confirmation"})
MANAGED_EXECUTABLE_NAMES = (
    APP_EXECUTABLE_NAME,
    UPDATER_EXECUTABLE_NAME,
    LEGACY_APP_EXECUTABLE_NAME,
    LEGACY_UPDATER_EXECUTABLE_NAME,
)


# Get current UTC time.
def utc_now() -> str:
    return datetime.now(UTC).isoformat()


# Get default install root.
def default_install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


# Write atomically json write.
def atomic_json_write(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


# Read state.
def read_state(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


# Build managed baseline.
def _baseline_managed_files(root: Path, executable_name: str = APP_EXECUTABLE_NAME) -> dict[str, str]:
    root = Path(root).resolve()
    names = (executable_name, *MANAGED_EXECUTABLE_NAMES)
    managed: dict[str, str] = {}
    for name in dict.fromkeys(names):
        candidate = root / name
        if candidate.is_file():
            managed[name] = _sha256(candidate)
    return managed


# Check state metadata.
def _state_metadata_is_valid(
    state: dict[str, object] | None,
    root: Path,
    package_id: str,
) -> bool:
    if not state or state.get("status") in ACTIVE_UPDATE_STATUSES:
        return False
    managed = _managed_files(state)
    if not managed:
        return False
    if state.get("package_id") not in (None, "", package_id):
        return False
    recorded_root = state.get("install_root")
    if recorded_root:
        try:
            if Path(str(recorded_root)).resolve() != Path(root).resolve():
                return False
        except OSError:
            return False
    for relative, digest in managed.items():
        if not relative or Path(relative).name != relative or "/" in relative or "\\" in relative:
            return False
        if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
            return False
    return True


# Ensure initial state.
def ensure_initial_state(
    root: Path,
    package_id: str,
    version: str,
    *,
    executable_name: str = APP_EXECUTABLE_NAME,
    state_path: Path | None = None,
) -> None:
    """Create or repair the managed-file baseline for an installation."""
    root = Path(root).resolve()
    state_path = Path(state_path or root / STATE_FILENAME)
    current = read_state(state_path)
    if current and current.get("status") in ACTIVE_UPDATE_STATUSES:
        return
    baseline = _baseline_managed_files(root, executable_name)
    if not baseline:
        return
    if _state_metadata_is_valid(current, root, package_id):
        managed = _managed_files(current)
        if current.get("status") == "confirmed":
            return
    else:
        managed = baseline
    try:
        normalized_version = str(parse_version(version))
    except UpdateError:
        normalized_version = "0.0.0"
    repaired = {
        "status": "confirmed",
        "version": normalized_version,
        "package_id": package_id,
        "install_root": str(root),
        "managed_files": managed,
        "created_at": str(current.get("created_at") or utc_now()) if current else utc_now(),
        "repaired_at": utc_now(),
    }
    atomic_json_write(state_path, repaired)


@dataclass(frozen=True)
class UpdateTransaction:
    state_path: Path
    backup_dir: Path
    install_root: Path
    installed_files: tuple[str, ...]
    previous_state: dict[str, object] | None


# Check install prerequisites.
def check_install_prerequisites(archive_path: Path, install_root: Path) -> None:
    """Check resources before asking the running application to close.

    The helper repeats these checks immediately before replacing files. This
    early check lets the UI report a full or read-only installation directory
    while the current process is still available to show the error.
    """
    archive_path = Path(archive_path)
    root = Path(install_root).resolve()
    if not archive_path.is_file():
        raise UpdateError("O pacote de atualização não está disponível.")
    if not root.is_dir():
        raise UpdateError("A pasta de instalação não existe.")
    try:
        free_space = shutil.disk_usage(root).free
        if free_space < max(archive_path.stat().st_size * 2, 16 * 1024 * 1024):
            raise UpdateError("Não há espaço livre suficiente para atualizar com recuperação.")
        descriptor, probe_name = tempfile.mkstemp(prefix=".save-manager-update-check-", dir=root)
        os.close(descriptor)
        try:
            Path(probe_name).unlink()
        except OSError:
            pass
    except UpdateError:
        raise
    except OSError as exc:
        raise UpdateError("A pasta de instalação não permite preparar a atualização.") from exc


# Calculate SHA-256.
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Get managed files.
def _managed_files(state: dict[str, object] | None) -> dict[str, str]:
    if not state or not isinstance(state.get("managed_files"), dict):
        return {}
    return {str(path): str(digest) for path, digest in state["managed_files"].items() if isinstance(path, str) and isinstance(digest, str)}


# Validate safe target.
def _safe_target(root: Path, relative: str) -> Path:
    target = root.joinpath(*relative.split("/"))
    try:
        resolved = target.resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise UpdateError("O pacote tenta sair da pasta de instalação.")
    except OSError as exc:
        raise UpdateError("Não foi possível validar o destino da atualização.") from exc
    for parent in [target, *target.parents]:
        if parent == root:
            break
        if parent.exists() and parent.is_symlink():
            raise UpdateError("O destino da atualização contém um link simbólico.")
    return target


# Handle release from archive.
def _release_from_archive(archive: Path, config: UpdateConfig, expected_version: str | None) -> ReleaseInfo:
    try:
        import zipfile

        with zipfile.ZipFile(archive) as source:
            with source.open("update-manifest.json") as manifest_stream:
                raw_manifest = manifest_stream.read(MAX_METADATA_BYTES + 1)
            if len(raw_manifest) > MAX_METADATA_BYTES:
                raise UpdateError("O manifesto da atualização é grande demais.")
            manifest = json.loads(raw_manifest.decode("utf-8"))
    except (OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise UpdateError("O manifesto da atualização está ausente ou inválido.") from exc
    if not isinstance(manifest, dict):
        raise UpdateError("O manifesto da atualização é inválido.")
    version = parse_version(manifest.get("version"))
    if expected_version is not None and version != parse_version(expected_version):
        raise UpdateError("A versão do pacote não corresponde à atualização solicitada.")
    return ReleaseInfo(
        version=version,
        version_text=str(version),
        tag_name=f"v{version}",
        notes="",
        html_url="",
        package=ReleaseAsset(archive.name, ""),
        checksum=ReleaseAsset("", ""),
        published_at=utc_now(),
    )


# Install archive.
def install_archive(
    archive_path: Path,
    checksum_path: Path,
    install_root: Path,
    config: UpdateConfig,
    *,
    expected_version: str | None = None,
    state_path: Path | None = None,
) -> UpdateTransaction:
    """Install a validated archive while preserving user data and rollback state."""
    archive_path, checksum_path, root = Path(archive_path).resolve(), Path(checksum_path).resolve(), Path(install_root).resolve()
    if not archive_path.is_file() or not checksum_path.is_file():
        raise UpdateError("O pacote ou o checksum não está disponível.")
    if not root.is_dir():
        raise UpdateError("A pasta de instalação não existe.")
    try:
        check_install_prerequisites(archive_path, root)
    except UpdateError:
        raise
    except OSError as exc:
        raise UpdateError("Não foi possível verificar os recursos da instalação.") from exc

    service = UpdateService(config, str(expected_version or "0.0.0"))
    service._verify_checksum(archive_path, checksum_path)
    release = _release_from_archive(archive_path, config, expected_version)
    staging_parent = Path(tempfile.mkdtemp(prefix="save-manager-update-", dir=root))
    try:
        manifest = service._extract_and_validate(archive_path, staging_parent, release)
        files = tuple(str(item).replace("\\", "/") for item in manifest["files"])
        state_path = Path(state_path) if state_path else root / STATE_FILENAME
        try:
            if not state_path.resolve().is_relative_to(root):
                raise UpdateError("O estado da atualização precisa ficar na pasta de instalação.")
        except OSError as exc:
            raise UpdateError("Não foi possível validar o estado da atualização.") from exc
        previous_state = read_state(state_path)
        baseline_version = str(previous_state.get("version", "0.0.0")) if previous_state else "0.0.0"
        ensure_initial_state(
            root,
            config.package_id,
            baseline_version,
            state_path=state_path,
        )
        previous_state = read_state(state_path)
        if previous_state and previous_state.get("status") in ACTIVE_UPDATE_STATUSES:
            raise UpdateError("Já existe uma atualização em andamento.")
        managed = _managed_files(previous_state)
        if not managed and any(_safe_target(root, relative).exists() for relative in files):
            raise UpdateError("A instalação não possui um manifesto anterior; atualização interrompida para preservar arquivos desconhecidos.")
        for relative in files:
            if UpdateService._is_user_path(relative):
                raise UpdateError("O pacote tenta atualizar dados do usuário.")
            target = _safe_target(root, relative)
            if target.exists():
                if relative not in managed:
                    raise UpdateError(f"Conflito com arquivo não gerenciado: {relative}")
                if _sha256(target) != managed[relative]:
                    raise UpdateError(f"O arquivo do programa foi alterado localmente: {relative}")

        transaction_id = uuid.uuid4().hex
        backup_dir = root / BACKUP_DIRECTORY / transaction_id
        backup_dir.mkdir(parents=True, exist_ok=False)
        backed_up: list[str] = []
        try:
            for relative in files:
                target = _safe_target(root, relative)
                if target.exists():
                    backup = backup_dir / Path(relative)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                    backed_up.append(relative)
            # Remove old managed files only when the previous state identifies
            # them and they are absent from the new manifest.
            for relative in set(managed) - set(files):
                target = _safe_target(root, relative)
                if target.exists():
                    backup = backup_dir / Path(relative)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                    backed_up.append(relative)
                    target.unlink()
            atomic_json_write(
                state_path,
                {
                    "status": "installing",
                    "version": str(release.version),
                    "package_id": config.package_id,
                    "install_root": str(root),
                    "backup_dir": str(backup_dir),
                    "previous_state": previous_state,
                    "backed_up_files": backed_up,
                    "installed_files": list(files),
                    "created_at": utc_now(),
                },
            )
            for relative in files:
                target = _safe_target(root, relative)
                source = staging_parent / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
            new_managed = {relative: _sha256(_safe_target(root, relative)) for relative in files}
            state = {
                "status": "awaiting_confirmation",
                "version": str(release.version),
                "package_id": config.package_id,
                "install_root": str(root),
                "backup_dir": str(backup_dir),
                "managed_files": new_managed,
                "previous_state": previous_state,
                "backed_up_files": backed_up,
                "created_at": utc_now(),
            }
            atomic_json_write(state_path, state)
            return UpdateTransaction(state_path, backup_dir, root, files, previous_state)
        except Exception:
            _rollback_files(root, backup_dir, backed_up, files, state_path, previous_state)
            raise
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)


# Remove an empty backup container.
def _remove_empty_backup_parent(backup_dir: Path) -> None:
    parent = Path(backup_dir).resolve().parent
    if parent.name != BACKUP_DIRECTORY:
        return
    try:
        parent.rmdir()
    except OSError:
        pass


# Rollback files.
def _rollback_files(root: Path, backup_dir: Path, backed_up: list[str], installed: tuple[str, ...], state_path: Path, previous_state: dict[str, object] | None) -> None:
    for relative in installed:
        target = _safe_target(root, relative)
        if relative not in backed_up and target.exists():
            target.unlink()
    for relative in backed_up:
        backup = backup_dir / Path(relative)
        if backup.exists():
            target = _safe_target(root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, target)
    if previous_state is None:
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass
    else:
        atomic_json_write(state_path, previous_state)
    shutil.rmtree(backup_dir, ignore_errors=True)
    _remove_empty_backup_parent(backup_dir)


# Build rollback state.
def _rolled_back_state(
    root: Path,
    state: dict[str, object] | None,
    previous_state: dict[str, object] | None,
) -> dict[str, object]:
    repaired = dict(previous_state or {})
    current = state or {}
    repaired.update(
        {
            "status": "rolled_back",
            "version": str(current.get("version") or repaired.get("version") or "0.0.0"),
            "package_id": str(current.get("package_id") or repaired.get("package_id") or ""),
            "install_root": str(root),
            "rolled_back_at": utc_now(),
        }
    )
    package_id = str(current.get("package_id") or repaired.get("package_id") or "")
    if _state_metadata_is_valid(previous_state, root, package_id):
        managed = _managed_files(previous_state)
    else:
        managed = _baseline_managed_files(root)
    if managed:
        repaired["managed_files"] = managed
    else:
        repaired.pop("managed_files", None)
    for key in ("backup_dir", "previous_state", "backed_up_files", "installed_files"):
        repaired.pop(key, None)
    return repaired


# Finalize transaction.
def finalize_transaction(transaction: UpdateTransaction) -> None:
    state = read_state(transaction.state_path)
    if state:
        state["status"] = "confirmed"
        state.pop("backup_dir", None)
        state.pop("previous_state", None)
        state.pop("backed_up_files", None)
        state["confirmed_at"] = utc_now()
        atomic_json_write(transaction.state_path, state)
    shutil.rmtree(transaction.backup_dir, ignore_errors=True)
    _remove_empty_backup_parent(transaction.backup_dir)


# Rollback transaction.
def rollback_transaction(transaction: UpdateTransaction) -> None:
    state = read_state(transaction.state_path)
    backed_up = state.get("backed_up_files", []) if state else []
    if not isinstance(backed_up, list):
        backed_up = []
    _rollback_files(transaction.install_root, transaction.backup_dir, [str(item) for item in backed_up], transaction.installed_files, transaction.state_path, transaction.previous_state)
    failed_state = _rolled_back_state(
        transaction.install_root,
        state,
        transaction.previous_state,
    )
    atomic_json_write(transaction.state_path, failed_state)


# Recover pending install.
def recover_pending_install(state_path: Path | None = None) -> bool:
    """Recover an interrupted file replacement before normal app startup."""
    path = Path(state_path or (default_install_root() / STATE_FILENAME))
    state = read_state(path)
    if not state or state.get("status") != "installing":
        return False
    try:
        root = Path(str(state["install_root"])).resolve()
        backup_dir = Path(str(state["backup_dir"])).resolve()
        if not backup_dir.is_relative_to(root):
            raise UpdateError("Estado de recuperação aponta para uma pasta externa.")
        if not path.resolve().is_relative_to(root):
            raise UpdateError("Estado de recuperação está fora da instalação.")
        backed_up = state.get("backed_up_files", [])
        installed = state.get("installed_files", [])
        if not isinstance(backed_up, list) or not isinstance(installed, list):
            raise UpdateError("Estado de recuperação inválido.")
        previous = state.get("previous_state")
        previous_state = previous if isinstance(previous, dict) else None
        _rollback_files(root, backup_dir, [str(item) for item in backed_up], tuple(str(item) for item in installed), path, previous_state)
        atomic_json_write(path, _rolled_back_state(root, state, previous_state))
        return True
    except Exception:
        LOGGER.exception("Não foi possível recuperar uma atualização interrompida")
        return False


# Handle confirm pending startup.
def confirm_pending_startup(state_path: Path | None = None) -> bool:
    path = Path(state_path or os.environ.get(CONFIRMATION_ENV, default_install_root() / STATE_FILENAME))
    state = read_state(path)
    if not state or state.get("status") != "awaiting_confirmation":
        return False
    backup_dir = state.get("backup_dir")
    state["status"] = "confirmed"
    state.pop("backup_dir", None)
    state.pop("previous_state", None)
    state.pop("backed_up_files", None)
    state["confirmed_at"] = utc_now()
    atomic_json_write(path, state)
    if backup_dir:
        try:
            candidate = Path(str(backup_dir)).resolve()
            root = Path(str(state.get("install_root", path.parent))).resolve()
            if candidate.is_relative_to(root / BACKUP_DIRECTORY):
                shutil.rmtree(candidate, ignore_errors=True)
                _remove_empty_backup_parent(candidate)
        except (OSError, ValueError):
            LOGGER.warning("Não foi possível limpar o backup transitório da atualização.")
    return True
