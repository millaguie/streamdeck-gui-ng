"""Geometry contract between every supported Stream Deck shape and the UI.

Expected values are literals on purpose: they describe the physical device, so a
dependency bump that changes a layout or image size breaks here instead of
silently rendering the wrong thing.
"""

import importlib.util
import threading

import pytest

from StreamDeck.Devices.StreamDeck import StreamDeck
from StreamDeck.Devices.StreamDeckMini import StreamDeckMini
from StreamDeck.Devices.StreamDeckNeo import StreamDeckNeo
from StreamDeck.Devices.StreamDeckOriginal import StreamDeckOriginal
from StreamDeck.Devices.StreamDeckOriginalV2 import StreamDeckOriginalV2
from StreamDeck.Devices.StreamDeckPedal import StreamDeckPedal
from StreamDeck.Devices.StreamDeckPlus import StreamDeckPlus
from StreamDeck.Devices.StreamDeckXL import StreamDeckXL

from streamdeck_ui.device_detection import MODEL_BY_PRODUCT_ID, detect
from streamdeck_ui.display.display_grid import DisplayGrid

from tests.common import FakeTransport, FakeTransportDevice


# The Plus XL and the Studio only exist from streamdeck 0.10.0 on. The app keeps running on
# older libraries, so those two rows skip instead of breaking the whole file.
def _optional(module_name: str):
    if importlib.util.find_spec(module_name) is None:
        return None
    module = importlib.import_module(module_name)
    return getattr(module, module_name.rpartition(".")[2])


StreamDeckPlusXL = _optional("StreamDeck.Devices.StreamDeckPlusXL")
StreamDeckStudio = _optional("StreamDeck.Devices.StreamDeckStudio")

# pid, model, rows, cols, keys, key size, image format, visual, dials
_RAW_MATRIX = [
    (0x0063, StreamDeckMini, 2, 3, 6, (80, 80), "BMP", True, 0),
    (0x00B3, StreamDeckMini, 2, 3, 6, (80, 80), "BMP", True, 0),
    (0x0090, StreamDeckMini, 2, 3, 6, (80, 80), "BMP", True, 0),
    (0x0060, StreamDeckOriginal, 3, 5, 15, (72, 72), "BMP", True, 0),
    (0x006D, StreamDeckOriginalV2, 3, 5, 15, (72, 72), "JPEG", True, 0),
    (0x0080, StreamDeckOriginalV2, 3, 5, 15, (72, 72), "JPEG", True, 0),
    (0x00A5, StreamDeckOriginalV2, 3, 5, 15, (72, 72), "JPEG", True, 0),
    (0x006C, StreamDeckXL, 4, 8, 32, (96, 96), "JPEG", True, 0),
    (0x008F, StreamDeckXL, 4, 8, 32, (96, 96), "JPEG", True, 0),
    (0x009A, StreamDeckNeo, 2, 4, 8, (96, 96), "JPEG", True, 0),
    (0x0086, StreamDeckPedal, 1, 3, 3, (0, 0), "", False, 0),
    (0x0084, StreamDeckPlus, 2, 4, 8, (120, 120), "JPEG", True, 4),
    (0x00C6, StreamDeckPlusXL, 4, 9, 36, (112, 112), "JPEG", True, 6),
    (0x00AA, StreamDeckStudio, 2, 16, 32, (80, 120), "JPEG", True, 2),
]


def _sku_params():
    rows = []
    for row in _RAW_MATRIX:
        if row[1] is None:
            rows.append(
                pytest.param(
                    *row, marks=pytest.mark.skip(reason="the installed streamdeck library does not know this SKU")
                )
            )
        else:
            rows.append(pytest.param(*row))
    return rows


SKU_MATRIX = _sku_params()
SKU_IDS = [f"{cls.__name__ if cls else 'unknown'}/{pid:#06x}" for pid, cls, *_ in _RAW_MATRIX]


def _deck(model: type[StreamDeck], product_id: int) -> StreamDeck:
    return model(FakeTransportDevice(product_id))


@pytest.mark.parametrize("product_id,model,rows,cols,keys,size,image_format,visual,dials", SKU_MATRIX, ids=SKU_IDS)
def test_detect_maps_product_id_to_model(product_id, model, rows, cols, keys, size, image_format, visual, dials):
    results = detect(FakeTransport([FakeTransportDevice(product_id)]), "/nonexistent")

    assert results[0].detected_by == "product-id"
    assert type(results[0].deck) is model
    assert results[0].supported is True


@pytest.mark.parametrize("product_id,model,rows,cols,keys,size,image_format,visual,dials", SKU_MATRIX, ids=SKU_IDS)
def test_device_geometry_contract(product_id, model, rows, cols, keys, size, image_format, visual, dials):
    deck = _deck(model, product_id)

    assert deck.key_count() == keys
    assert deck.key_layout() == (rows, cols)
    assert rows * cols == keys
    assert deck.dial_count() == dials
    assert deck.is_visual() is visual
    assert deck.deck_type() == model.DECK_TYPE

    if visual:
        assert deck.key_image_format()["size"] == size
        assert deck.key_image_format()["format"] == image_format


@pytest.mark.parametrize("product_id,model,rows,cols,keys,size,image_format,visual,dials", SKU_MATRIX, ids=SKU_IDS)
def test_display_grid_follows_device_geometry(product_id, model, rows, cols, keys, size, image_format, visual, dials):
    deck = _deck(model, product_id)

    grid = DisplayGrid(threading.Lock(), deck, [0], lambda serial, cpu: None)

    expected_size = (
        size
        if visual
        else (
            StreamDeckOriginal.KEY_PIXEL_WIDTH,
            StreamDeckOriginal.KEY_PIXEL_HEIGHT,
        )
    )
    assert grid.size == expected_size
    assert len(grid.pages[0]) == keys


@pytest.mark.parametrize("product_id,model", [(pid, cls) for pid, cls, *_ in _RAW_MATRIX])
def test_every_supported_sku_is_in_the_detection_table(product_id, model):
    if model is None:
        assert MODEL_BY_PRODUCT_ID.get(product_id) is None
    else:
        assert MODEL_BY_PRODUCT_ID[product_id] is model
