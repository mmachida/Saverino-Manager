from save_manager.ui.audio import AudioManager


class FakeEffect:
    def __init__(self):
        self.volumes = []
        self.play_count = 0

    def setVolume(self, value):
        self.volumes.append(value)

    def play(self):
        self.play_count += 1


def test_audio_resolves_bundled_asset_and_respects_preferences():
    keyboard = AudioManager.resolve_path("sfx_keyboard.wav")
    mouse = AudioManager.resolve_path("sfx_mouse.wav")
    assert keyboard is not None and keyboard.is_file()
    assert mouse is not None and mouse.is_file()

    manager = AudioManager()
    imported, loaded = FakeEffect(), FakeEffect()
    manager._effects = {"import": imported, "load": loaded}
    manager.configure(
        {
            "sound_effect_import_enabled": False,
            "sound_effect_load_enabled": True,
            "sound_effect_volume": 75,
        }
    )

    manager.play("import")
    manager.play("load")
    assert imported.play_count == 0
    assert loaded.play_count == 1
    assert loaded.volumes[-1] == 0.75

    manager.configure({"sound_effect_import_enabled": True, "sound_effect_load_enabled": True, "sound_effect_volume": 0})
    manager.play("import")
    manager.play("load")
    assert imported.play_count == 0
    assert loaded.play_count == 1
