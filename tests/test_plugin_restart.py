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
