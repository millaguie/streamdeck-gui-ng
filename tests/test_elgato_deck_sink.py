"""The hardware sink: converting what plugins send into what a deck accepts."""

from io import BytesIO

from PIL import Image

from streamdeck_ui.elgato_plugin.deck_sink import DeckSink


class FakeDeck:
    def __init__(self, open: bool = True):
        self._open = open
        self.images: list[tuple[int, bytes]] = []

    def is_open(self) -> bool:
        return self._open

    def key_image_format(self):
        return {"size": (72, 72), "format": "JPEG", "flip": [1, 1], "rotation": 0}

    def set_key_image(self, key, image):
        self.images.append((key, image))


def png_bytes(size=(72, 72), color="red") -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_a_plugin_image_lands_on_the_deck_in_native_format():
    deck = FakeDeck()
    sink = DeckSink({"serial-1": deck})
    sink.set_key_image("serial-1", 4, png_bytes())

    assert len(deck.images) == 1
    key, payload = deck.images[0]
    assert key == 4
    assert payload[:2] == b"\xff\xd8", "the deck wants JPEG for this model"


def test_a_clear_asks_the_app_to_paint_its_own_picture_back():
    deck = FakeDeck()
    repainted = []
    sink = DeckSink({"serial-1": deck}, repaint=lambda device_id, key: repainted.append((device_id, key)))
    sink.set_key_image("serial-1", 2, None)

    assert deck.images == []
    assert repainted == [("serial-1", 2)]


def test_titles_are_remembered_and_the_key_repaints():
    deck = FakeDeck()
    repainted = []
    sink = DeckSink({"s": deck}, repaint=lambda d, k: repainted.append((d, k)))
    sink.set_key_title("s", 1, "PLAY")

    assert sink.titles[("s", 1)] == "PLAY"
    assert repainted == [("s", 1)]


def test_a_device_we_do_not_know_about_is_not_fatal():
    sink = DeckSink({})
    sink.set_key_image("ghost", 0, png_bytes())
    sink.set_key_title("ghost", 0, "x")


def test_a_closed_device_is_left_alone():
    deck = FakeDeck(open=False)
    sink = DeckSink({"s": deck})
    sink.set_key_image("s", 0, png_bytes())
    assert deck.images == []


def test_rubbish_image_data_does_not_take_the_host_down():
    deck = FakeDeck()
    sink = DeckSink({"s": deck})
    sink.set_key_image("s", 0, b"this is not an image")
    assert deck.images == []


def test_a_transparent_plugin_image_flattens_on_black():
    deck = FakeDeck()
    sink = DeckSink({"s": deck})
    transparent = BytesIO()
    image = Image.new("RGBA", (72, 72), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (36, 36), (255, 0, 0, 255)), (0, 0))
    image.save(transparent, format="PNG")

    sink.set_key_image("s", 0, transparent.getvalue())

    assert deck.images
    restored = Image.open(BytesIO(deck.images[0][1]))
    assert restored.size == (72, 72)
    raw = restored.convert("RGB").tobytes()
    channels = (raw[i : i + 3] for i in range(0, len(raw), 3))
    assert any(red > 200 for red, _, _ in channels), "the opaque square survived"
    assert any(max(pixel) < 40 for pixel in channels), "the transparent area became black"


def test_an_image_of_the_wrong_size_is_scaled_not_refused():
    deck = FakeDeck()
    sink = DeckSink({"s": deck})
    sink.set_key_image("s", 0, png_bytes(size=(200, 200)))
    assert Image.open(BytesIO(deck.images[0][1])).size == (72, 72)
