import json

import pytest

from streamdeck_ui.elgato_plugin.manifest import (
    ManifestError,
    PLATFORM_LINUX,
    PLATFORM_MAC,
    discover_plugin_directories,
    load_plugin_manifest,
)

MINIMAL = {
    "Name": "Demo",
    "UUID": "com.example.demo",
    "Version": "1.0",
}


def write_manifest(directory, manifest):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


@pytest.fixture
def plugin(tmp_path):
    return write_manifest(
        tmp_path / "com.example.demo.sdPlugin",
        {
            **MINIMAL,
            "PluginVersion": 2,
            "SDKVersion": 2,
            "Category": "Demo",
            "CodePath": "app.html",
            "PropertyInspectorPath": "pi.html",
            "OS": [{"Platform": "mac", "MinimumVersion": "10.11"}, {"Platform": "windows", "MinimumVersion": "10"}],
            "Actions": [
                {
                    "UUID": "com.example.demo.key",
                    "Name": "Demo key",
                    "Tooltip": "Press me",
                    "Icon": "icons/key",
                    "States": [{"Image": "icons/key"}],
                },
                {
                    "UUID": "com.example.demo.dial",
                    "Name": "Demo dial",
                    "Controllers": {
                        "Encoder": {
                            "PropertyInspectorPath": "pi-dial.html",
                            "States": [{"Image": "icons/dial", "Name": "Dial"}],
                        }
                    },
                },
            ],
        },
    )


def test_minimal_manifest_loads(tmp_path):
    root = write_manifest(tmp_path / "p.sdPlugin", MINIMAL)

    manifest = load_plugin_manifest(root)

    assert manifest.uuid == "com.example.demo"
    assert manifest.name == "Demo"
    assert manifest.version == "1.0"
    assert manifest.actions == []
    assert manifest.code_path is None
    assert manifest.code_kind == "none"
    assert manifest.plugin_version is None
    assert manifest.platforms == []


def test_missing_manifest_file(tmp_path):
    (tmp_path / "empty.sdPlugin").mkdir()

    with pytest.raises(ManifestError):
        load_plugin_manifest(tmp_path / "empty.sdPlugin")


def test_manifest_that_is_not_json(tmp_path):
    root = tmp_path / "broken.sdPlugin"
    root.mkdir()
    (root / "manifest.json").write_text("{no es json", encoding="utf-8")

    with pytest.raises(ManifestError):
        load_plugin_manifest(root)


def test_a_bundle_with_no_uuid_anywhere_is_refused(tmp_path):
    root = write_manifest(tmp_path / "loose", {"Name": "x", "Version": "1"})

    with pytest.raises(ManifestError, match="UUID"):
        load_plugin_manifest(root)


def test_action_without_uuid_names_the_file(tmp_path):
    root = write_manifest(tmp_path / "bad.sdPlugin", {**MINIMAL, "Actions": [{"Name": "sin uuid"}]})

    with pytest.raises(ManifestError, match="manifest.json"):
        load_plugin_manifest(root)


def test_web_plugin_needs_a_view(plugin):
    manifest = load_plugin_manifest(plugin)

    assert manifest.code_kind == "web"
    assert manifest.needs_web_view is True
    assert manifest.executable() is None


def test_executable_plugin_resolves_its_binary(tmp_path):
    root = write_manifest(tmp_path / "native.sdPlugin", {**MINIMAL, "CodePath": "linux/native.node"})

    manifest = load_plugin_manifest(root)

    assert manifest.code_kind == "executable"
    assert manifest.needs_web_view is False
    assert manifest.executable() == (root / "linux/native.node").resolve()


def test_platform_specific_code_path_wins(tmp_path):
    root = write_manifest(
        tmp_path / "native.sdPlugin",
        {
            **MINIMAL,
            "CodePath": "windows/thing.exe",
            "OS": [{"Platform": PLATFORM_LINUX, "CodePath": "linux/thing.node"}],
        },
    )

    manifest = load_plugin_manifest(root)

    assert manifest.code_path_for_platform(PLATFORM_LINUX) == "linux/thing.node"
    assert manifest.code_path_for_platform("windows") == "windows/thing.exe"
    assert manifest.resolves_on(PLATFORM_LINUX) is True


def test_official_platform_list_is_not_a_refusal_on_linux(plugin):
    manifest = load_plugin_manifest(plugin)

    assert manifest.platforms == ["mac", "windows"]
    assert manifest.declares_platform(PLATFORM_MAC) is True
    assert manifest.declares_platform(PLATFORM_LINUX) is False


def test_unclaimed_platforms_are_not_excluding_anything(tmp_path):
    manifest = load_plugin_manifest(write_manifest(tmp_path / "p.sdPlugin", MINIMAL))

    assert manifest.declares_platform("anything") is True


def test_actions_are_indexed(plugin):
    manifest = load_plugin_manifest(plugin)

    assert manifest.action_uuids == ["com.example.demo.key", "com.example.demo.dial"]
    assert manifest.action("com.example.demo.inventado") is None
    assert manifest.action("com.example.demo.dial").controllers_offered == ["Encoder"]


def test_property_inspector_falls_back_from_controller_to_plugin(plugin):
    manifest = load_plugin_manifest(plugin)

    assert manifest.property_inspector_for("com.example.demo.dial", "Encoder") == "pi-dial.html"
    assert manifest.property_inspector_for("com.example.demo.dial") == "pi.html"
    assert manifest.property_inspector_for("com.example.demo.key") == "pi.html"
    assert manifest.property_inspector_for("com.example.demo.inventado") == "pi.html"


def test_state_images_prefer_the_controller(plugin):
    manifest = load_plugin_manifest(plugin)

    assert manifest.action("com.example.demo.key").state_images() == ["icons/key"]
    assert manifest.action("com.example.demo.dial").state_images("Encoder") == ["icons/dial"]


def test_manifest_details_are_kept(plugin):
    manifest = load_plugin_manifest(plugin)

    assert manifest.plugin_version == 2
    assert manifest.sdk_version == "2"
    assert manifest.category == "Demo"
    assert manifest.raw["OS"][0]["MinimumVersion"] == "10.11"
    assert manifest.action("com.example.demo.key").tooltip == "Press me"
    assert manifest.action("com.example.demo.key").visible_in_actions_list is True


def test_discovery_finds_bundles_and_ignores_noise(tmp_path):
    write_manifest(tmp_path / "plugins" / "com.a.sdPlugin", MINIMAL)
    write_manifest(tmp_path / "plugins" / "com.b.sdPlugin", MINIMAL)
    (tmp_path / "plugins" / "sin.manifiesto.sdPlugin").mkdir()
    (tmp_path / "plugins" / "codigo").mkdir()
    single = write_manifest(tmp_path / "suelto.sdPlugin", MINIMAL)

    found = discover_plugin_directories(tmp_path / "plugins")

    assert [path.name for path in found] == ["com.a.sdPlugin", "com.b.sdPlugin"]
    assert discover_plugin_directories(single) == [single]
    assert discover_plugin_directories(tmp_path / "no-existe") == []


def test_the_folder_carries_the_uuid_when_the_manifest_does_not(tmp_path):
    root = write_manifest(tmp_path / "com.example.cpu.sdPlugin", {"Name": "CPU", "Version": "1.0"})

    assert load_plugin_manifest(root).uuid == "com.example.cpu"


def test_the_code_of_this_platform_is_the_one_we_would_run(tmp_path):
    root = write_manifest(
        tmp_path / "com.lin.thing.sdPlugin",
        {
            "Name": "Lin",
            "Version": "1",
            "CodePathLin": "run.sh",
            "CodePathWin": "thing.exe",
            "Actions": [{"UUID": "com.lin.thing.key", "Name": "Thing"}],
        },
    )

    manifest = load_plugin_manifest(root)

    assert manifest.code_kind == "executable"
    assert manifest.executable(PLATFORM_LINUX).name == "run.sh"
    assert manifest.code_path_for_platform("windows") == "thing.exe"


def test_a_bundle_that_only_ships_windows_code_has_nothing_to_run_here(tmp_path):
    root = write_manifest(
        tmp_path / "com.only.win.sdPlugin",
        {
            "Name": "Win",
            "Version": "1",
            "CodePathWin": "thing.exe",
            "Actions": [{"UUID": "com.only.win.key", "Name": "Thing"}],
        },
    )

    manifest = load_plugin_manifest(root)

    assert manifest.code_kind == "none"
    assert manifest.resolves_on(PLATFORM_LINUX) is False
    assert manifest.code_path_for_platform("windows") == "thing.exe"


def test_controllers_written_as_a_list_are_understood(tmp_path):
    root = write_manifest(
        tmp_path / "com.list.ctrl.sdPlugin",
        {
            **MINIMAL,
            "Controllers": ["Keypad", "Encoder"],
            "Actions": [{"UUID": "com.example.demo.key", "Name": "Key", "Controllers": ["Keypad"]}],
        },
    )

    manifest = load_plugin_manifest(root)

    assert sorted(manifest.controllers) == ["Encoder", "Keypad"]
    assert manifest.action("com.example.demo.key").controllers_offered == ["Keypad"]
