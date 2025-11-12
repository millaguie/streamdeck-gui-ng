"""Base plugin class for streamdeck-ui plugins."""

import logging
import select
import socket
import sys
from abc import ABC, abstractmethod
from io import BytesIO
from typing import Any, Dict, Optional

from PIL import Image

from .protocol import (
    LogLevel,
    MessageType,
    ProtocolMessage,
    create_error_message,
    create_heartbeat_message,
    create_log_message,
    create_ready_message,
    create_request_page_switch_message,
    create_update_image_raw_message,
    create_update_image_render_message,
)


class BasePlugin(ABC):
    """Base class for streamdeck-ui plugins.

    Plugin developers should inherit from this class and implement
    the abstract methods.
    """

    def __init__(self, socket_path: str, config: Dict[str, Any]):
        """Initialize plugin.

        Args:
            socket_path: Path to Unix socket for communication with host
            config: Plugin configuration dictionary
        """
        self.socket_path = socket_path
        self.config = config
        self.socket: Optional[socket.socket] = None
        self.running = False
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """Set up logger for plugin."""
        logger = logging.getLogger(self.__class__.__name__)
        logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        logger.addHandler(handler)
        return logger

    def connect(self) -> None:
        """Connect to the host via Unix socket."""
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.connect(self.socket_path)
        self.running = True
        self.logger.info(f"Connected to host at {self.socket_path}")

    def disconnect(self) -> None:
        """Disconnect from the host."""
        if self.socket:
            self.socket.close()
            self.socket = None
        self.running = False
        self.logger.info("Disconnected from host")

    def send_message(self, message: ProtocolMessage) -> None:
        """Send a message to the host.

        Args:
            message: Protocol message to send
        """
        if not self.socket:
            raise RuntimeError("Not connected to host")

        data = message.to_bytes()
        self.socket.sendall(data)

    def receive_message(self, timeout: Optional[float] = None) -> Optional[ProtocolMessage]:
        """Receive a message from the host.

        Args:
            timeout: Timeout in seconds (None = blocking)

        Returns:
            Received message or None if timeout
        """
        if not self.socket:
            raise RuntimeError("Not connected to host")

        # Use select to check if data is available
        if timeout is not None:
            ready = select.select([self.socket], [], [], timeout)
            if not ready[0]:
                return None

        # Read length prefix (4 bytes)
        length_data = self._recv_exact(4)
        if not length_data:
            return None

        length = int.from_bytes(length_data, byteorder="big")

        # Read message data
        message_data = self._recv_exact(length)
        if not message_data:
            return None

        return ProtocolMessage.from_bytes(message_data)

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Receive exactly n bytes from socket."""
        if self.socket is None:
            return None
        data = b""
        while len(data) < n:
            chunk = self.socket.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    # Host communication methods

    def update_image_raw(self, image: Image.Image, format: str = "PNG") -> None:
        """Update button image with raw image data.

        Args:
            image: PIL Image to display
            format: Image format (PNG, JPEG, etc.)
        """
        buffer = BytesIO()
        image.save(buffer, format=format)
        image_data = buffer.getvalue()

        message = create_update_image_raw_message(image_data, format)
        self.send_message(message)

    def update_image_render(
        self,
        text: Optional[str] = None,
        icon: Optional[str] = None,
        background_color: Optional[str] = None,
        font_color: Optional[str] = None,
        font_size: Optional[int] = None,
        text_vertical_align: Optional[str] = None,
        text_horizontal_align: Optional[str] = None,
    ) -> None:
        """Update button image with rendering instructions.

        Args:
            text: Text to display
            icon: Path to icon image
            background_color: Background color (hex)
            font_color: Font color (hex)
            font_size: Font size
            text_vertical_align: Vertical alignment (top, middle, bottom, etc.)
            text_horizontal_align: Horizontal alignment (left, center, right)
        """
        message = create_update_image_render_message(
            text=text,
            icon=icon,
            background_color=background_color,
            font_color=font_color,
            font_size=font_size,
            text_vertical_align=text_vertical_align,
            text_horizontal_align=text_horizontal_align,
        )
        self.send_message(message)

    def request_page_switch(self, duration: Optional[int] = None) -> None:
        """Request to switch to the page containing this button.

        Args:
            duration: Duration in seconds to show the page (None = permanent)
        """
        message = create_request_page_switch_message(duration)
        self.send_message(message)

    def log(self, level: LogLevel, message: str) -> None:
        """Send log message to host.

        Args:
            level: Log level
            message: Log message
        """
        msg = create_log_message(level, message)
        self.send_message(msg)

    def send_heartbeat(self) -> None:
        """Send heartbeat to host."""
        message = create_heartbeat_message()
        self.send_message(message)

    def send_ready(self) -> None:
        """Notify host that plugin is ready."""
        message = create_ready_message()
        self.send_message(message)

    def send_error(self, error: str, details: Optional[str] = None) -> None:
        """Send error message to host.

        Args:
            error: Error message
            details: Optional error details
        """
        message = create_error_message(error, details)
        self.send_message(message)

    # Message handlers (called by run_loop)

    def _handle_message(self, message: ProtocolMessage) -> None:
        """Dispatch message to appropriate handler."""
        try:
            if message.type == MessageType.BUTTON_PRESSED:
                self.on_button_pressed()
            elif message.type == MessageType.BUTTON_RELEASED:
                self.on_button_released()
            elif message.type == MessageType.BUTTON_VISIBLE:
                page = message.payload.get("page", 0)
                button = message.payload.get("button", 0)
                self.on_button_visible(int(page), int(button))
            elif message.type == MessageType.BUTTON_HIDDEN:
                self.on_button_hidden()
            elif message.type == MessageType.CONFIG_UPDATE:
                config = message.payload.get("config", {})
                self.config = config
                self.on_config_update(config)
            elif message.type == MessageType.SHUTDOWN:
                self.logger.info("Received shutdown request")
                self.running = False
                self.on_shutdown()
            elif message.type == MessageType.ERROR:
                error = message.payload.get("error", "")
                details = message.payload.get("details")
                self.on_error(str(error), details)
            else:
                self.logger.warning(f"Unknown message type: {message.type}")
        except Exception as e:
            self.logger.error(f"Error handling message {message.type}: {e}", exc_info=True)
            self.send_error(str(e), f"Exception in handler: {type(e).__name__}")

    # Abstract methods to be implemented by plugins

    @abstractmethod
    def on_start(self) -> None:
        """Called when plugin starts.

        Plugins should perform initialization here.
        """
        pass

    @abstractmethod
    def on_button_pressed(self) -> None:
        """Called when the button is pressed."""
        pass

    @abstractmethod
    def on_button_released(self) -> None:
        """Called when the button is released."""
        pass

    @abstractmethod
    def on_button_visible(self, page: int, button: int) -> None:
        """Called when button becomes visible.

        Args:
            page: Page number
            button: Button number
        """
        pass

    @abstractmethod
    def on_button_hidden(self) -> None:
        """Called when button is no longer visible."""
        pass

    def on_config_update(self, config: Dict[str, Any]) -> None:  # noqa: B027
        """Called when configuration is updated.

        Default implementation does nothing. Override if needed.

        Args:
            config: New configuration
        """
        pass

    def on_shutdown(self) -> None:  # noqa: B027
        """Called when plugin should shut down.

        Default implementation does nothing. Override for cleanup.
        """
        pass

    def on_error(self, error: str, details: Optional[str] = None) -> None:
        """Called when an error message is received from host.

        Default implementation logs the error. Override if needed.

        Args:
            error: Error message
            details: Optional error details
        """
        self.logger.error(f"Host error: {error}")
        if details:
            self.logger.error(f"Details: {details}")

    @abstractmethod
    def update(self) -> None:
        """Called periodically in the main loop.

        Plugins should perform periodic tasks here (e.g., polling APIs,
        updating display, etc.). This is called approximately once per second.
        """
        pass

    # Main run loop

    def run(self) -> None:
        """Main plugin run loop."""
        try:
            self.connect()
            self.send_ready()
            self.on_start()

            last_heartbeat: float = 0.0
            import time

            while self.running:
                # Check for incoming messages (non-blocking)
                message = self.receive_message(timeout=0.1)
                if message:
                    self._handle_message(message)

                # Call update periodically
                self.update()

                # Send heartbeat every 5 seconds
                current_time = time.time()
                if current_time - last_heartbeat > 5:
                    self.send_heartbeat()
                    last_heartbeat = current_time

                # Small sleep to avoid busy-waiting
                time.sleep(0.1)

        except KeyboardInterrupt:
            self.logger.info("Received keyboard interrupt")
        except Exception as e:
            self.logger.error(f"Plugin error: {e}", exc_info=True)
            self.send_error(str(e))
        finally:
            self.disconnect()
