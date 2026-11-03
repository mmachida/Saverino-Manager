"""Cliente GitHub e validação local dos pacotes de atualização."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import stat
import threading
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from packaging.version import InvalidVersion, Version

from .config import UpdateConfig

LOGGER = logging.getLogger("save_manager.updates")
CHUNK_SIZE = 64 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
HEX_SHA256 = re.compile(r"\b([0-9a-fA-F]{64})\b")
ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]


class UpdateError(RuntimeError):
    """Erro seguro para exibição na interface sem traceback técnico."""


class UpdateCancelled(UpdateError):
    """Download cancelado pelo usuário."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int | None = None


@dataclass(frozen=True)
class ReleaseInfo:
    version: Version
    version_text: str
    tag_name: str
    notes: str
    html_url: str
    package: ReleaseAsset
    checksum: ReleaseAsset
    published_at: str


@dataclass(frozen=True)
class PreparedUpdate:
    release: ReleaseInfo
    archive_path: Path
    checksum_path: Path
    staging_dir: Path
    manifest: dict[str, object]


# Parse version.
def parse_version(value: object) -> Version:
    """Parse a stable numeric version while accepting a leading ``v``."""
    text = str(value or "").strip()
    if text[:1].lower() == "v":
        text = text[1:].strip()
    try:
        parsed = Version(text)
    except InvalidVersion as exc:
        raise UpdateError(f"Versão inválida: {value!s}") from exc
    if parsed.is_prerelease or parsed.is_devrelease:
        raise UpdateError("A versão informada não é estável.")
    return parsed


class UpdateService:
    """Consulta releases e prepara um pacote validado em diretório temporário."""

    # Initialize the object.
    def __init__(
        self,
        config: UpdateConfig,
        current_version: str,
        *,
        opener=None,
        timeout: float = 15.0,
    ):
        self.config = config
        self.current_version = parse_version(current_version)
        self._opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self._operation_lock = threading.Lock()

    @property
    # Check configuration state.
    def configured(self) -> bool:
        return self.config.configured

    # Check latest.
    def check_latest(self) -> ReleaseInfo | None:
        """Return a newer stable release, or ``None`` when already current."""
        if not self.configured:
            raise UpdateError("Updates are not configured yet.")
        self._acquire_operation("check")
        try:
            payload = self._get_json(self.config.api_url)
            return self._parse_release(payload)
        finally:
            self._operation_lock.release()

    # Download and prepare.
    def download_and_prepare(
        self,
        release: ReleaseInfo,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> PreparedUpdate:
        """Download, checksum and safely extract a release into ``destination``."""
        self._acquire_operation("download")
        try:
            root = Path(destination).resolve()
            root.mkdir(parents=True, exist_ok=True)
            archive_path = root / release.package.name
            checksum_path = root / release.checksum.name
            self._download(release.package, archive_path, progress=progress, cancel=cancel)
            self._download(release.checksum, checksum_path, progress=None, cancel=cancel)
            self._verify_checksum(archive_path, checksum_path)
            staging_dir = root / "extracted"
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            staging_dir.mkdir()
            manifest = self._extract_and_validate(archive_path, staging_dir, release)
            return PreparedUpdate(release, archive_path, checksum_path, staging_dir, manifest)
        except UpdateCancelled:
            raise
        except UpdateError:
            raise
        except (OSError, zipfile.BadZipFile, ValueError, json.JSONDecodeError) as exc:
            raise UpdateError(f"Não foi possível preparar a atualização: {exc}") from exc
        finally:
            self._operation_lock.release()

    # Acquire operation.
    def _acquire_operation(self, operation: str) -> None:
        if not self._operation_lock.acquire(blocking=False):
            raise UpdateError(f"Já existe uma operação de atualização em andamento ({operation}).")

    # Get json.
    def _get_json(self, url: str) -> dict[str, object]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Save-Manager-Updater/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            response = self._opener(request, timeout=self.timeout)
            with response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise UpdateError(f"GitHub respondeu com HTTP {status}.")
                final_url = str(getattr(response, "geturl", lambda: url)())
                if urlparse(final_url).scheme != "https":
                    raise UpdateError("A consulta de atualizações não permaneceu em HTTPS.")
                raw = response.read(MAX_METADATA_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise UpdateError("O limite temporário da API do GitHub foi atingido. Tente novamente mais tarde.") from exc
            if exc.code == 404:
                raise UpdateError("Nenhum release publicado foi encontrado.") from exc
            raise UpdateError(f"GitHub respondeu com HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UpdateError(f"Não foi possível consultar atualizações: {exc}") from exc
        if len(raw) > MAX_METADATA_BYTES:
            raise UpdateError("A resposta do GitHub é maior que o esperado.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateError("A resposta do GitHub é inválida.") from exc
        if not isinstance(payload, dict):
            raise UpdateError("A resposta do GitHub é inválida.")
        return payload

    # Parse release.
    def _parse_release(self, payload: dict[str, object]) -> ReleaseInfo | None:
        if payload.get("draft") or payload.get("prerelease") or not payload.get("published_at"):
            raise UpdateError("O release mais recente não é uma versão estável publicada.")
        tag_name = str(payload.get("tag_name") or "").strip()
        version = parse_version(tag_name)
        if version <= self.current_version:
            return None
        assets = payload.get("assets")
        if not isinstance(assets, list):
            raise UpdateError("O release não contém uma lista de pacotes.")
        by_name: dict[str, ReleaseAsset] = {}
        for raw_asset in assets:
            if not isinstance(raw_asset, dict):
                continue
            name = str(raw_asset.get("name") or "")
            browser_url = str(raw_asset.get("browser_download_url") or "")
            parsed_url = urlparse(browser_url)
            if (name and browser_url and parsed_url.scheme == "https" and parsed_url.hostname
                    and raw_asset.get("state", "uploaded") == "uploaded"):
                size = raw_asset.get("size")
                by_name[name] = ReleaseAsset(name, browser_url, int(size) if isinstance(size, int) else None)
        package = by_name.get(self.config.package_name)
        checksum = by_name.get(self.config.checksum_name)
        if package is None:
            raise UpdateError(f"Não há pacote compatível ({self.config.package_name}) neste release.")
        if checksum is None:
            raise UpdateError(f"O checksum obrigatório ({self.config.checksum_name}) não foi publicado.")
        return ReleaseInfo(
            version=version,
            version_text=str(version),
            tag_name=tag_name,
            notes=str(payload.get("body") or "Sem notas da versão."),
            html_url=str(payload.get("html_url") or ""),
            package=package,
            checksum=checksum,
            published_at=str(payload["published_at"]),
        )

    # Download.
    def _download(
        self,
        asset: ReleaseAsset,
        destination: Path,
        *,
        progress: ProgressCallback | None,
        cancel: CancelCallback | None,
    ) -> None:
        request = urllib.request.Request(asset.url, headers={"Accept": "application/octet-stream", "User-Agent": "Save-Manager-Updater/1"})
        temporary = destination.with_suffix(destination.suffix + ".part")
        total = asset.size or 0
        written = 0
        try:
            response = self._opener(request, timeout=self.timeout)
            with response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise UpdateError(f"Download respondeu com HTTP {status}.")
                final_url = str(getattr(response, "geturl", lambda: asset.url)())
                if urlparse(final_url).scheme != "https":
                    raise UpdateError("O download da atualização não permaneceu em HTTPS.")
                if not total:
                    try:
                        total = int(response.headers.get("Content-Length", 0))
                    except (AttributeError, TypeError, ValueError):
                        total = 0
                temporary.parent.mkdir(parents=True, exist_ok=True)
                with temporary.open("wb") as stream:
                    if progress:
                        progress(0, total)
                    while True:
                        if cancel and cancel():
                            raise UpdateCancelled("Download cancelado.")
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        stream.write(chunk)
                        written += len(chunk)
                        if progress:
                            progress(written, total)
            if total and written != total:
                raise UpdateError("O download foi interrompido antes de terminar.")
            temporary.replace(destination)
        except urllib.error.HTTPError as exc:
            raise UpdateError(f"Download respondeu com HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UpdateError(f"Não foi possível baixar {asset.name}: {exc}") from exc
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                LOGGER.warning("Não foi possível remover o download parcial %s", temporary)

    @staticmethod
    # Verify checksum.
    def _verify_checksum(archive_path: Path, checksum_path: Path) -> None:
        try:
            checksum_text = checksum_path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            raise UpdateError("Não foi possível ler o checksum do pacote.") from exc
        match = HEX_SHA256.search(checksum_text)
        if not match:
            raise UpdateError("O checksum publicado é inválido.")
        expected = match.group(1).lower()
        digest = hashlib.sha256()
        try:
            with archive_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise UpdateError("Não foi possível calcular o checksum do pacote.") from exc
        if digest.hexdigest() != expected:
            raise UpdateError("O checksum SHA-256 do pacote não confere.")

    # Extract and validate.
    def _extract_and_validate(self, archive_path: Path, staging_dir: Path, release: ReleaseInfo) -> dict[str, object]:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > MAX_ARCHIVE_ENTRIES:
                    raise UpdateError("O pacote de atualização é inválido.")
                if sum(max(0, info.file_size) for info in infos) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise UpdateError("O pacote de atualização é grande demais.")
                safe_infos: dict[str, zipfile.ZipInfo] = {}
                folded_paths: set[str] = set()
                for info in infos:
                    relative = self._safe_archive_path(info.filename)
                    if info.is_dir():
                        raise UpdateError("O pacote contém uma entrada de pasta inesperada.")
                    folded = relative.casefold()
                    if relative in safe_infos or folded in folded_paths:
                        raise UpdateError("O pacote contém caminhos duplicados.")
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == stat.S_IFLNK:
                        raise UpdateError("O pacote contém um link simbólico bloqueado.")
                    safe_infos[relative] = info
                    folded_paths.add(folded)
                if "update-manifest.json" not in safe_infos:
                    raise UpdateError("O manifesto da atualização está ausente.")
                with archive.open(safe_infos["update-manifest.json"]) as manifest_stream:
                    raw_manifest = manifest_stream.read(MAX_METADATA_BYTES + 1)
                if len(raw_manifest) > MAX_METADATA_BYTES:
                    raise UpdateError("O manifesto da atualização é grande demais.")
                try:
                    manifest = json.loads(raw_manifest.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise UpdateError("O manifesto da atualização é inválido.") from exc
                self._validate_manifest(manifest, safe_infos, release)
                for relative, info in safe_infos.items():
                    target = staging_dir / Path(relative)
                    if relative == "update-manifest.json":
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination, CHUNK_SIZE)
                return manifest
        except zipfile.BadZipFile as exc:
            raise UpdateError("O pacote baixado não é um ZIP válido.") from exc

    @staticmethod
    # Validate safe archive path.
    def _safe_archive_path(value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (not path.parts or normalized.startswith("/") or path.is_absolute()
                or "\x00" in normalized or ":" in path.parts[0]):
            raise UpdateError("O pacote contém um caminho absoluto inseguro.")
        if any(part in ("", ".", "..") for part in path.parts):
            raise UpdateError("O pacote contém um caminho inseguro.")
        return "/".join(path.parts)

    # Validate manifest.
    def _validate_manifest(self, manifest: object, infos: dict[str, zipfile.ZipInfo], release: ReleaseInfo) -> None:
        if not isinstance(manifest, dict) or manifest.get("format") != 1:
            raise UpdateError("O manifesto da atualização é incompatível.")
        if parse_version(manifest.get("version")) != release.version:
            raise UpdateError("A versão do manifesto não corresponde ao release.")
        if manifest.get("package_id") != self.config.package_id:
            raise UpdateError("O pacote não é compatível com esta instalação.")
        files = manifest.get("files")
        if not isinstance(files, list) or not files or any(not isinstance(item, str) for item in files):
            raise UpdateError("O manifesto não lista os arquivos do programa.")
        listed: set[str] = set()
        listed_folded: set[str] = set()
        for item in files:
            relative = self._safe_archive_path(item)
            if relative == "update-manifest.json" or relative not in infos:
                raise UpdateError("O manifesto aponta para um arquivo ausente.")
            folded = relative.casefold()
            if relative in listed or folded in listed_folded:
                raise UpdateError("O manifesto contém arquivos duplicados.")
            if self._is_user_path(relative):
                raise UpdateError("O pacote tenta atualizar dados do usuário.")
            listed.add(relative)
            listed_folded.add(folded)
        actual = set(infos) - {"update-manifest.json"}
        if listed != actual:
            raise UpdateError("O manifesto não corresponde ao conteúdo do pacote.")

    @staticmethod
    # Handle is user path.
    def _is_user_path(relative: str) -> bool:
        first = relative.split("/", 1)[0].casefold()
        return first in {"data", "backups", "saverino backup", "saves", "logs"}
