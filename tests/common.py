from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

from streamdeck_ui.api import StreamDeckServer
from streamdeck_ui.model import ButtonMultiState, ButtonState, DeckState

STREAMDECK_SERIAL = "DL4XXXXXX"
STREAMDECK_KEY_COUNT = 3
STREAMDECK_TYPE = "Stream Deck Original"


class FakeTransportDevice:
    """Stands in for a StreamDeck.Transport.Device, without touching real hardware."""

    def __init__(self, product_id, product_string="", path="7-1.1:1.0", serial_number=""):
        self.device_info = {
            "product_id": product_id,
            "product_string": product_string,
            "path": path,
            "serial_number": serial_number,
        }

    def product_id(self):
        return self.device_info["product_id"]

    def path(self):
        return self.device_info["path"]

    def read_feature(self, report_id, length):
        return b"\x00" * length

    def write_feature(self, report_id, data):
        return len(data)

    def open(self):
        pass

    def close(self):
        pass

    def write(self, data):
        return len(data)

    def read(self, length):
        return b"\x00" * length


class FakeTransport:
    def __init__(self, devices):
        self._devices = devices
        self.calls = []

    def enumerate(self, vid=None, pid=None):
        self.calls.append((vid, pid))
        return list(self._devices)


# the number of pages in the test state
PAGES = 3


def func_gen_page() -> dict:
    page = {}
    for i in range(0, PAGES):
        page[i] = ButtonMultiState(
            state=0,
            states={key_id: ButtonState(text=f"State {key_id}") for key_id in range(0, STREAMDECK_KEY_COUNT)},
        )
    return page


class TestableStreamDeckServer(StreamDeckServer):
    """A testable version of the StreamDeckServer."""

    def __init__(self):
        super().__init__()

    def expose_save_state(self):
        return self._save_state


def create_test_api_server() -> TestableStreamDeckServer:
    """Created a testable version of the StreamDeckServer.
    with some default values."""
    api = TestableStreamDeckServer()
    deck = MagicMock()
    deck.key_layout.return_value = (1, 1)
    deck.key_count.return_value = STREAMDECK_KEY_COUNT
    display_handler = MagicMock()
    display_handler.get_image.return_value = None
    api.display_handlers[STREAMDECK_SERIAL] = display_handler

    api.decks_by_serial[STREAMDECK_SERIAL] = deck
    api.dimmers[STREAMDECK_SERIAL] = MagicMock()

    api.state = {
        STREAMDECK_TYPE: DeckState(
            buttons={
                0: func_gen_page(),
                1: func_gen_page(),
                2: func_gen_page(),
            },
        ),
        STREAMDECK_SERIAL: DeckState(
            buttons={
                0: func_gen_page(),
                1: func_gen_page(),
            },
        ),
    }
    # we don't want to save the state to disk
    api._save_state = MagicMock()
    api.export_config = MagicMock()
    api.import_config = MagicMock()
    return api


TEST_PLUGIN_UUID = "net.millaguie.testplugin"
TEST_PLUGIN_ACTION = "net.millaguie.testplugin.action"


def write_executable_plugin(root, source: str, code_path: str = "plugin.py", manifest: Optional[dict] = None):
    """Writes a plugin bundle in the official layout that launches a Python script."""
    import json
    import sys

    from streamdeck_ui.elgato_plugin.manifest import load_plugin_manifest

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    script = root / code_path
    script.write_text(f"#!{sys.executable}\n{source}")
    script.chmod(0o755)

    document = {
        "UUID": TEST_PLUGIN_UUID,
        "Name": "Test Plugin",
        "Version": "1.0.0",
        "CodePath": code_path,
        "Actions": [{"UUID": TEST_PLUGIN_ACTION, "Name": "Test action"}],
    }
    document.update(manifest or {})
    (root / "manifest.json").write_text(json.dumps(document))
    return load_plugin_manifest(root)
