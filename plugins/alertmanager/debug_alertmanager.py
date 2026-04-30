#!/usr/bin/env python3
"""Debug script for testing the AlertManager plugin with a specific configuration."""

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path to import the plugin
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from streamdeck_ui.plugin_system.protocol import LogLevel

# Configuration from your StreamDeck setup
TEST_CONFIG = {
    "alertmanager_url": "https://alertmanager-staging.cloud.icij.org/",
    "environment_name": "staging",
    "poll_interval": 30,
    "client_cert": "/home/millaguie/NextCloud/SynologyDrive/Secrets/certs/client_cert.pem",
    "client_key": "/home/millaguie/NextCloud/SynologyDrive/Secrets/certs/private_key_nopass.pem",
    "auth_type": "none",
    "flash_duration": 10
}


class MockSocket:
    """Mock socket that doesn't require a real Unix socket."""

    def __init__(self):
        self.connected = False
        self.messages_sent = []

    def connect(self, path):
        self.connected = True
        print(f"[MockSocket] Connected to {path}")

    def sendall(self, data):
        if not self.connected:
            raise RuntimeError("Not connected")
        self.messages_sent.append(data)

    def recv(self, size):
        # Return empty to simulate no incoming messages
        return b''

    def close(self):
        self.connected = False
        print("[MockSocket] Disconnected")


def mock_send_message(plugin, message):
    """Mock send_message to log what would be sent."""
    msg_type = message.type.name if hasattr(message.type, 'name') else str(message.type)
    payload_preview = str(message.payload)[:100] if hasattr(message, 'payload') else ''
    print(f"[Plugin Message] Type: {msg_type}, Payload: {payload_preview}")


def main():
    """Run the AlertManager plugin in debug mode."""
    print("=" * 80)
    print("AlertManager Plugin Debug Script")
    print("=" * 80)
    print()

    print("Configuration:")
    print(json.dumps(TEST_CONFIG, indent=2))
    print()
    print("=" * 80)
    print()

    # Import the plugin
    from plugins.alertmanager.alertmanager_plugin import AlertManagerPlugin

    # Create mock socket
    mock_socket = MockSocket()

    # Create plugin with mock socket path
    socket_path = "/tmp/mock_socket"
    plugin = AlertManagerPlugin(socket_path, TEST_CONFIG)

    # Patch the socket connection and message sending
    with patch.object(plugin, 'socket', mock_socket):
        with patch.object(plugin, 'send_message', lambda msg: mock_send_message(plugin, msg)):
            # Override the run method to avoid the full event loop
            print("Initializing plugin...")
            mock_socket.connect(socket_path)
            plugin.running = True

            print("\n--- Calling on_start() ---")
            plugin.on_start()

            print("\n--- Initial display update ---")
            plugin._update_display()

            print("\n--- Polling AlertManager (initial) ---")
            plugin._poll_alertmanager()

            print("\n--- Updating display after poll ---")
            plugin._update_display()

            print("\n--- Simulating button press ---")
            plugin.on_button_pressed()

            print("\n--- Waiting 2 seconds and polling again ---")
            time.sleep(2)
            plugin._poll_alertmanager()
            plugin._update_display()

            print("\n--- Plugin State ---")
            print(f"Current alert count: {plugin.current_alert_count}")
            print(f"Last alert count: {plugin.last_alert_count}")
            print(f"Is flashing: {plugin.is_flashing}")
            print(f"Poll interval: {plugin.poll_interval}s")
            print(f"Flash duration: {plugin.flash_duration}s")

            print("\n--- Manual update() calls (simulating main loop) ---")
            print("This will simulate the periodic update that happens in the main loop.")
            print("Press Ctrl+C to stop.\n")

            try:
                iteration = 0
                while True:
                    iteration += 1
                    print(f"\n[Iteration {iteration}] Calling update()...")
                    plugin.update()

                    # Show current state
                    print(f"  Alerts: {plugin.current_alert_count}, "
                          f"Flashing: {plugin.is_flashing}, "
                          f"Time since last poll: {time.time() - plugin.last_poll_time:.1f}s")

                    time.sleep(5)  # Wait 5 seconds between updates

            except KeyboardInterrupt:
                print("\n\nStopping debug session...")

            mock_socket.close()

    print("\n" + "=" * 80)
    print("Debug session complete!")
    print("=" * 80)


if __name__ == '__main__':
    main()
