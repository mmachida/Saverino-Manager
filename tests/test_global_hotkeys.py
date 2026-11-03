from save_manager.ui.global_hotkeys import (
    DISPATCH_DEBOUNCE_SECONDS,
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    GlobalHotkeyManager,
)


def test_global_hotkey_parser_converts_common_sequences():
    assert GlobalHotkeyManager.parse_sequence("Ctrl+Alt+S") == (MOD_CONTROL | MOD_ALT, ord("S"))
    assert GlobalHotkeyManager.parse_sequence("Ctrl+Shift+F5") == (MOD_CONTROL | MOD_SHIFT, 0x74)
    assert GlobalHotkeyManager.parse_sequence("") is None
    assert GlobalHotkeyManager.parse_sequence("Ctrl+O, Ctrl+P") is None


def test_global_hotkey_manager_polls_without_consuming_bindings(qtbot, monkeypatch):
    events = []
    clock = [100.0]
    monkeypatch.setattr("save_manager.ui.global_hotkeys.time.monotonic", lambda: clock[0])

    def record(action):
        events.append(action)
        clock[0] += DISPATCH_DEBOUNCE_SECONDS + 0.05

    manager = GlobalHotkeyManager(record)

    class FakeUser32:
        def __init__(self):
            self.down = set()

        def GetAsyncKeyState(self, virtual_key):
            return -0x8000 if virtual_key in self.down else 0

    fake = FakeUser32()
    manager._user32 = fake
    try:
        failures = manager.apply({
            "global_hotkeys_enabled": True,
            "create_save_hotkey": "Ctrl+Alt+S",
            "load_save_hotkey": "Ctrl+Shift+L",
        })
        assert failures == []
        assert manager._bindings == [
            ("create_save", MOD_CONTROL | MOD_ALT, ord("S")),
            ("load_save", MOD_CONTROL | MOD_SHIFT, ord("L")),
        ]
        fake.down.update({ord("S"), 0xA2, 0xA4})
        manager._poll_hotkeys()
        assert events == ["create_save"]
        manager._poll_hotkeys()
        assert events == ["create_save"]
        fake.down.remove(ord("S"))
        manager._poll_hotkeys()
        clock[0] += DISPATCH_DEBOUNCE_SECONDS + 0.05
        fake.down.add(ord("S"))
        manager._poll_hotkeys()
        assert events == ["create_save", "create_save"]
        assert manager.nativeEventFilter(b"windows_generic_MSG", 0) is False
    finally:
        manager.close()

    assert not manager._poll_timer.isActive()
