"""A whole plugin, started as a process, talking the official protocol to the host.

Nothing here is mocked in the middle: the plugin is a real process written to a temporary
directory, it opens a real websocket and speaks the protocol's JSON directly. Only
the hardware is replaced by a recording sink.
"""

import functools
import json
import logging
from pathlib import Path

import pytest

from PySide6.QtCore import QEventLoop, QTimer

from streamdeck_ui.elgato_plugin.launcher import launch_plugin
from streamdeck_ui.elgato_plugin.runtime import PluginRuntime
from streamdeck_ui.elgato_plugin.server import ElgatoPluginHost

from tests.common import TEST_PLUGIN_ACTION, TEST_PLUGIN_UUID, write_executable_plugin

HERE = Path(__file__).resolve().parent

IMAGE_BYTES = b"fake-image"

PLUGIN = """
import json, os, sys
from pathlib import Path

exec(Path(os.environ["REA_WS_CLIENT"]).read_text(), globals())

port, session, register_event, info, action_info = sys.argv[1:6]
events_path = Path(os.environ["REA_EVENTS"])


def note(kind, **extra):
    with events_path.open("a") as handle:
        handle.write(json.dumps({"kind": kind, **extra}) + "\\n")


print("plugin up", file=sys.stderr, flush=True)
note("argv", register_event=register_event, info=json.loads(info), action_info=json.loads(action_info))

sock = connect(int(port))
send_json(sock, {"event": register_event, "uuid": session})

try:
    while True:
        message = read_json(sock)
        if message is None:
            note("closed")
            break
        name = message.get("event")
        note("received", event=name, message=message)
        if name == "willAppear":
            context = message["context"]
            send_json(sock, {"event": "setImage", "context": context,
                             "payload": {"image": "data:image/png;base64," + IMAGE_B64, "target": 0}})
            send_json(sock, {"event": "setTitle", "context": context,
                             "payload": {"title": "PRUEBA", "target": 0}})
            send_json(sock, {"event": "setState", "context": context, "payload": {"state": 1}})
            send_json(sock, {"event": "setSettings", "context": context, "action": message["action"],
                             "payload": {"alpha": 1}, "targetContext": context})
            send_json(sock, {"event": "getGlobalSettings", "uuid": session})
            send_json(sock, {"event": "setGlobalSettings", "uuid": session, "payload": {"global": True}})
            send_json(sock, {"event": "showOk", "context": context})
            send_json(sock, {"event": "openUrl", "payload": {"url": "https://example.invalid/deep"}})
            send_json(sock, {"event": "logMessage", "payload": {"message": "desde dentro"}})
        elif name == "keyDown":
            send_json(sock, {"event": "setTitle", "context": message["context"], "payload": {"title": "pulsado"}})
        elif name == "keyUp":
            note("bye")
            break
except Exception as error:
    note("error", error=repr(error))
    raise
"""

IMAGE_B64 = "ZmFrZS1pbWFnZQ=="


class RecordingSink:
    def __init__(self):
        self.images = []
        self.titles = []
        self.oks = []
        self.urls = []
        self.logs = []
        self.saves = 0
        self.settings_changed = []

    def settings(self, context, settings):
        self.settings_changed.append((context, settings))

    def set_key_image(self, device_id, key, payload):
        self.images.append((device_id, key, payload))

    def set_key_title(self, device_id, key, title):
        self.titles.append((device_id, key, title))

    def show_ok(self, device_id, key):
        self.oks.append((device_id, key))

    def open_url(self, url):
        self.urls.append(url)

    def log(self, plugin_uuid, level, message):
        self.logs.append((plugin_uuid, level, message))

    def save(self):
        self.saves += 1


def wait_until(predicate, timeout_ms=15000, interval_ms=10):
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(interval_ms)
    poll.timeout.connect(lambda: predicate() and loop.quit())
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(loop.quit)
    poll.start()
    watchdog.start(timeout_ms)
    loop.exec()
    poll.stop()
    return predicate()


@pytest.fixture
def runtime(tmp_path, qcore_app):
    events = tmp_path / "events.jsonl"
    root = tmp_path / "net.millaguie.testplugin.sdPlugin"
    manifest = write_executable_plugin(
        root,
        'import base64\nIMAGE_B64 = base64.b64encode(b"fake-image").decode()\n' + PLUGIN,
    )
    sink = RecordingSink()
    host = ElgatoPluginHost()
    environment = {"REA_WS_CLIENT": str(HERE / "mini_ws_client.py"), "REA_EVENTS": str(events)}
    instance = PluginRuntime(
        host,
        sink,
        language="es",
        application_version="0.0.0-test",
        persist=sink.save,
        on_settings=sink.settings,
        launcher=functools.partial(launch_plugin, extra_environment=environment),
    )
    instance.add_plugin(manifest)
    instance.start()
    yield instance, sink, manifest, events
    instance.stop()


def events_seen(events: Path) -> list[str]:
    if not events.is_file():
        return []
    found = []
    for line in events.read_text().splitlines():
        if line.strip():
            name = json.loads(line).get("event")
            if name:
                found.append(name)
    return found


def events_of(events: Path, kind: str, event: str = None) -> list[dict]:
    if not events.is_file():
        return []
    found = []
    for line in events.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") == kind and (event is None or record.get("event") == event):
            found.append(record)
    return found


def test_the_plugin_runs_and_talks_to_the_host(runtime, caplog):
    instance, sink, manifest, events = runtime

    instance.device_connected("dev-1", {"name": "Stream Deck MK.2", "size": {"columns": 5, "rows": 3}})
    placement = instance.place(
        manifest.uuid,
        TEST_PLUGIN_ACTION,
        "dev-1",
        3,
        positions={"column": 0, "row": 1},
        coordinates={"column": 0, "row": 1},
        settings={"seed": 1},
    )

    assert wait_until(lambda: sink.images), f"plugin never drew; events: {events_seen(events)}"
    assert sink.images == [("dev-1", 3, IMAGE_BYTES)]

    assert wait_until(lambda: len(sink.titles) == 1)
    assert sink.titles == [("dev-1", 3, "PRUEBA")]

    assert wait_until(lambda: placement.state == 1)
    assert wait_until(lambda: placement.settings == {"alpha": 1})
    assert wait_until(lambda: sink.saves >= 1)
    assert wait_until(lambda: (placement.context, {"alpha": 1}) in sink.settings_changed)
    assert sink.oks == [("dev-1", 3)]
    assert sink.urls == ["https://example.invalid/deep"]

    assert wait_until(lambda: instance.plugin_settings.get(TEST_PLUGIN_UUID) == {"global": True})
    global_replies = events_of(events, "received", "didReceiveGlobalSettings")
    assert [reply["message"]["payload"]["settings"] for reply in global_replies] == [{}, {"global": True}]

    caplog.set_level(logging.INFO, logger="streamdeck_ui.elgato_plugin.runtime")
    assert any("plugin up" in message for _, _, message in sink.logs)
    assert instance.connected_plugins == [TEST_PLUGIN_UUID]

    settings_reply = events_of(events, "received", "didReceiveSettings")
    assert settings_reply[0]["message"]["payload"]["settings"] == {"alpha": 1}

    argv = events_of(events, "argv")[0]
    assert argv["register_event"] == "registerPlugin"
    assert argv["info"]["application"]["language"] == "es"
    assert argv["action_info"]["action"] == TEST_PLUGIN_ACTION
    assert argv["action_info"]["payload"]["settings"] == {"seed": 1}

    will_appear = events_of(events, "received", "willAppear")[0]["message"]
    assert will_appear["context"] == placement.context
    assert will_appear["action"] == TEST_PLUGIN_ACTION
    assert will_appear["payload"]["positions"] == {"column": 0, "row": 1}
    assert will_appear["payload"]["controller"] == "Keypad"

    seen = events_seen(events)
    assert seen.index("deviceDidConnect") < seen.index("willAppear")

    instance.press(placement.context)
    assert wait_until(lambda: len(sink.titles) == 2), f"no keyDown; events: {seen}"
    assert sink.titles[1] == ("dev-1", 3, "pulsado")

    process = instance.processes[TEST_PLUGIN_UUID]
    instance.release(placement.context)
    assert wait_until(lambda: process.exit_code is not None), "plugin did not exit after keyUp"

    instance.stop()


def test_a_plugin_whose_key_left_is_told_to_go_away(runtime):
    instance, sink, manifest, events = runtime
    placement = instance.place(manifest.uuid, TEST_PLUGIN_ACTION, "dev-1", 0)
    assert wait_until(lambda: sink.images)

    instance.unplace(placement.context)
    assert wait_until(lambda: "willDisappear" in events_seen(events)), f"events: {events_seen(events)}"
    assert placement.context not in instance.placements


def test_web_plugins_are_not_spawned(tmp_path, qcore_app):
    root = tmp_path / "com.example.web.sdPlugin"
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "UUID": "com.example.web",
                "Name": "Web",
                "Version": "1",
                "CodePath": "index.html",
                "Actions": [{"UUID": "com.example.web.action", "Name": "A"}],
            }
        )
    )
    from streamdeck_ui.elgato_plugin.manifest import load_plugin_manifest

    sink = RecordingSink()
    instance = PluginRuntime(ElgatoPluginHost(), sink)
    instance.add_plugin(load_plugin_manifest(root))
    instance.start()
    try:
        instance.place("com.example.web", "com.example.web.action", "dev-1", 0)
        assert instance.processes == {}
        assert instance.placements
    finally:
        instance.stop()


def test_pressing_an_unknown_context_is_harmless(runtime):
    instance, sink, manifest, events = runtime
    instance.press("no-existe")
    instance.rotate("no-existe", 1)
    assert not events_seen(events)


def test_a_program_that_is_not_in_the_bundle_costs_one_key_not_the_page(tmp_path, qcore_app):
    """A bundle may declare a linux code path and ship a Windows-only build. A page sync must
    survive that, and the same bundle must not be retried on every repaint."""

    class FakeProcess:
        def __init__(self, arguments):
            self.arguments = arguments
            self.running = True

        def stop(self, timeout=3.0):
            self.running = False
            return 0

    started = []

    def launcher(manifest, port, session_uuid, info, action_info, **kwargs):
        if manifest.uuid == "com.example.broken":
            raise FileNotFoundError(f"{manifest.uuid} declares {manifest.code_path} which is not a file")
        started.append(manifest.uuid)
        return FakeProcess([str(manifest.code_path)])

    def bundle(name: str) -> Path:
        root = tmp_path / f"{name}.sdPlugin"
        root.mkdir()
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "Name": name,
                    "Version": "1",
                    "CodePath": "bin/program",
                    "Actions": [{"UUID": f"{name}.action", "Name": "A"}],
                }
            )
        )
        return root

    from streamdeck_ui.elgato_plugin.manifest import load_plugin_manifest

    instance = PluginRuntime(ElgatoPluginHost(), RecordingSink(), launcher=launcher)
    instance.add_plugin(load_plugin_manifest(bundle("com.example.broken")))
    instance.add_plugin(load_plugin_manifest(bundle("com.example.good")))
    instance.start()
    try:
        instance.place("com.example.broken", "com.example.broken.action", "dev-1", 0)
        instance.place("com.example.good", "com.example.good.action", "dev-1", 1)

        assert started == ["com.example.good"]
        assert list(instance.processes) == ["com.example.good"]
        assert len(instance.placements) == 2

        instance.place("com.example.broken", "com.example.broken.action", "dev-1", 2)
        assert started == ["com.example.good"]
    finally:
        instance.stop()
