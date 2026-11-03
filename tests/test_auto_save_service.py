from types import SimpleNamespace
from uuid import uuid4

import pytest

from save_manager.domain.errors import DomainError
from save_manager.domain.models import SourceType
from save_manager.services.auto_save import AutoSaveService, MIN_INTERVAL_SECONDS


class FakeCatalog:
    # Initialize fake catalog.
    def __init__(self, schedule, profile):
        self.schedule = schedule
        self.profile = profile

    # Read fake setting.
    def get_setting(self, key, default=None):
        return self.schedule if key == "auto_saves" else default

    # Return fake profile.
    def get_profile(self, _profile_id):
        return self.profile


# Check legacy folder conversion.
def test_legacy_folder_schedule_is_labeled_folder_and_uses_minimum_interval():
    profile_id = uuid4()
    schedule = [{
        "id": str(uuid4()),
        "project_id": str(uuid4()),
        "profile_id": str(profile_id),
        "target_type": "file",
        "snapshot_id": str(uuid4()),
        "target_name": "Save folder",
        "interval_seconds": 2,
        "enabled": True,
    }]
    profile = SimpleNamespace(source_type=SourceType.DIRECTORY)

    entry = AutoSaveService(FakeCatalog(schedule, profile)).list_all()[0]

    assert entry["target_type"] == "folder"
    assert entry["interval_seconds"] == MIN_INTERVAL_SECONDS


# Reject intervals below minimum.
def test_new_intervals_below_minimum_are_rejected():
    with pytest.raises(DomainError, match="mínimo é de 15 segundos"):
        AutoSaveService._interval(14)
