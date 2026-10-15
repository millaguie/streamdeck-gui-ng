"""Tests the application facing plugin service: what gets placed, and what a key shows."""

import json
from types import SimpleNamespace

import streamdeck_ui.elgato_plugin.service as service_module
from streamdeck_ui.elgato_plugin.service import Binding, ElgatoPluginService
from tests.common import write_executable_plugin


class FakeRuntime:
    def __init__(self, plugins=None):
        self.plugins = plugins if plugins is not None else {"com.test.plugin": SimpleNamespace(uuid="com.test.plugin")}
        self.plugin_settings = {}
        self.calls = []
        self.placements = {}
        self._next = 0

    def load_plugins_from(self, *directories):
        self.calls.append(("load", directories))
        return list(self.plugins.values())

    def start(self):
        self.calls.append(("start",))
        return 51000

    def stop(self):
        self.calls.append(("stop",))

    def device_connected(self, serial, device_info):
        self.calls.append(("connected", serial, device_info))

    def device_disconnected(self, serial):
        self.calls.append(("disconnected", serial))

    def place(self, plugin_uuid, action_uuid, serial, key, settings=None):
        self._next += 1
        context = f"context-{self._next}"
        self.calls.append(("place", context, plugin_uuid, action_uuid, serial, key, settings))
        self.placements[context] = (serial, key)
        return SimpleNamespace(context=context, plugin_uuid=plugin_uuid, action_uuid=action_uuid)

    def unplace(self, context):
        self.calls.append(("unplace", context))
        self.placements.pop(context, None)

    def press(self, context):
        self.calls.append(("press", context))

    def release(self, context):
        self.calls.append(("release", context))

    def rotate(self, context, ticks):
        self.calls.append(("rotate", context, ticks))

    def contexts(self, kind):
        return [call for call in self.calls if call[0] == kind]


def make_service(tmp_path, runtime=None, repaint=None, plugins=None):
    installed = (
        plugins if plugins is not None else {"com.test.plugin": SimpleNamespace(uuid="com.test.plugin", actions=[])}
    )
    runtime = runtime or FakeRuntime(plugins=installed)
    service = ElgatoPluginService(
        repaint=repaint,
        url_opener=lambda url: None,
        plugin_directories=[str(tmp_path / "plugins")],
        settings_file=str(tmp_path / "settings.json"),
        runtime=runtime,
    )
    service.plugins = dict(installed)
    return service


def bindings(*buttons):
    return [
        Binding(button=button, plugin_uuid="com.test.plugin", action_uuid=f"action.{button}", settings={"b": button})
        for button in buttons
    ]


def placed_buttons(runtime, serial="SERIAL"):
    """The keys of this deck that a plugin is running on right now."""
    return {key: context for context, (device, key) in runtime.placements.items() if device == serial}


def test_only_bound_buttons_of_the_visible_page_are_placed(tmp_path):
    runtime = FakeRuntime()
    service = make_service(tmp_path, runtime)
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(0, 1))

    assert set(placed_buttons(runtime)) == {0, 1}


def test_moving_to_another_page_replaces_the_running_instances(tmp_path):
    runtime = FakeRuntime()
    service = make_service(tmp_path, runtime)
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(0, 1))
    first = dict(placed_buttons(runtime))
    service.sync_page("SERIAL", 1, bindings(2))

    assert [call[1] for call in runtime.contexts("unplace")] == sorted(first.values())
    assert placed_buttons(runtime) == {2: "context-3"}


def test_syncing_the_same_page_twice_changes_nothing(tmp_path):
    runtime = FakeRuntime()
    service = make_service(tmp_path, runtime)
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(0))
    service.sync_page("SERIAL", 0, bindings(0))

    assert len(runtime.contexts("place")) == 1
    assert not runtime.contexts("unplace")


def test_rebinding_a_button_restarts_it(tmp_path):
    runtime = FakeRuntime()
    service = make_service(tmp_path, runtime)
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(0))
    service.sync_page("SERIAL", 0, [Binding(button=0, plugin_uuid="com.test.plugin", action_uuid="other")])

    assert len(runtime.contexts("unplace")) == 1
    assert len(runtime.contexts("place")) == 2


def test_buttons_of_plugins_that_are_not_installed_are_skipped(tmp_path):
    runtime = FakeRuntime(plugins={})
    service = make_service(tmp_path, runtime=runtime, plugins={})
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(0))

    assert not runtime.contexts("place")


def test_press_and_release_only_reach_bound_keys(tmp_path):
    runtime = FakeRuntime()
    service = make_service(tmp_path, runtime)
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(3))
    context = placed_buttons(runtime)[3]

    service.key_pressed("SERIAL", 3, True)
    service.key_pressed("SERIAL", 3, False)
    service.key_pressed("SERIAL", 4, True)

    assert [call[1] for call in runtime.contexts("press")] == [context]
    assert [call[1] for call in runtime.contexts("release")] == [context]


def test_closing_a_deck_stops_its_instances(tmp_path):
    runtime = FakeRuntime()
    service = make_service(tmp_path, runtime)
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(0))
    service.deck_closed("SERIAL")

    assert len(runtime.contexts("unplace")) == 1
    assert ("disconnected", "SERIAL") in runtime.calls
    assert service.overlay_for("SERIAL", 0, 0) is None


def test_a_plugin_image_becomes_a_file_the_display_pipeline_can_draw(tmp_path):
    repainted = []
    service = make_service(tmp_path, repaint=lambda serial, page, button: repainted.append((serial, page, button)))
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(1))

    service.sink.set_key_image("SERIAL", 1, b"\x89PNG fake")

    overlay = service.overlay_for("SERIAL", 0, 1)
    assert overlay is not None
    assert open(overlay.image, "rb").read() == b"\x89PNG fake"
    assert repainted == [("SERIAL", 0, 1)]


def test_a_plugin_can_hand_a_key_back_to_the_button(tmp_path):
    service = make_service(tmp_path)
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(1))
    service.sink.set_key_image("SERIAL", 1, b"\x89PNG fake")

    service.sink.set_key_image("SERIAL", 1, None)

    assert service.overlay_for("SERIAL", 0, 1).image is None


def test_overlays_only_apply_to_the_page_being_shown(tmp_path):
    service = make_service(tmp_path)
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(1))
    service.sink.set_key_image("SERIAL", 1, b"\x89PNG fake")

    assert service.overlay_for("SERIAL", 0, 1) is not None
    assert service.overlay_for("SERIAL", 1, 1) is None


def test_a_plugin_title_replaces_the_button_text(tmp_path):
    service = make_service(tmp_path)
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(1))

    service.sink.set_key_title("SERIAL", 1, "ON AIR")

    assert service.overlay_for("SERIAL", 0, 1).title == "ON AIR"


def test_global_settings_survive_a_restart(tmp_path):
    settings_file = tmp_path / "settings.json"
    runtime = FakeRuntime()
    service = make_service(tmp_path, runtime)
    service.plugin_settings["com.test.plugin"] = {"volume": 11}
    service.save_settings()

    reloaded = ElgatoPluginService(
        plugin_directories=[str(tmp_path / "plugins")],
        settings_file=str(settings_file),
        runtime=FakeRuntime(),
    )

    assert reloaded.plugin_settings == {"com.test.plugin": {"volume": 11}}
    assert json.loads(settings_file.read_text())["global_settings"]["com.test.plugin"]["volume"] == 11


def test_a_plugin_setting_its_own_key_reaches_the_button_owner(tmp_path):
    seen = []
    runtime = FakeRuntime()
    service = make_service(tmp_path, runtime)
    service.on_settings = lambda serial, page, button, settings: seen.append((serial, page, button, settings))
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(1))

    service.plugin_settings_changed(placed_buttons(runtime)[1], {"volume": 11})

    assert seen == [("SERIAL", 0, 1, {"volume": 11})]


def test_settings_for_an_instance_that_is_gone_are_ignored(tmp_path):
    seen = []
    runtime = FakeRuntime()
    service = make_service(tmp_path, runtime)
    service.on_settings = lambda *arguments: seen.append(arguments)
    service.deck_opened("SERIAL", {"name": "Stream Deck"})
    service.sync_page("SERIAL", 0, bindings(1))
    service.sync_page("SERIAL", 1, bindings(1))

    service.plugin_settings_changed("context-1", {"volume": 11})

    assert seen == []


def test_a_broken_settings_file_does_not_stop_the_app(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{not json")

    service = ElgatoPluginService(
        plugin_directories=[str(tmp_path / "plugins")],
        settings_file=str(settings_file),
        runtime=FakeRuntime(),
    )

    assert service.plugin_settings == {}


def test_the_button_list_offers_every_installed_action(tmp_path):
    manifest = SimpleNamespace(
        uuid="com.test.plugin",
        actions=[SimpleNamespace(uuid="b", name="Second"), SimpleNamespace(uuid="a", name="First")],
    )
    service = make_service(tmp_path, plugins={"com.test.plugin": manifest})

    assert service.button_options() == [("com.test.plugin", "a", "First"), ("com.test.plugin", "b", "Second")]


def test_bundles_other_launchers_installed_are_found_too(tmp_path):
    ours = tmp_path / "ours"
    shared = tmp_path / "shared"
    write_executable_plugin(ours / "com.ours.sdPlugin", "", manifest={"UUID": "com.ours.plugin"})
    write_executable_plugin(shared / "com.theirs.sdPlugin", "", manifest={"UUID": "com.theirs.plugin"})

    service = ElgatoPluginService(
        plugin_directories=[str(ours), str(shared)],
        settings_file=str(tmp_path / "settings.json"),
        runtime=FakeRuntime(plugins={}),
    )

    assert set(service.plugins) == {"com.ours.plugin", "com.theirs.plugin"}


def test_our_copy_of_a_bundle_wins_over_the_one_another_launcher_installed(tmp_path):
    ours = tmp_path / "ours"
    shared = tmp_path / "shared"
    write_executable_plugin(ours / "duplicate.sdPlugin", "", manifest={"UUID": "com.dupe", "Name": "Ours"})
    write_executable_plugin(shared / "duplicate.sdPlugin", "", manifest={"UUID": "com.dupe", "Name": "Theirs"})

    service = ElgatoPluginService(
        plugin_directories=[str(ours), str(shared)],
        settings_file=str(tmp_path / "settings.json"),
        runtime=FakeRuntime(plugins={}),
    )

    assert service.plugins["com.dupe"].name == "Ours"


def test_the_default_scan_starts_with_our_own_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "DEFAULT_PLUGIN_DIRECTORY", str(tmp_path / "ours"))
    monkeypatch.setattr(service_module, "SHARED_PLUGIN_DIRECTORY", str(tmp_path / "shared"))

    service = ElgatoPluginService(settings_file=str(tmp_path / "settings.json"), runtime=FakeRuntime(plugins={}))

    assert [directory.name for directory in service.plugin_directories] == ["ours", "shared"]
