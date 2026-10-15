"""The button form has to say plainly whether an official plugin can run on this machine.

These call the gui helper with stand ins instead of widgets, so they run without pytest-qt
and without a display server.
"""

import json
from types import SimpleNamespace

from streamdeck_ui import gui
from streamdeck_ui.elgato_plugin.manifest import load_plugin_manifest
from tests.common import write_executable_plugin

BUNDLE = "/home/me/.streamdeck_ui/elgato_plugins"


class FakeInfo:
    def __init__(self):
        self.text = ""

    def setText(self, value):
        self.text = value


class FakeApi:
    def __init__(self, binding, plugins):
        self.binding = binding
        self.elgato_plugins = SimpleNamespace(
            plugins=plugins,
            plugin_directories=[BUNDLE],
        )

    def get_button_official_binding(self, serial_number, page, button, state=None):
        return self.binding


def describe(monkeypatch, binding, plugins):
    monkeypatch.setattr(gui, "api", FakeApi(binding, plugins))
    info = FakeInfo()
    gui._refresh_official_action_info(SimpleNamespace(official_widgets={"info": info}), "SERIAL", 0, 0)
    return info.text


def test_an_unbound_button_points_at_the_directories_we_scan(monkeypatch):
    text = describe(monkeypatch, ("", ""), {})

    assert BUNDLE in text


def test_a_binding_to_a_bundle_that_is_not_here_says_so(monkeypatch):
    text = describe(monkeypatch, ("com.not.here", "com.not.here.action"), {})

    assert "is not installed" in text


def test_an_action_the_plugin_does_not_offer_says_so(monkeypatch, tmp_path):
    manifest = write_executable_plugin(
        tmp_path / "com.demo.plugin.sdPlugin", "", manifest={"UUID": "com.demo.plugin", "CodePathLin": "run.sh"}
    )

    text = describe(monkeypatch, ("com.demo.plugin", "com.demo.plugin.gone"), {manifest.uuid: manifest})

    assert "no action called" in text


def test_a_web_plugin_is_reported_as_not_runnable(monkeypatch, tmp_path):
    manifest = write_executable_plugin(
        tmp_path / "com.demo.web.sdPlugin",
        "",
        manifest={"UUID": "com.demo.web", "CodePath": "index.html"},
    )

    text = describe(monkeypatch, ("com.demo.web", manifest.actions[0].uuid), {manifest.uuid: manifest})

    assert "web plugin" in text


def test_a_bundle_without_a_program_for_this_platform_says_it_cannot_start(monkeypatch, tmp_path):
    written = write_executable_plugin(tmp_path / "com.demo.win.sdPlugin", "", manifest={"UUID": "com.demo.win"})
    # The shape some bundles use: a program per platform and no CodePath at all.
    document = json.loads((written.root / "manifest.json").read_text())
    del document["CodePath"]
    document["CodePathWin"] = "thing.exe"
    (written.root / "manifest.json").write_text(json.dumps(document))
    manifest = load_plugin_manifest(written.root)

    text = describe(monkeypatch, ("com.demo.win", manifest.actions[0].uuid), {manifest.uuid: manifest})

    assert "cannot start" in text


def test_a_plugin_that_can_run_says_what_it_will_launch(monkeypatch, tmp_path):
    manifest = write_executable_plugin(
        tmp_path / "com.demo.ok.sdPlugin", "", manifest={"UUID": "com.demo.ok", "CodePathLin": "watcher.sh"}
    )

    text = describe(monkeypatch, ("com.demo.ok", manifest.actions[0].uuid), {manifest.uuid: manifest})

    assert "watcher.sh" in text


def test_the_chosen_action_is_found_in_the_combo_even_when_find_data_is_lost():
    """The real bug: findData() returned -1 for a pair that was in the list."""
    from PySide6.QtWidgets import QApplication, QComboBox

    from streamdeck_ui.gui import _official_action_index

    app = QApplication.instance() or QApplication([])
    assert app is not None
    binding = ("net.millaguie.minino.clock", "net.millaguie.minino.clock.action")
    combo = QComboBox()
    combo.addItem("(No Official Plugin)", None)
    for plugin, action in [
        ("com.example.runcommand", "com.example.runcommand.action"),
        ("com.example.clock", "com.example.clock.action"),
        binding,
    ]:
        combo.addItem(f"{action} [{plugin}]", (plugin, action))

    assert _official_action_index(combo, binding) == 3
    assert _official_action_index(combo, ("com.not.there", "com.not.there.action")) == -1


def test_only_actions_that_can_run_here_are_offered(monkeypatch, tmp_path):
    """The dropdown should not dangle a web plugin or a Windows-only one in front of the user."""
    from types import SimpleNamespace

    from streamdeck_ui.elgato_plugin.manifest import load_plugin_manifest

    def bundle(name, manifest):
        root = tmp_path / f"{name}.sdPlugin"
        root.mkdir()
        (root / "manifest.json").write_text(json.dumps(manifest))
        return load_plugin_manifest(root)

    here = bundle("com.demo.here", {"UUID": "com.demo.here", "Name": "Here", "CodePath": "main.py", "Actions": []})
    web = bundle("com.demo.web", {"UUID": "com.demo.web", "Name": "Web", "CodePath": "index.html", "Actions": []})
    windows = bundle(
        "com.demo.windows", {"UUID": "com.demo.windows", "Name": "Win", "CodePathWin": "run.exe", "Actions": []}
    )
    plugins = {manifest.uuid: manifest for manifest in (here, web, windows)}
    monkeypatch.setattr(gui, "api", SimpleNamespace(elgato_plugins=SimpleNamespace(plugins=plugins)))

    assert gui._official_action_problem("com.demo.here") == ""
    assert gui._official_action_problem("com.demo.web") == "needs a web view"
    assert gui._official_action_problem("com.demo.windows") == "no program for this platform"
    assert gui._official_action_problem("com.demo.absent") == "not installed"
