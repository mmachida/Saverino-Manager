from types import MethodType, SimpleNamespace

from save_manager.ui.main_window import MainWindow


class FakeTimer:
    # Initialize fake timer.
    def __init__(self):
        self.active = False

    # Start fake timer.
    def start(self):
        self.active = True

    # Stop fake timer.
    def stop(self):
        self.active = False


class FakeSchedules:
    # Initialize fake schedules.
    def __init__(self, entries):
        self.entries = entries

    # Return enabled schedules.
    def list_enabled(self):
        return [entry for entry in self.entries if entry["enabled"]]


# Create scheduler harness.
def make_scheduler(entries, *, global_enabled=True):
    window = SimpleNamespace(
        _auto_save_global_enabled=global_enabled,
        _auto_save_deadlines={"stale": 0.0},
        auto_saves=FakeSchedules(entries),
        auto_save_timer=FakeTimer(),
        operation_active=False,
    )
    window._sync_auto_save_timer = MethodType(MainWindow._sync_auto_save_timer, window)
    return window


# Check global shutdown.
def test_global_off_stops_scheduler_and_clears_deadlines():
    window = make_scheduler([], global_enabled=False)

    MainWindow._reset_auto_save_schedule(window)
    MainWindow._poll_auto_saves(window)

    assert window.auto_save_timer.active is False
    assert window._auto_save_deadlines == {}


# Check only enabled deadlines.
def test_only_enabled_schedules_receive_deadlines():
    window = make_scheduler([
        {"id": "enabled", "interval_seconds": 15, "enabled": True},
        {"id": "disabled", "interval_seconds": 15, "enabled": False},
    ])

    MainWindow._reset_auto_save_schedule(window)

    assert set(window._auto_save_deadlines) == {"enabled"}
    assert window.auto_save_timer.active is True


# Check all-disabled shutdown.
def test_no_enabled_schedules_stops_scheduler():
    window = make_scheduler([
        {"id": "disabled", "interval_seconds": 15, "enabled": False},
    ])

    MainWindow._reset_auto_save_schedule(window)

    assert window.auto_save_timer.active is False
    assert window._auto_save_deadlines == {}
