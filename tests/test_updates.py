import hashlib
import json
import urllib.error
import zipfile
from pathlib import Path

import pytest

from save_manager.updates import helper as update_helper
from save_manager.updates.config import APP_EXECUTABLE_NAME, UPDATER_EXECUTABLE_NAME, UpdateConfig
from save_manager.updates.installer import (
    atomic_json_write,
    confirm_pending_startup,
    ensure_initial_state,
    finalize_transaction,
    install_archive,
    read_state,
    recover_pending_install,
    rollback_transaction,
)
from save_manager.updates.service import ReleaseAsset, UpdateError, UpdateService, parse_version

PACKAGE_ID = "saverino-manager-windows-x64"


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        if size < 0:
            data, self.payload = self.payload, b""
            return data
        data, self.payload = self.payload[:size], self.payload[size:]
        return data


def test_unconfigured_updates_never_request_github():
    called = []
    service = UpdateService(UpdateConfig(owner="", repository=""), "1.0.0", opener=lambda *_args, **_kwargs: called.append(True))
    with pytest.raises(UpdateError, match="Updates are not configured yet"):
        service.check_latest()
    assert called == []


def test_frozen_install_baseline_claims_only_program_executables(tmp_path):
    (tmp_path / APP_EXECUTABLE_NAME).write_bytes(b"app")
    (tmp_path / UPDATER_EXECUTABLE_NAME).write_bytes(b"helper")
    (tmp_path / "user-notes.txt").write_bytes(b"keep")
    ensure_initial_state(tmp_path, PACKAGE_ID, "1.0.0")
    state = read_state(tmp_path / ".save-manager-update-state.json")
    assert set(state["managed_files"]) == {APP_EXECUTABLE_NAME, UPDATER_EXECUTABLE_NAME}
    assert (tmp_path / "user-notes.txt").exists()


def test_invalid_or_rolled_back_state_is_rebuilt(tmp_path):
    (tmp_path / APP_EXECUTABLE_NAME).write_bytes(b"app")
    (tmp_path / UPDATER_EXECUTABLE_NAME).write_bytes(b"helper")
    state_path = tmp_path / ".save-manager-update-state.json"
    atomic_json_write(
        state_path,
        {
            "status": "rolled_back",
            "version": "1.0.1",
            "install_root": str(tmp_path),
        },
    )
    ensure_initial_state(tmp_path, PACKAGE_ID, "1.0.1")
    repaired = read_state(state_path)
    assert repaired["status"] == "confirmed"
    assert set(repaired["managed_files"]) == {APP_EXECUTABLE_NAME, UPDATER_EXECUTABLE_NAME}

    state_path.write_text("{not valid json", encoding="utf-8")
    ensure_initial_state(tmp_path, PACKAGE_ID, "1.0.1")
    repaired = read_state(state_path)
    assert repaired["status"] == "confirmed"
    assert set(repaired["managed_files"]) == {APP_EXECUTABLE_NAME, UPDATER_EXECUTABLE_NAME}


def test_interrupted_install_is_recovered_from_recorded_backup(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    target = root / APP_EXECUTABLE_NAME
    target.write_bytes(b"partial")
    backup = root / ".save-manager-update-backups" / "tx"
    backup.mkdir(parents=True)
    (backup / APP_EXECUTABLE_NAME).write_bytes(b"old")
    state_path = root / ".save-manager-update-state.json"
    atomic_json_write(
        state_path,
        {
            "status": "installing",
            "install_root": str(root),
            "backup_dir": str(backup),
            "backed_up_files": [APP_EXECUTABLE_NAME],
            "installed_files": [APP_EXECUTABLE_NAME],
            "previous_state": None,
        },
    )
    assert recover_pending_install(state_path)
    assert target.read_bytes() == b"old"
    assert read_state(state_path)["status"] == "rolled_back"


def test_pending_confirmation_is_finalized_and_cleans_backup(tmp_path):
    state_path = tmp_path / ".save-manager-update-state.json"
    backup = tmp_path / ".save-manager-update-backups" / "tx"
    backup.mkdir(parents=True)
    (backup / "old.exe").write_bytes(b"old")
    atomic_json_write(
        state_path,
        {
            "status": "awaiting_confirmation",
            "install_root": str(tmp_path),
            "backup_dir": str(backup),
            "managed_files": {APP_EXECUTABLE_NAME: "0" * 64},
        },
    )
    assert confirm_pending_startup(state_path)
    state = read_state(state_path)
    assert state["status"] == "confirmed"
    assert not backup.exists()
    assert not (tmp_path / ".save-manager-update-backups").exists()


def test_release_uses_numeric_version_and_exact_assets():
    config = UpdateConfig(owner="owner", repository="repo", package_id=PACKAGE_ID)
    payload = {
        "tag_name": "v1.10.0",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-09-14T00:00:00Z",
        "body": "Notas",
        "html_url": "https://github.com/owner/repo/releases/tag/v1.10.0",
        "assets": [
            {"name": config.package_name, "browser_download_url": "https://example/app.zip", "size": 10},
            {"name": config.checksum_name, "browser_download_url": "https://example/app.sha256", "size": 70},
            {"name": "Source code.zip", "browser_download_url": "https://example/source.zip", "size": 10},
        ],
    }
    service = UpdateService(config, "1.9.0", opener=lambda *_args, **_kwargs: FakeResponse(json.dumps(payload).encode()))
    release = service.check_latest()
    assert release is not None
    assert release.version == parse_version("1.10.0")
    assert release.package.name == config.package_name
    assert service._parse_release({**payload, "tag_name": "v1.9.0"}) is None


def test_network_failure_is_reported_without_a_success_value():
    config = UpdateConfig(owner="owner", repository="repo", package_id=PACKAGE_ID)

    def opener(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    service = UpdateService(config, "1.0.0", opener=opener)
    with pytest.raises(UpdateError, match="consultar atualizações"):
        service.check_latest()


def test_helper_rolls_back_if_the_new_process_cannot_start(tmp_path, monkeypatch):
    config = UpdateConfig(package_id=PACKAGE_ID)
    archive = tmp_path / config.package_name
    _make_archive(archive, package_id=config.package_id)
    checksum = tmp_path / config.checksum_name
    checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
    install_root = tmp_path / "install"
    install_root.mkdir()

    def fail_start(*_args, **_kwargs):
        raise OSError("process unavailable")

    monkeypatch.setattr(update_helper.subprocess, "Popen", fail_start)
    result = update_helper.run_update(
        archive,
        checksum,
        install_root,
        tmp_path / APP_EXECUTABLE_NAME,
        0,
        "1.1.0",
        config.package_id,
    )
    assert result == 4
    assert not (install_root / APP_EXECUTABLE_NAME).exists()
    assert read_state(install_root / ".save-manager-update-state.json")["status"] == "rolled_back"


def _make_archive_files(path: Path, files: dict[str, bytes], *, version="1.1.0", package_id=PACKAGE_ID):
    manifest = {"format": 1, "version": version, "package_id": package_id, "files": list(files)}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("update-manifest.json", json.dumps(manifest))
        for relative, data in files.items():
            archive.writestr(relative, data)


def _make_archive(path: Path, *, version="1.1.0", package_id=PACKAGE_ID, relative=APP_EXECUTABLE_NAME, data=b"new"):
    _make_archive_files(path, {relative: data}, version=version, package_id=package_id)


def test_update_repairs_missing_state_before_replacing_files(tmp_path):
    config = UpdateConfig(package_id=PACKAGE_ID)
    archive = tmp_path / config.package_name
    _make_archive_files(
        archive,
        {
            APP_EXECUTABLE_NAME: b"new app",
            UPDATER_EXECUTABLE_NAME: b"new helper",
        },
        package_id=config.package_id,
    )
    checksum = tmp_path / config.checksum_name
    checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
    install_root = tmp_path / "install"
    install_root.mkdir()
    (install_root / APP_EXECUTABLE_NAME).write_bytes(b"old app")
    (install_root / UPDATER_EXECUTABLE_NAME).write_bytes(b"old helper")
    state_path = install_root / ".save-manager-update-state.json"
    state_path.write_text("{bad state", encoding="utf-8")

    transaction = install_archive(
        archive,
        checksum,
        install_root,
        config,
        expected_version="1.1.0",
        state_path=state_path,
    )
    assert (install_root / APP_EXECUTABLE_NAME).read_bytes() == b"new app"
    assert (install_root / UPDATER_EXECUTABLE_NAME).read_bytes() == b"new helper"
    assert read_state(state_path)["status"] == "awaiting_confirmation"
    assert set(read_state(state_path)["managed_files"]) == {APP_EXECUTABLE_NAME, UPDATER_EXECUTABLE_NAME}
    rollback_transaction(transaction)
    assert (install_root / APP_EXECUTABLE_NAME).read_bytes() == b"old app"
    assert (install_root / UPDATER_EXECUTABLE_NAME).read_bytes() == b"old helper"
    failed = read_state(state_path)
    assert failed["status"] == "rolled_back"
    assert set(failed["managed_files"]) == {APP_EXECUTABLE_NAME, UPDATER_EXECUTABLE_NAME}


def test_download_checksum_and_safe_install_with_rollback(tmp_path):
    config = UpdateConfig(package_id=PACKAGE_ID)
    archive = tmp_path / config.package_name
    _make_archive(archive, package_id=config.package_id)
    checksum = tmp_path / config.checksum_name
    checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
    install_root = tmp_path / "install"
    install_root.mkdir()
    (install_root / "data").mkdir()
    (install_root / "data" / "catalog.sqlite3").write_bytes(b"catalog")
    (install_root / "backups").mkdir()
    (install_root / "backups" / "keep.sav").write_bytes(b"user backup")
    transaction = install_archive(archive, checksum, install_root, config, expected_version="1.1.0")
    assert (install_root / APP_EXECUTABLE_NAME).read_bytes() == b"new"
    assert (install_root / "data" / "catalog.sqlite3").read_bytes() == b"catalog"
    assert (install_root / "backups" / "keep.sav").read_bytes() == b"user backup"
    assert read_state(transaction.state_path)["status"] == "awaiting_confirmation"
    assert confirm_pending_startup(transaction.state_path)
    finalize_transaction(transaction)
    assert read_state(transaction.state_path)["status"] == "confirmed"
    assert not (install_root / ".save-manager-update-backups").exists()

    rollback_transaction(transaction)
    assert (install_root / APP_EXECUTABLE_NAME).exists() is False
    assert read_state(transaction.state_path)["status"] == "rolled_back"


def test_archive_traversal_and_checksum_mismatch_are_blocked(tmp_path):
    config = UpdateConfig(package_id=PACKAGE_ID)
    archive = tmp_path / config.package_name
    _make_archive(archive, package_id=config.package_id, relative="../outside.exe")
    checksum = tmp_path / config.checksum_name
    checksum.write_text("0" * 64, encoding="utf-8")
    install_root = tmp_path / "install"
    install_root.mkdir()
    with pytest.raises(UpdateError, match="checksum"):
        install_archive(archive, checksum, install_root, config, expected_version="1.1.0")
    checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
    with pytest.raises(UpdateError, match="caminho"):
        install_archive(archive, checksum, install_root, config, expected_version="1.1.0")


def test_download_and_prepare_reports_progress_and_rejects_source_zip(tmp_path):
    config = UpdateConfig(owner="owner", repository="repo", package_id=PACKAGE_ID)
    archive = tmp_path / config.package_name
    _make_archive(archive, package_id=config.package_id)
    archive_bytes = archive.read_bytes()
    checksum_bytes = f"{hashlib.sha256(archive_bytes).hexdigest()}  {archive.name}\n".encode()
    release = ReleaseAsset(config.package_name, "https://example/app.zip", len(archive_bytes))
    checksum = ReleaseAsset(config.checksum_name, "https://example/app.sha256", len(checksum_bytes))
    from save_manager.updates.service import ReleaseInfo

    info = ReleaseInfo(parse_version("1.1.0"), "1.1.0", "v1.1.0", "", "", release, checksum, "2026-09-14")
    responses = {release.url: archive_bytes, checksum.url: checksum_bytes}
    progress = []

    def opener(request, **_kwargs):
        return FakeResponse(responses[request.full_url])

    service = UpdateService(config, "1.0.0", opener=opener)
    prepared = service.download_and_prepare(info, tmp_path / "download", progress=lambda done, total: progress.append((done, total)))
    assert prepared.manifest["version"] == "1.1.0"
    assert progress[-1][0] == len(archive_bytes)
