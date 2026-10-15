import importlib
import importlib.util
import sys

import pytest
from StreamDeck.Devices.StreamDeckMini import StreamDeckMini
from StreamDeck.Devices.StreamDeckOriginal import StreamDeckOriginal
from StreamDeck.Devices.StreamDeckOriginalV2 import StreamDeckOriginalV2
from StreamDeck.Devices.StreamDeckPlus import StreamDeckPlus

from streamdeck_ui import device_detection
from streamdeck_ui.device_detection import (
    DetectedDevice,
    detect,
    model_for_product_string,
    normalize_product_string,
    physical_port,
)
from tests.common import FakeTransport, FakeTransportDevice


# The Plus XL only exists from streamdeck 0.10.0 on; the app still has to work without it.
def _optional(module_name: str):
    if importlib.util.find_spec(module_name) is None:
        return None
    module = importlib.import_module(module_name)
    return getattr(module, module_name.rpartition(".")[2])


StreamDeckPlusXL = _optional("StreamDeck.Devices.StreamDeckPlusXL")


@pytest.mark.parametrize(
    "product_string,expected",
    [
        ("Stream Deck", "streamdeck"),
        ("Stream Deck +", "streamdeckplus"),
        ("Stream Deck MK.2", "streamdeckmk2"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_product_string(product_string, expected):
    assert normalize_product_string(product_string) == expected


@pytest.mark.parametrize(
    "product_string,expected",
    [
        ("Stream Deck + XL", StreamDeckPlusXL),
        ("Stream Deck +", StreamDeckPlus),
        ("Stream Deck + touch bar", StreamDeckPlus),
        ("Elgato Stream Deck MK.2", StreamDeckOriginalV2),
        ("Stream Deck Mini Discord Edition", StreamDeckMini),
        ("Stream Deck", StreamDeckOriginal),
        ("Marshall Analyzer", None),
        ("Stream Deck Network Dock", None),
        ("", None),
        (None, None),
    ],
)
def test_model_for_product_string(product_string, expected):
    assert model_for_product_string(product_string) is expected


def _sysfs(tmp_path, name, busnum, devnum):
    entry = tmp_path / name
    entry.mkdir()
    (entry / "busnum").write_text(f"{busnum}\n")
    (entry / "devnum").write_text(f"{devnum}\n")
    return entry


def test_physical_port_parses_hidapi_usb_core_path():
    assert physical_port("7-1.3.2.1:1.0") == "7-1.3.2.1"


def test_physical_port_resolves_topology_slot(tmp_path):
    _sysfs(tmp_path, "usb1", 1, 1)
    _sysfs(tmp_path, "3-1", 3, 6)
    _sysfs(tmp_path, "3-1.4", 3, 7)

    assert physical_port("0003:0007:00:00.0", str(tmp_path)) == "3-1.4"


def test_physical_port_unknown_device(tmp_path):
    _sysfs(tmp_path, "usb1", 1, 1)
    assert physical_port("0003:0099:00:00.0", str(tmp_path)) is None


def test_physical_port_without_path():
    assert physical_port(None) is None


def test_detect_uses_product_id_for_known_sku(tmp_path):
    device = FakeTransportDevice(0x006D, "Stream Deck MK.2", "/dev/hidraw0", "BL44K1B00290")

    results = detect(FakeTransport([device]), str(tmp_path))

    assert len(results) == 1
    assert results[0].detected_by == "product-id"
    assert isinstance(results[0].deck, StreamDeckOriginalV2)
    assert results[0].serial == "BL44K1B00290"
    assert results[0].stable_key == "BL44K1B00290"


def test_detect_falls_back_to_product_string_for_unknown_sku(tmp_path):
    device = FakeTransportDevice(0x00FF, "Stream Deck + XL", "0003:0007:00:00.0")
    _sysfs(tmp_path, "3-1.4", 3, 7)

    results = detect(FakeTransport([device]), str(tmp_path))

    if StreamDeckPlusXL is None:
        assert results[0].deck is None
        assert results[0].supported is False
    else:
        assert results[0].detected_by == "product-string"
        assert isinstance(results[0].deck, StreamDeckPlusXL)
    assert results[0].port == "3-1.4"
    assert results[0].stable_key == "port:3-1.4"


def test_detect_reports_unsupported_device(tmp_path):
    device = FakeTransportDevice(0x00FE, "Stream Deck Network Dock", "0003:0008:00:00.0", "ABC")

    results = detect(FakeTransport([device]), str(tmp_path))

    assert results[0].supported is False
    assert results[0].detected_by == "unsupported"
    assert results[0].stable_key == "ABC"


def test_detect_handles_device_without_strings():
    """Observed with a real Original V2: hidapi reports no product string nor serial."""
    device = FakeTransportDevice(0x006D, "", "7-1.3.2.1:1.0")

    results = detect(FakeTransport([device]), "/nonexistent")

    assert isinstance(results[0].deck, StreamDeckOriginalV2)
    assert results[0].detected_by == "product-id"
    assert results[0].port == "7-1.3.2.1"
    assert results[0].stable_key == "port:7-1.3.2.1"


def test_detect_enumerates_every_elgato_device():
    transport = FakeTransport([])
    detect(transport)
    assert transport.calls == [(0x0FD9, None)]


def test_stable_key_falls_back_to_path():
    detected = DetectedDevice(None, 0x00FE, "Unknown", "/dev/hidraw9", None, "", "unsupported")
    assert detected.stable_key == "path:/dev/hidraw9"


def test_a_library_too_old_for_a_sku_still_starts_and_keeps_the_rest(monkeypatch, tmp_path):
    """streamdeck 0.9.x knows neither the Plus XL nor the Studio, and the app must not die there."""
    for missing in ("StreamDeck.Devices.StreamDeckPlusXL", "StreamDeck.Devices.StreamDeckStudio"):
        monkeypatch.setitem(sys.modules, missing, None)

    try:
        old = importlib.reload(device_detection)

        assert old.MODEL_BY_PRODUCT_ID[0x00C6] is None
        assert old.MODEL_BY_PRODUCT_ID[0x006D] is not None

        mk2 = FakeTransportDevice(0x006D, "Elgato Stream Deck MK.2", "0003:0009:00:00.0")
        plus_xl = FakeTransportDevice(0x00C6, "Stream Deck + XL", "0003:0010:00:00.0")

        results = old.detect(FakeTransport([mk2, plus_xl]), "/nonexistent")

        assert results[0].supported is True
        assert results[1].supported is False
        assert results[1].detected_by == "unsupported"
    finally:
        monkeypatch.undo()
        importlib.reload(device_detection)
