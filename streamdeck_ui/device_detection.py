from dataclasses import dataclass
from importlib import import_module
import logging
import os
import re
from typing import Optional

from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Devices.StreamDeck import StreamDeck
from StreamDeck.ProductIDs import USBProductIDs

ELGATO_VENDOR_ID = 0x0FD9

logger = logging.getLogger(__name__)


def _sku(name: str) -> Optional[type[StreamDeck]]:
    """The class of one SKU, or None when the installed library is too old to know it.

    The dependency this project declares reaches back to streamdeck 0.9.5, which ships
    neither Studio nor Plus XL. A library that cannot model a device is a reason to say so,
    not a reason to refuse to start.
    """
    try:
        module = import_module(f"StreamDeck.Devices.{name}")
    except ImportError:
        logger.info("The installed streamdeck library has no %s, that SKU cannot be used", name)
        return None
    return getattr(module, name, None)


StreamDeckOriginal = _sku("StreamDeckOriginal")
StreamDeckOriginalV2 = _sku("StreamDeckOriginalV2")
StreamDeckMini = _sku("StreamDeckMini")
StreamDeckXL = _sku("StreamDeckXL")
StreamDeckNeo = _sku("StreamDeckNeo")
StreamDeckPedal = _sku("StreamDeckPedal")
StreamDeckPlus = _sku("StreamDeckPlus")
StreamDeckPlusXL = _sku("StreamDeckPlusXL")
StreamDeckStudio = _sku("StreamDeckStudio")


def _pid(name: str) -> Optional[int]:
    """One product id, or None when the installed library does not define that SKU yet."""
    value = getattr(USBProductIDs, name, None)
    if value is None:
        logger.info("The installed streamdeck library does not define %s", name)
    return value


def _known_product_ids(
    entries: list[tuple[Optional[int], Optional[type[StreamDeck]]]],
) -> dict[int, Optional[type[StreamDeck]]]:
    return {pid: sku for pid, sku in entries if pid is not None}


MODEL_BY_PRODUCT_ID: dict[int, Optional[type[StreamDeck]]] = _known_product_ids(
    [
        (_pid("USB_PID_STREAMDECK_ORIGINAL"), StreamDeckOriginal),
        (_pid("USB_PID_STREAMDECK_ORIGINAL_V2"), StreamDeckOriginalV2),
        (_pid("USB_PID_STREAMDECK_MK2"), StreamDeckOriginalV2),
        (_pid("USB_PID_STREAMDECK_MK2_MODULE"), StreamDeckOriginalV2),
        (_pid("USB_PID_STREAMDECK_MK2_SCISSOR"), StreamDeckOriginalV2),
        (_pid("USB_PID_STREAMDECK_MINI"), StreamDeckMini),
        (_pid("USB_PID_STREAMDECK_MINI_DISCORD"), StreamDeckMini),
        (_pid("USB_PID_STREAMDECK_MINI_MK2"), StreamDeckMini),
        (_pid("USB_PID_STREAMDECK_MINI_MK2_MODULE"), StreamDeckMini),
        (_pid("USB_PID_STREAMDECK_XL"), StreamDeckXL),
        (_pid("USB_PID_STREAMDECK_XL_V2"), StreamDeckXL),
        (_pid("USB_PID_STREAMDECK_XL_V2_MODULE"), StreamDeckXL),
        (_pid("USB_PID_STREAMDECK_NEO"), StreamDeckNeo),
        (_pid("USB_PID_STREAMDECK_PEDAL"), StreamDeckPedal),
        (_pid("USB_PID_STREAMDECK_PLUS"), StreamDeckPlus),
        (_pid("USB_PID_STREAMDECK_PLUS_XL"), StreamDeckPlusXL),
        (_pid("USB_PID_STREAMDECK_STUDIO"), StreamDeckStudio),
    ]
)

# Product ids that real hardware reports but that older streamdeck libraries do
# not name as a constant. Literals on purpose: they describe the device, not the
# dependency, so detection keeps working on the oldest supported library. The
# mapped SKU is whatever the dependency can model, or None for "known but not
# supported here", which keeps such devices listed as unsupported instead of
# vanishing from the table.
for _literal_pid, _literal_model in [
    (0x00B3, StreamDeckMini),  # Stream Deck Mini MK2, renamed PID
    (0x00C6, StreamDeckPlusXL),  # Stream Deck + XL, from streamdeck 0.10.0
    (0x00AA, StreamDeckStudio),  # Stream Deck Studio, from streamdeck 0.10.0
]:
    MODEL_BY_PRODUCT_ID.setdefault(_literal_pid, _literal_model)

MODEL_BY_PRODUCT_STRING: list[tuple[str, Optional[type[StreamDeck]]]] = [
    ("streamdeckplusxl", StreamDeckPlusXL),
    ("streamdeckplustouchbar", StreamDeckPlus),
    ("streamdeckpluslever", StreamDeckPlus),
    ("streamdeckplus", StreamDeckPlus),
    ("streamdeckxl", StreamDeckXL),
    ("streamdeckstudio", StreamDeckStudio),
    ("streamdeckneo", StreamDeckNeo),
    ("streamdeckpedal", StreamDeckPedal),
    ("streamdeckmini", StreamDeckMini),
    ("streamdeckmk2", StreamDeckOriginalV2),
    ("streamdeckoriginalv2", StreamDeckOriginalV2),
]

EXACT_MODEL_BY_PRODUCT_STRING: dict[str, Optional[type[StreamDeck]]] = {
    "streamdeck": StreamDeckOriginal,
    "streamdeckoriginal": StreamDeckOriginal,
    "streamdeckoriginalv1": StreamDeckOriginal,
    "streamdeck2019": StreamDeckOriginal,
}


def normalize_product_string(product_string: Optional[str]) -> str:
    if not product_string:
        return ""
    replaced = product_string.lower().replace("+", "plus")
    return re.sub(r"[^a-z0-9]", "", replaced)


def model_for_product_string(product_string: Optional[str]) -> Optional[type[StreamDeck]]:
    """Longest product string wins, so 'Stream Deck + XL' is not taken for 'Stream Deck +'."""
    normalized = normalize_product_string(product_string)
    if not normalized:
        return None
    for needle, model in sorted(MODEL_BY_PRODUCT_STRING, key=lambda entry: len(entry[0]), reverse=True):
        if needle in normalized:
            return model
    return EXACT_MODEL_BY_PRODUCT_STRING.get(normalized)


def _bus_and_address(path: str) -> Optional[tuple[int, int]]:
    groups = re.findall(r"\b[0-9a-fA-F]{4}\b", path)
    if len(groups) >= 2:
        return int(groups[0], 16), int(groups[1], 16)
    return None


def _topology_slot(path: str) -> Optional[str]:
    """Hidapi hands over USB core style paths such as '7-1.3.2.1:1.0'."""
    head = path.split(":", 1)[0]
    if re.fullmatch(r"\d+(-\d+)+(\.\d+)*", head):
        return head
    return None


def physical_port(path: Optional[str], sysfs_root: str = "/sys/bus/usb/devices") -> Optional[str]:
    """Returns the USB topology slot (e.g. 3-1.4) the device sits in, stable across replugs."""
    if not path:
        return None

    slot = _topology_slot(path)
    if slot:
        return slot

    candidates: list[str] = []

    if path.startswith("/dev/hidraw"):
        candidates.append(os.path.join("/sys/class/hidraw", os.path.basename(path), "device", "device"))

    usb_identity = _bus_and_address(path)
    if usb_identity and os.path.isdir(sysfs_root):
        bus, address = usb_identity
        for entry in sorted(os.listdir(sysfs_root)):
            entry_path = os.path.join(sysfs_root, entry)
            try:
                with open(os.path.join(entry_path, "busnum")) as handle:
                    sysfs_bus = int(handle.read().strip())
                with open(os.path.join(entry_path, "devnum")) as handle:
                    sysfs_address = int(handle.read().strip())
            except (OSError, ValueError):
                continue
            if (sysfs_bus, sysfs_address) == (bus, address):
                candidates.append(entry_path)

    for candidate in candidates:
        resolved = os.path.realpath(candidate)
        name = os.path.basename(resolved)
        if name and name not in (".", ".."):
            return name
    return None


@dataclass
class DetectedDevice:
    deck: Optional[StreamDeck]
    product_id: int
    product_string: str
    path: str
    port: Optional[str]
    serial: str
    detected_by: str

    @property
    def supported(self) -> bool:
        return self.deck is not None

    @property
    def stable_key(self) -> str:
        if self.serial:
            return self.serial
        if self.port:
            return f"port:{self.port}"
        return f"path:{self.path}"


def detect(transport, sysfs_root: str = "/sys/bus/usb/devices") -> list[DetectedDevice]:
    """Enumerates every Elgato device, including SKUs the streamdeck library does not know yet."""
    detected: list[DetectedDevice] = []

    for transport_device in transport.enumerate(vid=ELGATO_VENDOR_ID, pid=None):
        info = getattr(transport_device, "device_info", {}) or {}
        product_id = info.get("product_id", transport_device.product_id())
        product_string = info.get("product_string") or ""
        path = transport_device.path()
        serial = (info.get("serial_number") or "").strip()
        port = physical_port(path, sysfs_root)

        model = MODEL_BY_PRODUCT_ID.get(product_id)
        detected_by = "product-id"
        if model is None:
            model = model_for_product_string(product_string)
            detected_by = "product-string"

        if model is None:
            detected.append(DetectedDevice(None, product_id, product_string, path, port, serial, "unsupported"))
            continue

        deck = model(transport_device)

        detected.append(DetectedDevice(deck, product_id, product_string, path, port, serial, detected_by))

    return detected


def enumerate_streamdecks() -> list[StreamDeck]:
    """Drop-in for DeviceManager().enumerate(), additionally resolving unknown SKUs."""
    detected = detect(DeviceManager().transport)

    for unknown in (item for item in detected if not item.supported):
        logger.warning(
            "Unsupported Elgato device skipped: product 0x%04x '%s' at %s",
            unknown.product_id,
            unknown.product_string,
            unknown.path,
        )

    return [item.deck for item in detected if item.deck is not None]
