"""Launching official format plugins: the arguments, the environment and the exit."""

import json
import time

import pytest

from streamdeck_ui.elgato_plugin.launcher import app_info, launch_plugin, new_connection_uuid
from streamdeck_ui.elgato_plugin.manifest import ManifestError, load_plugin_manifest
from streamdeck_ui.elgato_plugin.runtime import action_information
from tests.common import TEST_PLUGIN_ACTION, TEST_PLUGIN_UUID, write_executable_plugin

ARGV_PLUGIN = """
import json, os, sys
from pathlib import Path
Path(os.environ["REA_ARGV"]).write_text(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "marker": os.environ.get("REA_MARKER")}))
print("plugin saying hello")
print("plugin complaining", file=sys.stderr)
"""

HANGING_PLUGIN = """
import time
while True:
    time.sleep(0.05)
"""


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_app_info_carries_the_plugin_identity(tmp_path):
    manifest = write_executable_plugin(tmp_path / "p.sdPlugin", "pass")
    info = app_info(manifest, language="es", theme="light", application_version="1.2.3", plugin_version="9")
    assert info["application"]["language"] == "es"
    assert info["application"]["theme"] == "light"
    assert info["application"]["platform"] == "linux"
    assert info["application"]["version"] == "1.2.3"
    assert info["application"]["pluginVersion"] == "9"
    assert info["plugin"] == {"uuid": manifest.uuid, "version": "1.0.0", "name": "Test Plugin"}


def test_action_information_names_the_action_that_was_placed(tmp_path):
    manifest = write_executable_plugin(tmp_path / "p.sdPlugin", "pass")
    placement = type(
        "P",
        (),
        {"action_uuid": TEST_PLUGIN_ACTION, "context": "ctx-9", "settings": {"alpha": 1}},
    )()
    assert action_information(manifest, placement) == {
        "action": TEST_PLUGIN_ACTION,
        "context": "ctx-9",
        "payload": {"settings": {"alpha": 1}},
    }


def test_new_connection_uuids_are_unique():
    assert len({new_connection_uuid() for _ in range(50)}) == 50


def test_launch_passes_the_handshake_arguments_in_order(tmp_path):
    manifest = write_executable_plugin(tmp_path / "p.sdPlugin", ARGV_PLUGIN)
    argv_file = tmp_path / "argv.json"
    lines = []

    process = launch_plugin(
        manifest,
        1234,
        "session-1",
        {"application": {"language": "en"}},
        {"action": TEST_PLUGIN_ACTION},
        log=lambda level, message: lines.append((level, message)),
        extra_environment={"REA_ARGV": str(argv_file), "REA_MARKER": "yes"},
    )
    assert wait_until(lambda: argv_file.is_file() and process.exit_code is not None), "plugin never finished"

    # Both handshake forms are offered to every plugin, the positional one first because that is
    # what the protocol defines. This plugin ignores what it does not expect, so its argv carries
    # exactly five values when it ran positionally; flag runs carry four.
    written = json.loads(argv_file.read_text())
    argv = written["argv"]
    if len(argv) == 4:
        assert argv == ["-port", "1234", "-pluginUUID", "session-1", "-registerEvent", "registerPlugin"]
    else:
        assert argv[:5] == [
            "1234",
            "session-1",
            "registerPlugin",
            json.dumps({"application": {"language": "en"}}),
            json.dumps({"action": TEST_PLUGIN_ACTION}),
        ]
    assert written["cwd"] == str(manifest.root)
    assert written["marker"] == "yes"

    assert wait_until(lambda: ("info", "plugin saying hello") in lines)
    assert ("warning", "plugin complaining") in lines
    assert process.exit_code == 0
    assert not process.running


def test_stop_terminates_a_plugin_that_will_not_quit(tmp_path):
    manifest = write_executable_plugin(tmp_path / "p.sdPlugin", HANGING_PLUGIN)
    process = launch_plugin(manifest, 1, "session-2", {}, {})
    assert wait_until(lambda: process.running)
    code = process.stop(timeout=5.0)
    assert not process.running
    assert code is not None


def test_a_plugin_without_code_cannot_be_launched(tmp_path):
    root = tmp_path / "web.sdPlugin"
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps({"UUID": "com.example.web", "Name": "Web", "Version": "1", "CodePath": "index.html", "Actions": []})
    )
    manifest = load_plugin_manifest(root)
    assert manifest.code_kind == "web"
    with pytest.raises(ValueError, match="no executable"):
        launch_plugin(manifest, 1, "session", {}, {})


def test_a_missing_executable_is_reported(tmp_path):
    root = tmp_path / "gone.sdPlugin"
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps({"UUID": "com.example.gone", "Name": "Gone", "Version": "1", "CodePath": "bin/thing", "Actions": []})
    )
    manifest = load_plugin_manifest(root)
    with pytest.raises(FileNotFoundError, match="not a file"):
        launch_plugin(manifest, 1, "session", {}, {})


def test_a_bundle_we_cannot_name_is_refused(tmp_path):
    root = tmp_path / "bad"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"Name": "Bad"}))
    with pytest.raises(ManifestError):
        load_plugin_manifest(root)


GO_STYLE_PLUGIN = """
import os, sys
args = sys.argv[1:]
if args and not args[0].startswith("-"):
    raise SystemExit(2)   # Go's flag package stops at the first bare argument and finds no flags
open(os.environ["REA_OK"], "w").write(",".join(args))
"""

POSITIONAL_STYLE_PLUGIN = """
import os, sys
if len(sys.argv) < 4 or sys.argv[1].startswith("-"):
    raise SystemExit(2)   # reads port, uuid and event as plain arguments
open(os.environ["REA_OK"], "w").write(",".join(sys.argv[1:3]))
"""


def launch_and_wait(tmp_path, source, name="p.sdPlugin", port=4321, connection="s", keep_memory=False):
    from streamdeck_ui.elgato_plugin import launcher

    if not keep_memory:
        launcher.KNOWN_HANDSHAKES.pop(TEST_PLUGIN_UUID, None)
    manifest = write_executable_plugin(tmp_path / name, source)
    lines = []
    process = launcher.launch_plugin(
        manifest,
        port,
        connection,
        {},
        {},
        log=lambda level, message: lines.append((level, message)),
        extra_environment={"REA_OK": str(tmp_path / "ok")},
    )
    assert wait_until(lambda: not process.running), "plugin never finished"
    return process, lines


def test_a_plugin_that_dies_under_positionals_gets_the_flag_form(tmp_path):
    process, lines = launch_and_wait(tmp_path, GO_STYLE_PLUGIN)
    assert any("quit right away under the positional handshake" in message for _, message in lines)
    assert any("retrying with the flags handshake" in message for _, message in lines)
    assert (tmp_path / "ok").read_text() == "-port,4321,-pluginUUID,s,-registerEvent,registerPlugin"
    assert process.arguments[1] == "-port"


def test_the_working_form_is_remembered_for_next_time(tmp_path):
    from streamdeck_ui.elgato_plugin import launcher

    # Only a process that proved it can stay up feeds the memory, so this one lingers clearly
    # past the early-exit window instead of racing a hardcoded number.
    linger = launcher.EARLY_EXIT_SECONDS + 0.3
    lingering = GO_STYLE_PLUGIN.replace(
        'open(os.environ["REA_OK"], "w").write(",".join(args))',
        'open(os.environ["REA_OK"], "w").write(",".join(args)); import time; time.sleep(%r)' % linger,
    )
    launch_and_wait(tmp_path, lingering)
    assert launcher.KNOWN_HANDSHAKES[TEST_PLUGIN_UUID] == "flags"
    _, lines = launch_and_wait(tmp_path / "second", GO_STYLE_PLUGIN, port=1, keep_memory=True)
    assert not any("quit right away" in message for _, message in lines), lines


def test_a_positional_only_plugin_works_from_the_first_try(tmp_path):
    process, lines = launch_and_wait(tmp_path, POSITIONAL_STYLE_PLUGIN)
    assert process.exit_code == 0, lines
    assert (tmp_path / "ok").read_text() == "4321,s"
    assert not any("retrying" in message for _, message in lines)


def test_a_plugin_dead_under_both_forms_is_reported_once_clearly(tmp_path):
    process, lines = launch_and_wait(tmp_path, "raise SystemExit(3)")
    tail = " ".join(message for _, message in lines[-2:])
    assert "never stayed up" in tail and "exit code 3" in tail


def test_a_fast_clean_exit_is_not_retried(tmp_path):
    # Trivial scripts end inside the patience window all the time; only a failing one was rejected.
    process, lines = launch_and_wait(tmp_path, "pass")
    assert not any("quit right away" in message for _, message in lines)
    assert not any("retrying" in message for _, message in lines)


def test_a_death_by_signal_also_gets_the_second_form(tmp_path):
    # Go panics exit non-zero, but a runtime killed outright says nothing at all and still retries.
    process, lines = launch_and_wait(tmp_path, "import os\nos.kill(os.getpid(), 9)\n", name="boom.sdPlugin")
    assert any("retrying with the flags handshake" in message for _, message in lines), lines
