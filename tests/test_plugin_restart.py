"""Restarting a dead plugin must not leave the old reader thread on the new socket."""

import logging
import time
from pathlib import Path

from streamdeck_ui.plugin_system.plugin_manager import PluginInstance
from streamdeck_ui.plugin_system.schema import PluginManifest

# Sends numbered log messages as fast as it can, so two reader threads on
# the same socket would split frames and break the sequence.
PLUGIN_SCRIPT = """
import socket, sys, time
from streamdeck_ui.plugin_system.protocol import MessageType, ProtocolMessage

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(sys.argv[1])
i = 0
while True:
    msg = ProtocolMessage(type=MessageType.LOG_MESSAGE, payload={"level": "info", "message": str(i)})
    s.sendall(msg.to_bytes())
    i += 1
    time.sleep(0.001)
"""


def _wait_for(predicate, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_restart_closes_old_reader(tmp_path: Path, monkeypatch, caplog):
    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("PYTHONPATH", str(repo_root))
    (tmp_path / "plugin.py").write_text(PLUGIN_SCRIPT)
    manifest = PluginManifest(name="t", version="1", description="t", author="t", entry_point="plugin.py")
    instance = PluginInstance("t", "t_0", manifest, tmp_path, {}, "SERIAL", 0, 0, False)

    received: list[int] = []
    instance.on_log_message = lambda level, msg: received.append(int(msg))

    assert instance.start()
    try:
        assert _wait_for(lambda: len(received) > 50)
        old_thread = instance.comm_thread
        assert old_thread is not None and instance.process is not None

        instance.process.kill()
        instance.process.wait()

        received.clear()
        with caplog.at_level(logging.ERROR):
            assert instance.restart()
            assert _wait_for(lambda: len(received) > 500)

        assert not old_thread.is_alive()
        assert "Failed to receive message" not in caplog.text
        # The new plugin counts from 0. A missing or repeated number means a frame was lost.
        start = len(received) - 1 - received[::-1].index(0)
        new = received[start:]
        assert new == list(range(len(new)))
    finally:
        instance.stop()


NOISY_PLUGIN_SCRIPT = """
import socket, sys
from streamdeck_ui.plugin_system.protocol import MessageType, ProtocolMessage

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(sys.argv[1])
# Far more than a pipe buffer (64 KiB) on both streams.
for i in range(5000):
    print("stdout line", i, "x" * 40, flush=True)
    print("stderr line", i, "x" * 40, file=sys.stderr, flush=True)
s.sendall(ProtocolMessage(type=MessageType.LOG_MESSAGE, payload={"level": "info", "message": "done"}).to_bytes())
import time
time.sleep(60)
"""


def _instance(tmp_path: Path, monkeypatch, script: str, manager=None) -> PluginInstance:
    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("PYTHONPATH", str(repo_root))
    (tmp_path / "plugin.py").write_text(script)
    manifest = PluginManifest(name="t", version="1", description="t", author="t", entry_point="plugin.py")
    instance = PluginInstance("t", "t_0", manifest, tmp_path, {}, "SERIAL", 0, 0, False)
    if manager is not None:
        manager.instances[instance.instance_id] = instance
    return instance


def test_plugin_output_does_not_block(tmp_path: Path, monkeypatch):
    instance = _instance(tmp_path, monkeypatch, NOISY_PLUGIN_SCRIPT)
    received: list[str] = []
    instance.on_log_message = lambda _level, msg: received.append(msg)

    assert instance.start()
    try:
        assert _wait_for(lambda: "done" in received, timeout=20)
        # The socket message can arrive before the reader thread drains the last lines.
        assert _wait_for(lambda: any("stderr line 4999" in line for line in instance._stderr_tail))
    finally:
        instance.process.kill()
        instance.stop()


def test_restart_all_instances_starts_new_process(tmp_path: Path, monkeypatch):
    from streamdeck_ui.plugin_system.plugin_manager import PluginManager

    manager = PluginManager(tmp_path / "plugins")
    instance = _instance(tmp_path, monkeypatch, PLUGIN_SCRIPT, manager)
    received: list[int] = []
    instance.on_log_message = lambda _level, msg: received.append(int(msg))

    assert instance.start()
    try:
        old_pid = instance.process.pid
        manager.restart_all_instances().join(timeout=30)
        assert instance.running
        assert instance.process.pid != old_pid
        received.clear()
        assert _wait_for(lambda: len(received) > 10)
    finally:
        instance.stop()
