"""A second instance asks the running one to show its window through the CLI socket."""

import tempfile
import threading
from unittest.mock import MagicMock

from streamdeck_ui.cli import server


def test_request_show_window_calls_callback(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    shown = threading.Event()
    cli = server.CLIStreamDeckServer(MagicMock(), MagicMock(), on_show_window=shown.set)
    cli.start()
    try:
        # The server binds its socket in its own thread.
        sock_path = tmp_path / "streamdeck_ui.sock"
        for _ in range(50):
            if sock_path.exists():
                break
            threading.Event().wait(0.05)
        assert server.request_show_window()
        assert shown.wait(timeout=3)
    finally:
        cli.stop()


def test_request_show_window_without_server(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    assert not server.request_show_window()
