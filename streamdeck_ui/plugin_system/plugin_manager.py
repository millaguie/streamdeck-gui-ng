"""Plugin manager for managing plugin lifecycle and communication."""

import base64
import logging
import os
import socket
import subprocess
import tempfile
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from PIL import Image

from .protocol import MessageType, ProtocolMessage
from .schema import LifecycleMode, PluginManifest

logger = logging.getLogger(__name__)


class PluginInstance:
    """Represents a running instance of a plugin for a specific button."""

    def __init__(
        self,
        plugin_id: str,
        instance_id: str,
        manifest: PluginManifest,
        plugin_dir: Path,
        config: Dict[str, Any],
        deck_serial: str,
        page: int,
        button: int,
        can_switch_page: bool,
    ):
        """Initialize plugin instance.

        Args:
            plugin_id: Unique plugin identifier (from directory name)
            instance_id: Unique instance identifier
            manifest: Plugin manifest
            plugin_dir: Path to plugin directory
            config: Plugin configuration
            deck_serial: Stream Deck serial number
            page: Page number
            button: Button number
            can_switch_page: Whether this instance can switch pages
        """
        self.plugin_id = plugin_id
        self.instance_id = instance_id
        self.manifest = manifest
        self.plugin_dir = plugin_dir
        self.config = config
        self.deck_serial = deck_serial
        self.page = page
        self.button = button
        self.can_switch_page = can_switch_page

        self.process: Optional[subprocess.Popen] = None
        self.socket_path: Optional[str] = None
        self.socket: Optional[socket.socket] = None
        self.client_socket: Optional[socket.socket] = None
        self.running = False
        self.retry_count = 0
        self.last_heartbeat: float = 0.0

        # Callbacks
        self.on_image_update: Optional[Callable[[str, int, int, Any], None]] = None
        self.on_page_switch_request: Optional[Callable[[str, int, int, Optional[int]], None]] = None
        self.on_log_message: Optional[Callable[[str, str], None]] = None

        # Communication thread
        self.comm_thread: Optional[threading.Thread] = None
        self.comm_lock = threading.Lock()

    def start(self) -> bool:
        """Start the plugin process.

        Returns:
            True if started successfully
        """
        try:
            # Create Unix socket
            self.socket_path = self._create_socket()

            # Start plugin process
            entry_point = self.plugin_dir / self.manifest.entry_point
            env = os.environ.copy()
            env["STREAMDECK_PLUGIN_SOCKET"] = self.socket_path

            import json

            config_json = json.dumps(self.config)
            env["STREAMDECK_PLUGIN_CONFIG"] = config_json

            self.process = subprocess.Popen(
                ["python3", str(entry_point), self.socket_path, config_json],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            logger.info(f"Started plugin {self.plugin_id} instance {self.instance_id} " f"(PID: {self.process.pid})")

            # Wait for plugin to connect (with timeout)
            if self.socket is None:
                logger.error(f"Socket is None for plugin {self.instance_id}")
                return False

            self.socket.settimeout(10.0)
            try:
                self.client_socket, _ = self.socket.accept()
                self.client_socket.settimeout(None)  # Set back to blocking
                logger.info(f"Plugin {self.instance_id} connected")
            except socket.timeout:
                logger.error(f"Plugin {self.instance_id} failed to connect within timeout")
                self.stop()
                return False

            self.running = True
            self.last_heartbeat = time.time()

            # Start communication thread
            self.comm_thread = threading.Thread(target=self._communication_loop, daemon=True)
            self.comm_thread.start()

            return True

        except Exception as e:
            logger.error(f"Failed to start plugin {self.instance_id}: {e}", exc_info=True)
            self.stop()
            return False

    def stop(self) -> None:
        """Stop the plugin process."""
        self.running = False

        # Send shutdown message
        if self.client_socket:
            try:
                self._send_message(ProtocolMessage(type=MessageType.SHUTDOWN, payload={}))
            except Exception:
                pass

        # Wait for process to exit
        if self.process:
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                logger.warning(f"Plugin {self.instance_id} did not exit gracefully, killing")
                self.process.kill()
                self.process.wait()

        # Close sockets
        if self.client_socket:
            self.client_socket.close()
            self.client_socket = None

        if self.socket:
            self.socket.close()
            self.socket = None

        # Remove socket file
        if self.socket_path and os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        logger.info(f"Stopped plugin {self.instance_id}")

    def send_button_pressed(self) -> None:
        """Notify plugin that button was pressed."""
        self._send_message(ProtocolMessage(type=MessageType.BUTTON_PRESSED, payload={}))

    def send_button_released(self) -> None:
        """Notify plugin that button was released."""
        self._send_message(ProtocolMessage(type=MessageType.BUTTON_RELEASED, payload={}))

    def send_button_visible(self) -> None:
        """Notify plugin that button is now visible."""
        self._send_message(
            ProtocolMessage(
                type=MessageType.BUTTON_VISIBLE,
                payload={"page": self.page, "button": self.button},
            )
        )

    def send_button_hidden(self) -> None:
        """Notify plugin that button is now hidden."""
        self._send_message(ProtocolMessage(type=MessageType.BUTTON_HIDDEN, payload={}))

    def send_config_update(self, config: Dict[str, Any]) -> None:
        """Send updated configuration to plugin."""
        self.config = config
        self._send_message(
            ProtocolMessage(
                type=MessageType.CONFIG_UPDATE,
                payload={"config": config},
            )
        )

    def is_alive(self) -> bool:
        """Check if plugin process is alive."""
        if not self.process:
            return False
        return self.process.poll() is None

    def is_responsive(self) -> bool:
        """Check if plugin is responsive (recent heartbeat)."""
        return (time.time() - self.last_heartbeat) < 30  # 30 second timeout

    def _create_socket(self) -> str:
        """Create Unix socket for communication."""
        # Create temporary socket file
        fd, socket_path = tempfile.mkstemp(prefix=f"streamdeck_plugin_{self.instance_id}_")
        os.close(fd)
        os.unlink(socket_path)

        # Create Unix socket
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(socket_path)
        self.socket.listen(1)

        return socket_path

    def _send_message(self, message: ProtocolMessage) -> None:
        """Send message to plugin."""
        if not self.client_socket:
            return

        try:
            with self.comm_lock:
                data = message.to_bytes()
                self.client_socket.sendall(data)
        except Exception as e:
            logger.error(f"Failed to send message to plugin {self.instance_id}: {e}")

    def _receive_message(self) -> Optional[ProtocolMessage]:
        """Receive message from plugin."""
        if not self.client_socket:
            return None

        try:
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

        except Exception as e:
            logger.error(f"Failed to receive message from plugin {self.instance_id}: {e}")
            return None

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Receive exactly n bytes from socket."""
        if self.client_socket is None:
            return None
        data = b""
        while len(data) < n:
            chunk = self.client_socket.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def _communication_loop(self) -> None:
        """Communication loop running in background thread."""
        logger.info(f"Communication loop started for plugin {self.instance_id}")

        while self.running:
            try:
                message = self._receive_message()
                if not message:
                    time.sleep(0.1)
                    continue

                self._handle_message(message)

            except Exception as e:
                logger.error(
                    f"Error in communication loop for plugin {self.instance_id}: {e}",
                    exc_info=True,
                )
                break

        logger.info(f"Communication loop stopped for plugin {self.instance_id}")

    def _handle_message(self, message: ProtocolMessage) -> None:
        """Handle message from plugin."""
        try:
            if message.type == MessageType.UPDATE_IMAGE_RAW:
                self._handle_update_image_raw(message)
            elif message.type == MessageType.UPDATE_IMAGE_RENDER:
                self._handle_update_image_render(message)
            elif message.type == MessageType.REQUEST_PAGE_SWITCH:
                self._handle_page_switch_request(message)
            elif message.type == MessageType.LOG_MESSAGE:
                self._handle_log_message(message)
            elif message.type == MessageType.HEARTBEAT:
                self.last_heartbeat = time.time()
            elif message.type == MessageType.READY:
                logger.info(f"Plugin {self.instance_id} is ready")
            elif message.type == MessageType.ERROR:
                error = message.payload.get("error")
                details = message.payload.get("details")
                logger.error(f"Plugin {self.instance_id} error: {error}")
                if details:
                    logger.error(f"Details: {details}")
            else:
                logger.warning(f"Unknown message type from plugin {self.instance_id}: {message.type}")

        except Exception as e:
            logger.error(f"Error handling message from plugin {self.instance_id}: {e}")

    def _handle_update_image_raw(self, message: ProtocolMessage) -> None:
        """Handle raw image update from plugin."""
        if not self.on_image_update:
            return

        try:
            image_data_b64 = message.payload.get("image_data")
            image_format = message.payload.get("format", "PNG")

            if not image_data_b64:
                logger.error("No image data in message")
                return

            # Decode base64 image
            image_data = base64.b64decode(image_data_b64)
            image = Image.open(BytesIO(image_data))

            self.on_image_update(
                self.deck_serial,
                self.page,
                self.button,
                {
                    "type": "raw",
                    "image": image,
                    "format": image_format,
                },
            )

        except Exception as e:
            logger.error(f"Failed to handle raw image update: {e}")

    def _handle_update_image_render(self, message: ProtocolMessage) -> None:
        """Handle render instructions update from plugin."""
        if not self.on_image_update:
            return

        try:
            self.on_image_update(
                self.deck_serial,
                self.page,
                self.button,
                {
                    "type": "render",
                    "instructions": message.payload,
                },
            )

        except Exception as e:
            logger.error(f"Failed to handle render update: {e}")

    def _handle_page_switch_request(self, message: ProtocolMessage) -> None:
        """Handle page switch request from plugin."""
        if not self.on_page_switch_request:
            return

        if not self.can_switch_page:
            logger.warning(f"Plugin {self.instance_id} requested page switch but permission denied")
            return

        try:
            duration = message.payload.get("duration")
            self.on_page_switch_request(self.deck_serial, self.page, self.button, duration)

        except Exception as e:
            logger.error(f"Failed to handle page switch request: {e}")

    def _handle_log_message(self, message: ProtocolMessage) -> None:
        """Handle log message from plugin."""
        if not self.on_log_message:
            return

        try:
            level = message.payload.get("level", "info")
            log_message = message.payload.get("message", "")
            self.on_log_message(level, log_message)

        except Exception as e:
            logger.error(f"Failed to handle log message: {e}")


class PluginManager:
    """Manages all plugin instances."""

    def __init__(self, plugins_dir: Path):
        """Initialize plugin manager.

        Args:
            plugins_dir: Directory containing plugins
        """
        self.plugins_dir = plugins_dir
        self.plugins: Dict[str, PluginManifest] = {}
        self.instances: Dict[str, PluginInstance] = {}
        self.lock = threading.Lock()

        # Create plugins directory if it doesn't exist
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

    def discover_plugins(self) -> None:
        """Discover all plugins in the plugins directory."""
        logger.info(f"Discovering plugins in {self.plugins_dir}")

        for plugin_dir in self.plugins_dir.iterdir():
            if not plugin_dir.is_dir():
                continue

            manifest_path = plugin_dir / "manifest.yaml"
            if not manifest_path.exists():
                logger.warning(f"No manifest.yaml found in {plugin_dir}")
                continue

            try:
                manifest = PluginManifest.load_from_file(str(manifest_path))
                errors = manifest.validate()
                if errors:
                    logger.error(f"Invalid manifest in {plugin_dir}: {errors}")
                    continue

                plugin_id = plugin_dir.name
                self.plugins[plugin_id] = manifest
                logger.info(f"Discovered plugin: {manifest.name} v{manifest.version}")

            except Exception as e:
                logger.error(f"Failed to load plugin from {plugin_dir}: {e}")

    def get_plugin(self, plugin_id: str) -> Optional[PluginManifest]:
        """Get plugin manifest by ID."""
        return self.plugins.get(plugin_id)

    def get_all_plugins(self) -> Dict[str, PluginManifest]:
        """Get all discovered plugins."""
        return self.plugins.copy()

    def create_instance(
        self,
        plugin_id: str,
        deck_serial: str,
        page: int,
        button: int,
        config: Dict[str, Any],
        can_switch_page: bool = False,
    ) -> Optional[str]:
        """Create a new plugin instance.

        Args:
            plugin_id: Plugin identifier
            deck_serial: Stream Deck serial number
            page: Page number
            button: Button number
            config: Plugin configuration
            can_switch_page: Whether to allow page switching

        Returns:
            Instance ID if successful, None otherwise
        """
        manifest = self.plugins.get(plugin_id)
        if not manifest:
            logger.error(f"Plugin {plugin_id} not found")
            return None

        plugin_dir = self.plugins_dir / plugin_id
        instance_id = f"{plugin_id}_{deck_serial}_{page}_{button}"

        with self.lock:
            if instance_id in self.instances:
                logger.warning(f"Instance {instance_id} already exists")
                return None

            instance = PluginInstance(
                plugin_id=plugin_id,
                instance_id=instance_id,
                manifest=manifest,
                plugin_dir=plugin_dir,
                config=config,
                deck_serial=deck_serial,
                page=page,
                button=button,
                can_switch_page=can_switch_page,
            )

            self.instances[instance_id] = instance

        logger.info(f"Created plugin instance {instance_id}")
        return instance_id

    def start_instance(self, instance_id: str) -> bool:
        """Start a plugin instance.

        Args:
            instance_id: Instance identifier

        Returns:
            True if started successfully
        """
        instance = self.instances.get(instance_id)
        if not instance:
            logger.error(f"Instance {instance_id} not found")
            return False

        return instance.start()

    def stop_instance(self, instance_id: str) -> None:
        """Stop a plugin instance.

        Args:
            instance_id: Instance identifier
        """
        instance = self.instances.get(instance_id)
        if not instance:
            return

        instance.stop()

    def remove_instance(self, instance_id: str) -> None:
        """Remove a plugin instance.

        Args:
            instance_id: Instance identifier
        """
        self.stop_instance(instance_id)

        with self.lock:
            if instance_id in self.instances:
                del self.instances[instance_id]

    def get_instance(self, instance_id: str) -> Optional[PluginInstance]:
        """Get plugin instance by ID."""
        return self.instances.get(instance_id)

    def get_instances_for_button(self, deck_serial: str, page: int, button: int) -> list[PluginInstance]:
        """Get all plugin instances for a specific button."""
        result = []
        for instance in self.instances.values():
            if instance.deck_serial == deck_serial and instance.page == page and instance.button == button:
                result.append(instance)
        return result

    def monitor_instances(self) -> None:
        """Monitor plugin instances and restart if needed."""
        while True:
            time.sleep(5)

            with self.lock:
                instances = list(self.instances.values())

            for instance in instances:
                if not instance.running:
                    continue

                # Check if process is alive
                if not instance.is_alive():
                    logger.warning(f"Plugin {instance.instance_id} died")

                    # Retry if allowed
                    if instance.retry_count < instance.manifest.max_retries:
                        instance.retry_count += 1
                        logger.info(
                            f"Restarting plugin {instance.instance_id} "
                            f"(attempt {instance.retry_count}/{instance.manifest.max_retries})"
                        )
                        time.sleep(instance.manifest.retry_delay)
                        instance.start()
                    else:
                        logger.error(f"Plugin {instance.instance_id} exceeded max retries, giving up")
                        instance.running = False

                # Check if responsive
                elif not instance.is_responsive():
                    logger.warning(f"Plugin {instance.instance_id} is unresponsive")
