from save_manager.services.windows_startup import STARTUP_KEY, STARTUP_VALUE, WindowsStartupService


class FakeKey:
    # Track key closure.
    def __init__(self):
        self.closed = False

    # Close the fake key.
    def Close(self):
        self.closed = True


class FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 2
    REG_SZ = 1

    # Initialize the fake registry.
    def __init__(self):
        self.created = []
        self.values = {}
        self.deleted = []
        self.key = FakeKey()

    # Create a registry key.
    def CreateKeyEx(self, root, path, reserved, access):
        self.created.append((root, path, reserved, access))
        return self.key

    # Store a registry value.
    def SetValueEx(self, key, name, reserved, value_type, value):
        self.values[name] = (key, reserved, value_type, value)

    # Delete a registry value.
    def DeleteValue(self, key, name):
        self.deleted.append((key, name))
        self.values.pop(name, None)


# Verify registry startup registration.
def test_windows_startup_service_registers_and_removes_entry():
    registry = FakeRegistry()
    service = WindowsStartupService(registry, platform_name="win32")

    assert service.supported
    assert service.set_enabled(True)
    assert registry.created[0][1] == STARTUP_KEY
    assert registry.values[STARTUP_VALUE][2] == registry.REG_SZ
    assert "__main__.py" in registry.values[STARTUP_VALUE][3]
    assert service.set_enabled(False)
    assert registry.deleted == [(registry.key, STARTUP_VALUE)]
    assert registry.key.closed
