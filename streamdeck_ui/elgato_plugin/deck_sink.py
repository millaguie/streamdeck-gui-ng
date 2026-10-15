"""Applies what plugins ask for to real hardware.

The runtime never touches a deck itself; this is the sink used when a deck is attached.
Plugins send images as PNG/JPEG bytes already decoded from their data URL, so we only have
to convert them to whatever the deck wants and hand them over.
"""

from io import BytesIO
import logging
import webbrowser
from typing import Callable, Optional

from PIL import Image
from StreamDeck.Devices.StreamDeck import StreamDeck
from StreamDeck.ImageHelpers import PILHelper

logger = logging.getLogger(__name__)


class DeckSink:
    """Writes plugin output to open StreamDeck devices.

    ``repaint`` exists because clearing a key is not "paint it black": when a plugin lets
    go of a key the app should get its own picture back, and only the app knows what that is.
    """

    def __init__(
        self,
        decks: dict[str, StreamDeck],
        repaint: Optional[Callable[[str, int], None]] = None,
    ):
        self.decks = decks
        self.repaint = repaint
        self.titles: dict[tuple[str, int], str] = {}

    def set_key_image(self, device_id: str, key: int, image: Optional[bytes]) -> None:
        if image is None:
            self._repaint(device_id, key)
            return
        deck = self._deck(device_id)
        if deck is None:
            return
        try:
            picture = _prepare(Image.open(BytesIO(image)), deck.key_image_format())
            deck.set_key_image(key, PILHelper.to_native_format(deck, picture))
        except Exception as error:
            logger.warning("Cannot put the plugin image on %s key %s: %s", device_id, key, error)

    def set_key_title(self, device_id: str, key: int, title: Optional[str]) -> None:
        self.titles[(device_id, key)] = title or ""
        self._repaint(device_id, key)

    def show_ok(self, device_id: str, key: int) -> None:
        logger.info("Plugin confirmed %s key %s", device_id, key)

    def show_alert(self, device_id: str, key: int) -> None:
        logger.warning("Plugin alerted on %s key %s", device_id, key)

    def open_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception as error:
            logger.warning("Could not open %s: %s", url, error)

    def _deck(self, device_id: str) -> Optional[StreamDeck]:
        deck = self.decks.get(device_id)
        if deck is None:
            logger.warning("Plugin drew on unknown device %s", device_id)
            return None
        if not deck.is_open():
            logger.warning("Plugin drew on closed device %s", device_id)
            return None
        return deck

    def _repaint(self, device_id: str, key: int) -> None:
        if self.repaint is not None:
            self.repaint(device_id, key)


def _prepare(image: Image.Image, key_format: dict) -> Image.Image:
    """Makes a plugin image something the deck can actually be sent.

    Plugins send PNG with transparency; the decks want JPEG, which has no alpha, so we flatten
    onto black (a black key is just a key that is not lit). If the picture is not the size the
    deck wants, we scale it instead of failing.
    """
    target_format = (key_format.get("format") or "").upper()
    width, height = key_format.get("size", image.size)

    if target_format in ("JPEG", "JPG") and image.mode != "RGB":
        background = Image.new("RGB", image.size, (0, 0, 0))
        if "A" in image.getbands():
            background.paste(image, mask=image.getchannel("A"))
        else:
            background.paste(image.convert("RGB"))
        image = background

    if image.size != (width, height):
        image = image.resize((width, height))

    return image
