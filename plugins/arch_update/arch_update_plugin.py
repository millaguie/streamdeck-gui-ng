#!/usr/bin/env python3
"""Arch Linux update monitoring plugin for StreamDeck UI."""

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Add parent directory to path to import base plugin
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from streamdeck_ui.plugin_system.base_plugin import BasePlugin
from streamdeck_ui.plugin_system.protocol import LogLevel


class ArchUpdatePlugin(BasePlugin):
    """Plugin for monitoring Arch Linux system updates."""

    def __init__(self, socket_path: str, config: Dict[str, Any]):
        super().__init__(socket_path, config)

        # Configuration
        self.check_command = config.get('check_command', 'checkupdates')
        self.aur_check_command = config.get('aur_check_command', '')
        self.flatpak_check = config.get('flatpak_check', False)
        self.update_command = config.get('update_command', 'sudo pacman -Syu')
        self.terminal = config.get('terminal', 'gnome-terminal')
        self.terminal_execute_flag = config.get('terminal_execute_flag', '--')
        self.check_interval = int(config.get('check_interval', 3600))
        self.notify_on_updates = config.get('notify_on_updates', True)
        self.notify_duration = int(config.get('notify_duration', 10))
        self.show_count_when_zero = config.get('show_count_when_zero', False)
        self.system_name = config.get('system_name', 'System')

        # State
        self.last_check_time = 0
        self.last_update_count = -1  # -1 means not checked yet
        self.current_update_count = 0
        self.pacman_updates = 0
        self.aur_updates = 0
        self.flatpak_updates = 0
        self.check_error = None
        self.is_checking = False

        # Get plugin directory for icon
        self.plugin_dir = Path(__file__).parent
        self.icon_path = str(self.plugin_dir / 'arch_icon.png')

    def on_start(self) -> None:
        """Called when plugin starts."""
        self.log(LogLevel.INFO, f"Arch Update plugin started for {self.system_name}")
        self.log(LogLevel.INFO, f"Check command: {self.check_command}")

        # Show initial loading state
        self._update_display()

        # Perform immediate check
        self._check_updates()

    def on_button_pressed(self) -> None:
        """Called when the button is pressed - launch update command."""
        self.log(LogLevel.INFO, "Button pressed, launching update command")

        try:
            # Build terminal command
            # Most terminals: terminal [--execute-flag] command
            terminal_cmd = [self.terminal]

            # Handle different terminal execute flags
            if self.terminal_execute_flag:
                if self.terminal_execute_flag == '--':
                    # gnome-terminal, mate-terminal
                    terminal_cmd.extend(['--', 'bash', '-c', self.update_command])
                elif self.terminal_execute_flag == '-e':
                    # xterm, urxvt, terminator
                    terminal_cmd.extend(['-e', 'bash', '-c', self.update_command])
                elif self.terminal_execute_flag == '-x':
                    # xfce4-terminal
                    terminal_cmd.extend(['-x', 'bash', '-c', self.update_command])
                else:
                    # Generic
                    terminal_cmd.extend([self.terminal_execute_flag, self.update_command])
            else:
                # Fallback for terminals that don't need a flag
                terminal_cmd.extend(['bash', '-c', self.update_command])

            # Launch terminal
            subprocess.Popen(terminal_cmd, start_new_session=True)

            self.log(LogLevel.INFO, f"Launched: {' '.join(terminal_cmd)}")

            # Force recheck after update command is launched
            # User might want to see if updates are applied
            # Wait a bit to let the user actually update
            # (this is just launching the command, not waiting for it)

        except Exception as e:
            self.log(LogLevel.ERROR, f"Failed to launch update command: {e}")
            self.check_error = f"Launch error: {str(e)}"
            self._update_display()

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

    def on_config_update(self, config: Dict[str, Any]) -> None:
        """Called when configuration is updated."""
        self.log(LogLevel.INFO, "Configuration updated")

        # Update configuration
        self.check_command = config.get('check_command', self.check_command)
        self.aur_check_command = config.get('aur_check_command', self.aur_check_command)
        self.flatpak_check = config.get('flatpak_check', self.flatpak_check)
        self.update_command = config.get('update_command', self.update_command)
        self.terminal = config.get('terminal', self.terminal)
        self.terminal_execute_flag = config.get('terminal_execute_flag', self.terminal_execute_flag)
        self.check_interval = int(config.get('check_interval', self.check_interval))
        self.notify_on_updates = config.get('notify_on_updates', self.notify_on_updates)
        self.notify_duration = int(config.get('notify_duration', self.notify_duration))
        self.show_count_when_zero = config.get('show_count_when_zero', self.show_count_when_zero)
        self.system_name = config.get('system_name', self.system_name)

        # Force immediate check with new config
        self._check_updates()
        self._update_display()

    def update(self) -> None:
        """Called periodically in the main loop."""
        current_time = time.time()

        # Check for updates at configured interval
        if not self.is_checking and current_time - self.last_check_time >= self.check_interval:
            self._check_updates()
            self.last_check_time = current_time

    def _check_updates(self) -> None:
        """Check for available updates."""
        self.is_checking = True
        self.check_error = None

        try:
            self.log(LogLevel.DEBUG, "Checking for updates...")

            # Reset counts
            self.pacman_updates = 0
            self.aur_updates = 0
            self.flatpak_updates = 0

            # Check pacman updates
            self.pacman_updates = self._check_pacman()
            self.log(LogLevel.DEBUG, f"Pacman updates: {self.pacman_updates}")

            # Check AUR updates if configured
            if self.aur_check_command:
                self.aur_updates = self._check_aur()
                self.log(LogLevel.DEBUG, f"AUR updates: {self.aur_updates}")

            # Check Flatpak updates if enabled
            if self.flatpak_check:
                self.flatpak_updates = self._check_flatpak()
                self.log(LogLevel.DEBUG, f"Flatpak updates: {self.flatpak_updates}")

            # Calculate total
            self.current_update_count = self.pacman_updates + self.aur_updates + self.flatpak_updates

            self.log(LogLevel.INFO, f"Total updates available: {self.current_update_count}")

            # Check if new updates appeared
            if self.last_update_count >= 0:  # Skip notification on first check
                if self.current_update_count > self.last_update_count and self.current_update_count > 0:
                    new_updates = self.current_update_count - self.last_update_count
                    self.log(LogLevel.WARNING, f"NEW UPDATES DETECTED! +{new_updates} updates")

                    # Request page switch if configured
                    if self.notify_on_updates:
                        self.request_page_switch(duration=self.notify_duration)

            self.last_update_count = self.current_update_count

        except Exception as e:
            self.log(LogLevel.ERROR, f"Failed to check updates: {e}")
            self.check_error = str(e)

        finally:
            self.is_checking = False
            self._update_display()

    def _check_pacman(self) -> int:
        """Check pacman updates."""
        try:
            # Run check command
            result = subprocess.run(
                shlex.split(self.check_command),
                capture_output=True,
                text=True,
                timeout=30
            )

            # checkupdates returns non-zero when there are no updates
            # Count lines in output (each line is one update)
            if result.stdout:
                return len([line for line in result.stdout.strip().split('\n') if line])
            return 0

        except subprocess.TimeoutExpired:
            self.log(LogLevel.ERROR, "Pacman check timed out")
            return 0
        except FileNotFoundError:
            self.log(LogLevel.ERROR, f"Command not found: {self.check_command}")
            self.check_error = f"Command not found: {self.check_command}"
            return 0
        except Exception as e:
            self.log(LogLevel.ERROR, f"Pacman check failed: {e}")
            return 0

    def _check_aur(self) -> int:
        """Check AUR updates."""
        try:
            # Run AUR check command (e.g., yay -Qu, paru -Qu)
            result = subprocess.run(
                shlex.split(self.aur_check_command),
                capture_output=True,
                text=True,
                timeout=60  # AUR checks can be slower
            )

            # Count lines (each line is one update)
            if result.stdout:
                # Filter out lines that are not package updates
                lines = result.stdout.strip().split('\n')
                return len([line for line in lines if line and '->' in line])
            return 0

        except subprocess.TimeoutExpired:
            self.log(LogLevel.ERROR, "AUR check timed out")
            return 0
        except FileNotFoundError:
            self.log(LogLevel.ERROR, f"AUR helper not found: {self.aur_check_command}")
            return 0
        except Exception as e:
            self.log(LogLevel.ERROR, f"AUR check failed: {e}")
            return 0

    def _check_flatpak(self) -> int:
        """Check Flatpak updates."""
        try:
            # Run flatpak remote-ls --updates
            result = subprocess.run(
                ['flatpak', 'remote-ls', '--updates'],
                capture_output=True,
                text=True,
                timeout=30
            )

            # Count lines (each line is one update)
            if result.stdout:
                return len([line for line in result.stdout.strip().split('\n') if line])
            return 0

        except subprocess.TimeoutExpired:
            self.log(LogLevel.ERROR, "Flatpak check timed out")
            return 0
        except FileNotFoundError:
            self.log(LogLevel.WARNING, "Flatpak not installed")
            return 0
        except Exception as e:
            self.log(LogLevel.ERROR, f"Flatpak check failed: {e}")
            return 0

    def _update_display(self) -> None:
        """Update the button display."""
        try:
            # Determine display based on state
            if self.is_checking:
                # Show checking state
                self.update_image_render(
                    text=f"{self.system_name}\n\nChecking...",
                    icon=self.icon_path if os.path.exists(self.icon_path) else None,
                    background_color="#555555",
                    font_color="#FFFFFF",
                    font_size=12,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
            elif self.check_error:
                # Show error state
                self.update_image_render(
                    text=f"{self.system_name}\n\nError",
                    icon=self.icon_path if os.path.exists(self.icon_path) else None,
                    background_color="#AA0000",
                    font_color="#FFFFFF",
                    font_size=12,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
            elif self.last_update_count < 0:
                # Not checked yet
                self.update_image_render(
                    text=f"{self.system_name}\n\nStarting...",
                    icon=self.icon_path if os.path.exists(self.icon_path) else None,
                    background_color="#555555",
                    font_color="#FFFFFF",
                    font_size=12,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
            else:
                # Show update count
                if self.current_update_count == 0:
                    # Up to date
                    if self.show_count_when_zero:
                        text = f"{self.system_name}\n\n0 updates"
                    else:
                        text = f"{self.system_name}\n\nUp to date"
                    bg_color = "#2E7D32"  # Green
                else:
                    # Updates available
                    # Build text with breakdown if multiple sources
                    text = f"{self.system_name}\n\n{self.current_update_count} update"
                    if self.current_update_count != 1:
                        text += "s"

                    # Add breakdown if multiple sources
                    parts = []
                    if self.pacman_updates > 0:
                        parts.append(f"P:{self.pacman_updates}")
                    if self.aur_updates > 0:
                        parts.append(f"A:{self.aur_updates}")
                    if self.flatpak_updates > 0:
                        parts.append(f"F:{self.flatpak_updates}")

                    if len(parts) > 1:
                        text += f"\n({' '.join(parts)})"

                    # Color based on update count
                    if self.current_update_count >= 20:
                        bg_color = "#D32F2F"  # Red
                    elif self.current_update_count >= 10:
                        bg_color = "#F57C00"  # Orange
                    else:
                        bg_color = "#1976D2"  # Blue

                self.update_image_render(
                    text=text,
                    icon=self.icon_path if os.path.exists(self.icon_path) else None,
                    background_color=bg_color,
                    font_color="#FFFFFF",
                    font_size=11 if self.current_update_count > 0 and len(parts) > 1 else 12,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )

        except Exception as e:
            self.log(LogLevel.ERROR, f"Failed to update display: {e}")


def main():
    """Main entry point for plugin."""
    if len(sys.argv) < 3:
        print("Usage: arch_update_plugin.py <socket_path> <config_json>")
        sys.exit(1)

    socket_path = sys.argv[1]
    config_json = sys.argv[2]

    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as e:
        print(f"Invalid config JSON: {e}")
        sys.exit(1)

    # Create and run plugin
    plugin = ArchUpdatePlugin(socket_path, config)
    plugin.run()


if __name__ == '__main__':
    main()
