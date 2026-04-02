#!/usr/bin/env python3
"""Prometheus AlertManager monitoring plugin for StreamDeck UI."""

import json
import os
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

# Add parent directory to path to import base plugin
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from streamdeck_ui.plugin_system.base_plugin import BasePlugin
from streamdeck_ui.plugin_system.protocol import LogLevel


class AlertManagerPlugin(BasePlugin):
    """Plugin for monitoring Prometheus AlertManager."""

    def __init__(self, socket_path: str, config: dict[str, Any]):
        super().__init__(socket_path, config)

        # Configuration
        self.alertmanager_url = config.get('alertmanager_url', '')
        self.environment_name = config.get('environment_name', 'Unknown')
        self.poll_interval = int(config.get('poll_interval', 30))
        self.flash_duration = int(config.get('flash_duration', 10))
        self.browser_command = config.get('browser_command', '')

        # Authentication
        self.client_cert = config.get('client_cert')
        self.client_key = config.get('client_key')
        self.ca_cert = config.get('ca_cert')
        self.cert_password = config.get('cert_password')
        self.auth_type = config.get('auth_type', 'none')
        self.username = config.get('username')
        self.password = config.get('password')
        self.bearer_token = config.get('bearer_token')

        # State
        self.last_alert_count = 0
        self.current_alert_count = 0
        self.last_poll_time = 0
        self.is_flashing = False
        self.flash_state = False  # True = show "NEW!", False = show count
        self.flash_start_time = 0

        # Get plugin directory for icon
        self.plugin_dir = Path(__file__).parent
        self.icon_path = str(self.plugin_dir / 'alertmanager_icon.png')

    def _clean_certificate(self, cert_path: str) -> str:
        """
        Clean certificate file by removing Bag Attributes that may contain non-UTF-8 characters.
        Returns path to cleaned certificate (either original or temporary cleaned version).
        """
        try:
            with open(cert_path, 'rb') as f:
                content = f.read()

            # Check if certificate has Bag Attributes with potential encoding issues
            if b'Bag Attributes' in content:
                # Extract only the certificate/key parts, skip metadata
                lines = content.split(b'\n')
                cleaned_lines = []
                in_cert_block = False

                for line in lines:
                    # Start of certificate/key block
                    if line.startswith(b'-----BEGIN'):
                        in_cert_block = True
                        cleaned_lines.append(line)
                    # End of certificate/key block
                    elif line.startswith(b'-----END'):
                        cleaned_lines.append(line)
                        in_cert_block = False
                    # Inside certificate/key block
                    elif in_cert_block:
                        cleaned_lines.append(line)

                # Create temporary file with cleaned certificate
                temp_fd, temp_path = tempfile.mkstemp(suffix='.pem', prefix='alertmanager_cert_')
                try:
                    with os.fdopen(temp_fd, 'wb') as f:
                        f.write(b'\n'.join(cleaned_lines))
                    return temp_path
                except Exception as e:
                    os.unlink(temp_path)
                    raise e
            else:
                # No Bag Attributes, use original certificate
                return cert_path

        except Exception as e:
            plugin_info = getattr(self, 'plugin_id', self.environment_name)
            self.log(LogLevel.WARNING, f"[{plugin_info}] Could not clean certificate {cert_path}: {e!r}, using original")
            return cert_path

    def on_start(self) -> None:
        """Called when plugin starts."""
        self.log(LogLevel.INFO, f"AlertManager plugin started for {self.environment_name}")
        self.log(LogLevel.INFO, f"Monitoring: {self.alertmanager_url}")

        # Store identifier for better logging
        self.plugin_id = f"{self.environment_name} ({self.alertmanager_url})"

        # Log authentication configuration
        if self.auth_type != 'none':
            self.log(LogLevel.INFO, f"Authentication type: {self.auth_type}")
        if self.client_cert:
            self.log(LogLevel.INFO, f"Using client certificate: {self.client_cert}")
        if self.ca_cert:
            self.log(LogLevel.INFO, f"Using CA certificate: {self.ca_cert}")

        # Initial update
        self._update_display()

    def on_button_pressed(self) -> None:
        """Called when the button is pressed - open AlertManager in browser."""
        self.log(LogLevel.INFO, "Button pressed, opening AlertManager")

        try:
            if self.browser_command:
                # Use custom browser command
                subprocess.Popen([self.browser_command, self.alertmanager_url])
            else:
                # Use default browser
                webbrowser.open(self.alertmanager_url)

            # Stop flashing when user acknowledges
            if self.is_flashing:
                self.is_flashing = False
                self._update_display()

        except Exception as e:
            self.log(LogLevel.ERROR, f"Failed to open browser: {e}")

    def on_button_released(self) -> None:
        """Called when the button is released."""
        pass  # Nothing to do on release

    def on_button_visible(self, page: int, button: int) -> None:
        """Called when button becomes visible."""
        self.log(LogLevel.INFO, f"Button now visible on page {page}, button {button}")
        # Force update when becoming visible
        self._update_display()

    def on_button_hidden(self) -> None:
        """Called when button is no longer visible."""
        self.log(LogLevel.INFO, "Button now hidden")

    def on_config_update(self, config: dict[str, Any]) -> None:
        """Called when configuration is updated."""
        self.log(LogLevel.INFO, "Configuration updated")

        # Update configuration
        self.alertmanager_url = config.get('alertmanager_url', self.alertmanager_url)
        self.environment_name = config.get('environment_name', self.environment_name)
        self.poll_interval = int(config.get('poll_interval', self.poll_interval))
        self.flash_duration = int(config.get('flash_duration', self.flash_duration))
        self.browser_command = config.get('browser_command', self.browser_command)

        # Update authentication
        self.client_cert = config.get('client_cert')
        self.client_key = config.get('client_key')
        self.ca_cert = config.get('ca_cert')
        self.cert_password = config.get('cert_password')
        self.auth_type = config.get('auth_type', 'none')
        self.username = config.get('username')
        self.password = config.get('password')
        self.bearer_token = config.get('bearer_token')

        # Force immediate update with new config
        self._poll_alertmanager()
        self._update_display()

    def update(self) -> None:
        """Called periodically in the main loop."""
        current_time = time.time()

        # Poll AlertManager at configured interval
        if current_time - self.last_poll_time >= self.poll_interval:
            self._poll_alertmanager()
            self.last_poll_time = current_time
            # Update display after polling to show new alert count
            self._update_display()

        # Handle flashing
        if self.is_flashing:
            # Check if flash duration expired
            if current_time - self.flash_start_time >= self.flash_duration:
                self.is_flashing = False
                self.log(LogLevel.INFO, "Flash duration expired")
                # Update display when flashing stops
                self._update_display()

            # Toggle flash state every 0.5 seconds
            self.flash_state = not self.flash_state
            self._update_display()

    def _poll_alertmanager(self) -> None:
        """Poll AlertManager for current alerts."""
        try:
            # Build API URL
            api_url = f"{self.alertmanager_url}/api/v2/alerts"

            # Prepare request kwargs
            kwargs = {
                'timeout': 10,
            }

            # Add authentication
            if self.auth_type == 'basic' and self.username and self.password:
                kwargs['auth'] = HTTPBasicAuth(self.username, self.password)
            elif self.auth_type == 'bearer' and self.bearer_token:
                kwargs['headers'] = {'Authorization': f'Bearer {self.bearer_token}'}

            # Add certificates
            if self.client_cert and self.client_key:
                # Ensure certificate paths are properly encoded strings
                try:
                    # Convert to string and handle any encoding issues in the path
                    cert_path = str(self.client_cert) if isinstance(self.client_cert, bytes) else self.client_cert
                    key_path = str(self.client_key) if isinstance(self.client_key, bytes) else self.client_key

                    # Validate that files exist
                    if not os.path.exists(cert_path):
                        plugin_info = getattr(self, 'plugin_id', self.environment_name)
                        self.log(LogLevel.ERROR, f"[{plugin_info}] Client certificate not found: {cert_path}")
                        raise FileNotFoundError(f"Certificate not found: {cert_path}")
                    if not os.path.exists(key_path):
                        plugin_info = getattr(self, 'plugin_id', self.environment_name)
                        self.log(LogLevel.ERROR, f"[{plugin_info}] Client key not found: {key_path}")
                        raise FileNotFoundError(f"Key not found: {key_path}")

                    # Clean certificates to remove Bag Attributes with non-UTF-8 characters
                    # This prevents encoding errors when the SSL library tries to parse metadata
                    cleaned_cert_path = self._clean_certificate(cert_path)
                    cleaned_key_path = self._clean_certificate(key_path)

                    kwargs['cert'] = (cleaned_cert_path, cleaned_key_path)

                except Exception as e:
                    plugin_info = getattr(self, 'plugin_id', self.environment_name)
                    self.log(LogLevel.ERROR, f"[{plugin_info}] Error setting up client certificates: {e!r}")
                    raise

            if self.ca_cert:
                try:
                    ca_path = str(self.ca_cert) if isinstance(self.ca_cert, bytes) else self.ca_cert

                    if not os.path.exists(ca_path):
                        plugin_info = getattr(self, 'plugin_id', self.environment_name)
                        self.log(LogLevel.ERROR, f"[{plugin_info}] CA certificate not found: {ca_path}")
                        raise FileNotFoundError(f"CA certificate not found: {ca_path}")

                    # Clean CA certificate to remove Bag Attributes with non-UTF-8 characters
                    cleaned_ca_path = self._clean_certificate(ca_path)
                    kwargs['verify'] = cleaned_ca_path

                except Exception as e:
                    plugin_info = getattr(self, 'plugin_id', self.environment_name)
                    self.log(LogLevel.ERROR, f"[{plugin_info}] Error setting up CA certificate: {e!r}")
                    # Don't disable SSL verification on error - let it fail
                    raise
            else:
                # Disable SSL verification if no CA cert provided (not recommended for production!)
                plugin_info = getattr(self, 'plugin_id', self.environment_name)
                self.log(LogLevel.WARNING, f"[{plugin_info}] No CA certificate configured, disabling SSL verification")
                kwargs['verify'] = False

            # Make request
            response = requests.get(api_url, **kwargs)
            response.raise_for_status()

            # Parse alerts - handle potential encoding issues
            # Some AlertManager instances may return responses with encoding issues
            # Bypass requests' automatic encoding detection and decode manually

            # Get raw bytes to avoid any automatic encoding
            raw_content = response.content

            # Decode with error handling
            try:
                content = raw_content.decode('utf-8', errors='replace')
            except Exception as e:
                plugin_info = getattr(self, 'plugin_id', self.environment_name)
                self.log(LogLevel.ERROR, f"[{plugin_info}] Failed to decode response bytes: {e}")
                # Try with latin-1 as fallback (accepts all byte values)
                content = raw_content.decode('latin-1', errors='replace')

            # Parse JSON
            try:
                alerts = json.loads(content)
            except json.JSONDecodeError as e:
                plugin_info = getattr(self, 'plugin_id', self.environment_name)
                self.log(LogLevel.ERROR, f"[{plugin_info}] Failed to parse JSON response: {e}")
                self.log(LogLevel.ERROR, f"[{plugin_info}] First 200 chars of content: {content[:200]}")
                raise

            # Count firing alerts (not resolved or suppressed)
            # AlertManager API returns alerts with status.state as:
            # - 'unprocessed': newly received
            # - 'active': being processed
            # - 'suppressed': silenced/inhibited
            # We count all non-suppressed alerts as these represent alerts that need attention

            plugin_info = getattr(self, 'plugin_id', self.environment_name)

            # Log all alerts and their states for debugging
            total_alerts = len(alerts)
            self.log(LogLevel.INFO, f"[{plugin_info}] Received {total_alerts} total alerts from AlertManager")

            alert_states = {}
            for alert in alerts:
                state = alert.get('status', {}).get('state', 'unknown')
                alert_states[state] = alert_states.get(state, 0) + 1

            self.log(LogLevel.INFO, f"[{plugin_info}] Alert states: {alert_states}")

            firing_alerts = [a for a in alerts if a.get('status', {}).get('state') in ['unprocessed', 'active']]
            self.current_alert_count = len(firing_alerts)

            self.log(LogLevel.INFO, f"[{plugin_info}] Polled AlertManager: {self.current_alert_count} firing alerts (was {self.last_alert_count})")

            # Check for new alerts
            if self.current_alert_count > self.last_alert_count and self.last_alert_count > 0:
                # New alerts detected!
                new_alert_count = self.current_alert_count - self.last_alert_count
                self.log(LogLevel.WARNING, f"NEW ALERTS DETECTED! +{new_alert_count} alerts")

                # Start flashing
                self.is_flashing = True
                self.flash_start_time = time.time()
                self.flash_state = True

                # Request page switch to show this button
                self.request_page_switch(duration=self.flash_duration)

            self.last_alert_count = self.current_alert_count

        except requests.exceptions.RequestException as e:
            # Handle encoding issues in error messages
            plugin_info = getattr(self, 'plugin_id', self.environment_name)
            try:
                error_msg = str(e)
            except UnicodeDecodeError:
                error_msg = repr(e)
            self.log(LogLevel.ERROR, f"[{plugin_info}] Failed to poll AlertManager: {error_msg}")
            # Keep previous count on error
        except UnicodeDecodeError as e:
            # Specific handling for encoding errors
            plugin_info = getattr(self, 'plugin_id', self.environment_name)
            self.log(LogLevel.ERROR, f"[{plugin_info}] Encoding error polling AlertManager: {type(e).__name__} at position {e.start}")
            self.log(LogLevel.ERROR, f"[{plugin_info}] URL: {api_url}")
            self.log(LogLevel.ERROR, f"[{plugin_info}] Error details: {e!r}")
        except Exception as e:
            # Handle encoding issues in error messages
            plugin_info = getattr(self, 'plugin_id', self.environment_name)
            try:
                error_msg = str(e)
            except Exception:
                error_msg = repr(e)
            self.log(LogLevel.ERROR, f"[{plugin_info}] Unexpected error polling AlertManager: {error_msg}")

    def _update_display(self) -> None:
        """Update the button display."""
        try:
            plugin_info = getattr(self, 'plugin_id', self.environment_name)
            self.log(LogLevel.DEBUG, f"[{plugin_info}] Updating display: {self.current_alert_count} alerts, flashing={self.is_flashing}")

            if self.is_flashing and self.flash_state:
                # Show "NEW!" when flashing
                self.update_image_render(
                    text=f"{self.environment_name}\n\nNEW!",
                    icon=self.icon_path if os.path.exists(self.icon_path) else None,
                    background_color="#FF0000",  # Red background
                    font_color="#FFFFFF",  # White text
                    font_size=14,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
            else:
                # Show normal display with alert count
                if self.current_alert_count > 0:
                    # Alerts active - show in orange/red
                    bg_color = "#FF4500" if self.current_alert_count >= 5 else "#FFA500"
                    text_color = "#FFFFFF"
                else:
                    # No alerts - show in green
                    bg_color = "#2E7D32"
                    text_color = "#FFFFFF"

                self.update_image_render(
                    text=f"{self.environment_name}\n\n{self.current_alert_count} alerts",
                    icon=self.icon_path if os.path.exists(self.icon_path) else None,
                    background_color=bg_color,
                    font_color=text_color,
                    font_size=12,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )

        except Exception as e:
            self.log(LogLevel.ERROR, f"Failed to update display: {e}")


def main():
    """Main entry point for plugin."""
    if len(sys.argv) < 3:
        print("Usage: alertmanager_plugin.py <socket_path> <config_json>")
        sys.exit(1)

    socket_path = sys.argv[1]
    config_json = sys.argv[2]

    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as e:
        print(f"Invalid config JSON: {e}")
        sys.exit(1)

    # Create and run plugin
    plugin = AlertManagerPlugin(socket_path, config)
    plugin.run()


if __name__ == '__main__':
    main()
