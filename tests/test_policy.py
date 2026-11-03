from uuid import uuid4

import pytest

from save_manager.domain.errors import PathPolicyError
from save_manager.domain.models import Profile, SourceType, StorageMode
from save_manager.filesystem.policy import PathPolicy


def test_policy_rejects_repository_collision_and_unc(tmp_path):
    root = tmp_path / "app"
    source = tmp_path / "source"
    source.mkdir()
    policy = PathPolicy(root)
    with pytest.raises(PathPolicyError):
        policy.validate_source("\\\\server\\share\\save", SourceType.FILE)
    with pytest.raises(PathPolicyError):
        policy.validate_source(str(root), SourceType.DIRECTORY)


def test_app_folder_uses_app_root_and_separate_data_root(tmp_path):
    app_root = tmp_path / "installation"
    data_root = tmp_path / "roaming" / "Saverino Manager" / "data"
    policy = PathPolicy(app_root, data_root)
    profile = Profile(
        uuid4(),
        "Profile",
        SourceType.FILE,
        str(tmp_path / "game" / "save.dat"),
        StorageMode.APP_FOLDER,
        "",
    )

    assert policy.resolve_backup_base(profile) == app_root / "Saverino backup"
    assert policy.data_root == data_root
