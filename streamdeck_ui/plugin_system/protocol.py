"""Plugin communication protocol via Unix sockets."""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class MessageType(Enum):
    """Message types for plugin-host communication."""

    # Host -> Plugin messages
    BUTTON_PRESSED = "button_pressed"
    BUTTON_RELEASED = "button_released"
    BUTTON_VISIBLE = "button_visible"  # Button is now on current page
    BUTTON_HIDDEN = "button_hidden"  # Button is no longer visible
    CONFIG_UPDATE = "config_update"  # Configuration has been updated
    SHUTDOWN = "shutdown"  # Plugin should gracefully terminate

    # Plugin -> Host messages
    UPDATE_IMAGE_RAW = "update_image_raw"  # Send raw image data
    UPDATE_IMAGE_RENDER = "update_image_render"  # Send rendering instructions
    REQUEST_PAGE_SWITCH = "request_page_switch"  # Request to switch to button's page
    LOG_MESSAGE = "log_message"  # Send log message to host
    HEARTBEAT = "heartbeat"  # Periodic heartbeat
    READY = "ready"  # Plugin is ready to receive commands

    # Bidirectional
    ERROR = "error"  # Error occurred
    ACK = "ack"  # Acknowledge message


class LogLevel(Enum):
    """Log levels for plugin logging."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class ProtocolMessage:
    """Base protocol message structure."""

    type: MessageType
    payload: Dict[str, Any]
    message_id: Optional[str] = None  # For tracking request/response

    def to_json(self) -> str:
        """Serialize message to JSON."""
        return json.dumps(
            {
                "type": self.type.value,
                "payload": self.payload,
                "message_id": self.message_id,
            }
        )

    @classmethod
    def from_json(cls, json_str: str) -> "ProtocolMessage":
        """Deserialize message from JSON."""
        data = json.loads(json_str)
        return cls(
            type=MessageType(data["type"]),
            payload=data["payload"],
            message_id=data.get("message_id"),
        )

    def to_bytes(self) -> bytes:
        """Convert to bytes for socket transmission."""
        json_str = self.to_json()
        # Length-prefixed message: 4 bytes for length + JSON data
        length = len(json_str.encode("utf-8"))
        return length.to_bytes(4, byteorder="big") + json_str.encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> "ProtocolMessage":
        """Parse from bytes."""
        return cls.from_json(data.decode("utf-8"))


# Convenience functions for creating common messages


def create_button_pressed_message() -> ProtocolMessage:
    """Create button pressed message."""
    return ProtocolMessage(
        type=MessageType.BUTTON_PRESSED,
        payload={},
    )


def create_button_released_message() -> ProtocolMessage:
    """Create button released message."""
    return ProtocolMessage(
        type=MessageType.BUTTON_RELEASED,
        payload={},
    )


def create_button_visible_message(page: int, button: int) -> ProtocolMessage:
    """Create button visible message."""
    return ProtocolMessage(
        type=MessageType.BUTTON_VISIBLE,
        payload={"page": page, "button": button},
    )


def create_button_hidden_message() -> ProtocolMessage:
    """Create button hidden message."""
    return ProtocolMessage(
        type=MessageType.BUTTON_HIDDEN,
        payload={},
    )


def create_config_update_message(config: Dict[str, Any]) -> ProtocolMessage:
    """Create config update message."""
    return ProtocolMessage(
        type=MessageType.CONFIG_UPDATE,
        payload={"config": config},
    )


def create_shutdown_message() -> ProtocolMessage:
    """Create shutdown message."""
    return ProtocolMessage(
        type=MessageType.SHUTDOWN,
        payload={},
    )


def create_update_image_raw_message(image_data: bytes, format: str = "PNG") -> ProtocolMessage:
    """Create update image raw message.

    Args:
        image_data: Base64 encoded image data
        format: Image format (PNG, JPEG, etc.)
    """
    import base64

    return ProtocolMessage(
        type=MessageType.UPDATE_IMAGE_RAW,
        payload={
            "image_data": base64.b64encode(image_data).decode("utf-8"),
            "format": format,
        },
    )


def create_update_image_render_message(
    text: Optional[str] = None,
    icon: Optional[str] = None,
    background_color: Optional[str] = None,
    font_color: Optional[str] = None,
    font_size: Optional[int] = None,
    text_vertical_align: Optional[str] = None,
    text_horizontal_align: Optional[str] = None,
) -> ProtocolMessage:
    """Create update image render message with rendering instructions."""
    payload = {}
    if text is not None:
        payload["text"] = text
    if icon is not None:
        payload["icon"] = icon
    if background_color is not None:
        payload["background_color"] = background_color
    if font_color is not None:
        payload["font_color"] = font_color
    if font_size is not None:
        payload["font_size"] = str(font_size)
    if text_vertical_align is not None:
        payload["text_vertical_align"] = text_vertical_align
    if text_horizontal_align is not None:
        payload["text_horizontal_align"] = text_horizontal_align

    return ProtocolMessage(
        type=MessageType.UPDATE_IMAGE_RENDER,
        payload=payload,
    )


def create_request_page_switch_message(duration: Optional[int] = None) -> ProtocolMessage:
    """Create request page switch message.

    Args:
        duration: Duration in seconds to show the page (None = permanent)
    """
    return ProtocolMessage(
        type=MessageType.REQUEST_PAGE_SWITCH,
        payload={"duration": duration} if duration is not None else {},
    )


def create_log_message(level: LogLevel, message: str) -> ProtocolMessage:
    """Create log message."""
    return ProtocolMessage(
        type=MessageType.LOG_MESSAGE,
        payload={"level": level.value, "message": message},
    )


def create_heartbeat_message() -> ProtocolMessage:
    """Create heartbeat message."""
    return ProtocolMessage(
        type=MessageType.HEARTBEAT,
        payload={},
    )


def create_ready_message() -> ProtocolMessage:
    """Create ready message."""
    return ProtocolMessage(
        type=MessageType.READY,
        payload={},
    )


def create_error_message(error: str, details: Optional[str] = None) -> ProtocolMessage:
    """Create error message."""
    payload = {"error": error}
    if details:
        payload["details"] = details
    return ProtocolMessage(
        type=MessageType.ERROR,
        payload=payload,
    )


def create_ack_message(message_id: Optional[str] = None) -> ProtocolMessage:
    """Create acknowledgment message."""
    return ProtocolMessage(
        type=MessageType.ACK,
        payload={},
        message_id=message_id,
    )
