#!/usr/bin/env python3
"""DeepSeek balance monitoring plugin for StreamDeck UI."""

import json
import sys
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont

# Add parent directory to path to import base plugin
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from streamdeck_ui.plugin_system.base_plugin import BasePlugin
from streamdeck_ui.plugin_system.protocol import LogLevel

BALANCE_URL = "https://api.deepseek.com/user/balance"
DEFAULT_OPENCODE_AUTH = str(Path.home() / ".local" / "share" / "opencode" / "auth.json")


class DeepSeekUsagePlugin(BasePlugin):
    """Plugin for monitoring DeepSeek API balance."""

    def __init__(self, socket_path: str, config: dict[str, Any]):
        super().__init__(socket_path, config)

        self.api_key = config.get('api_key', '')
        self.opencode_auth_path = config.get('opencode_auth_path', '') or DEFAULT_OPENCODE_AUTH
        self.poll_interval = max(int(config.get('poll_interval', 300)), 60)
        self.display_mode = config.get('display_mode', 'compact')
        self.rotate_interval = int(config.get('rotate_interval', 5))

        # State
        self.last_poll_time = 0
        self.balance_data: dict[str, Any] | None = None
        self.error_message: str | None = None
        self.current_view = 0
        self.last_rotate_time = 0

    def _resolve_key(self) -> str:
        """Get API key from config or opencode auth.json."""
        if self.api_key:
            return self.api_key
        try:
            with open(self.opencode_auth_path) as f:
                auth = json.load(f)
            for key_name in ('deepseek-coding-plan', 'deepseek'):
                entry = auth.get(key_name, {})
                if entry.get('key'):
                    self.log(LogLevel.INFO, f"Using API key from opencode auth ({key_name})")
                    return entry['key']
        except FileNotFoundError:
            self.log(LogLevel.WARNING, f"opencode auth not found: {self.opencode_auth_path}")
        except (json.JSONDecodeError, KeyError) as e:
            self.log(LogLevel.ERROR, f"Failed to read opencode auth: {e}")
        return ""

    def _fetch_usage(self) -> bool:
        """Fetch balance data from DeepSeek API. Returns True on success."""
        key = self._resolve_key()
        if not key:
            self.error_message = "No\nAPI key"
            return False

        try:
            headers = {"Authorization": f"Bearer {key}"}
            response = requests.get(BALANCE_URL, headers=headers, timeout=10)

            if response.status_code == 401:
                self.log(LogLevel.ERROR, "DeepSeek auth failed (401)")
                self.error_message = "Auth\nfailed"
                return False

            if response.status_code == 429:
                self.log(LogLevel.WARNING, "DeepSeek rate limited")
                self.error_message = "Rate\nlimited"
                return False

            response.raise_for_status()
            data = response.json()

            self.balance_data = data
            return True

        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch DeepSeek balance: {e}")
            self.error_message = "API\nerror"
            return False

    def _get_bar_color(self, balance: float, threshold_low: float = 1.0, threshold_mid: float = 5.0) -> tuple[int, int, int]:
        """Get bar fill color based on balance amount."""
        if balance <= 0:
            return (183, 28, 28)
        elif balance < threshold_low:
            return (230, 81, 0)
        elif balance < threshold_mid:
            return (245, 127, 23)
        else:
            return (21, 101, 192)

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Load a font, falling back to default."""
        for path in [
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ]:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _draw_bar(self, draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, pct: float) -> None:
        """Draw a progress bar with border and fill."""
        draw.rectangle([x, y, x + w, y + h], fill=(30, 30, 30), outline=(80, 80, 80))
        fill_w = int(w * min(pct, 100) / 100)
        if fill_w > 0:
            color = self._get_bar_color(pct)
            draw.rectangle([x + 1, y + 1, x + fill_w, y + h - 1], fill=color)

    def _parse_balances(self) -> list[dict[str, Any]]:
        """Extract balance info entries."""
        if not self.balance_data:
            return []
        return self.balance_data.get('balance_infos', [])

    def _update_display(self) -> None:
        """Update the button display."""
        try:
            if self.error_message and not self.balance_data:
                self.update_image_render(
                    text=f"DeepSeek\n{self.error_message}",
                    background_color="#B71C1C",
                    font_color="#FFFFFF",
                    font_size=10,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if not self.balance_data:
                self.update_image_render(
                    text="DeepSeek\n...",
                    background_color="#37474F",
                    font_color="#FFFFFF",
                    font_size=11,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if self.display_mode == 'rotate':
                self._render_rotate_view()
            else:
                self._render_compact_view()

        except Exception as e:
            self.log(LogLevel.ERROR, f"Display update failed: {e}")

    def _render_compact_view(self) -> None:
        """Render compact view showing balance summary."""
        balances = self._parse_balances()
        is_available = self.balance_data.get('is_available', False) if self.balance_data else False

        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_value = self._load_font(12)
        font_small = self._load_font(9)

        if not balances:
            draw.text((36, 36), "DeepSeek\nNo data", fill=(255, 255, 255), font=font_title, anchor="mm")
            self.update_image_raw(img)
            return

        bal = balances[0]
        currency = bal.get('currency', '?')
        total = float(bal.get('total_balance', '0'))
        granted = float(bal.get('granted_balance', '0'))
        topped_up = float(bal.get('topped_up_balance', '0'))

        # Status indicator
        status_color = (76, 175, 80) if is_available else (183, 28, 28)
        draw.ellipse([2, 2, 8, 8], fill=status_color)

        # Title
        draw.text((12, 2), "DeepSeek", fill=(255, 255, 255), font=font_title)

        # Total balance
        total_str = f"{currency} {total:.2f}"
        balance_color = self._get_bar_color(total)
        draw.text((36, 22), total_str, fill=balance_color, font=font_value, anchor="mt")

        # Breakdown
        draw.text((4, 38), f"Free: {granted:.2f}", fill=(180, 180, 180), font=font_small)
        draw.text((4, 50), f"Paid: {topped_up:.2f}", fill=(180, 180, 180), font=font_small)

        # Simple balance bar (percentage of some reference, e.g. 100 units)
        ref = max(total, 10)
        pct = min(total / ref * 100, 100)
        bar_color = self._get_bar_color(total)
        draw.rectangle([4, 63, 68, 69], fill=(30, 30, 30), outline=(80, 80, 80))
        fill_w = int(64 * min(pct, 100) / 100)
        if fill_w > 0:
            draw.rectangle([5, 64, 4 + fill_w, 68], fill=bar_color)

        self.update_image_raw(img)

    def _render_rotate_view(self) -> None:
        """Render rotating views cycling through balance details."""
        balances = self._parse_balances()
        is_available = self.balance_data.get('is_available', False) if self.balance_data else False

        if not balances:
            self.update_image_render(
                text="DeepSeek\nNo data",
                background_color="#37474F",
                font_color="#FFFFFF",
                font_size=11,
                text_vertical_align="middle",
                text_horizontal_align="center",
            )
            return

        bal = balances[0]
        currency = bal.get('currency', '?')
        total = float(bal.get('total_balance', '0'))
        granted = float(bal.get('granted_balance', '0'))
        topped_up = float(bal.get('topped_up_balance', '0'))

        views = [
            ("Total", f"{currency} {total:.2f}"),
            ("Free", f"{currency} {granted:.2f}"),
            ("Paid", f"{currency} {topped_up:.2f}"),
        ]

        idx = self.current_view % len(views)
        label, value = views[idx]

        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_label = self._load_font(12)
        font_value = self._load_font(14)

        # Status indicator
        status_color = (76, 175, 80) if is_available else (183, 28, 28)
        draw.ellipse([2, 2, 8, 8], fill=status_color)

        draw.text((12, 2), "DeepSeek", fill=(255, 255, 255), font=font_title)
        draw.text((36, 28), label, fill=(180, 180, 180), font=font_label, anchor="mt")

        balance_color = self._get_bar_color(total)
        draw.text((36, 46), value, fill=balance_color, font=font_value, anchor="mt")

        self.update_image_raw(img)

    def on_start(self) -> None:
        self.log(LogLevel.INFO, "DeepSeek Usage plugin started")
        self.log(LogLevel.INFO, f"Poll interval: {self.poll_interval}s, Display: {self.display_mode}")
        self._update_display()

    def on_button_pressed(self) -> None:
        """Force refresh on button press."""
        self.log(LogLevel.INFO, "Button pressed, forcing balance refresh")
        if self._fetch_usage():
            self.error_message = None
        self.last_poll_time = time.time()
        self._update_display()

    def on_button_released(self) -> None:
        pass

    def on_button_visible(self, page: int, button: int) -> None:
        self._update_display()

    def on_button_hidden(self) -> None:
        pass

    def on_config_update(self, config: dict[str, Any]) -> None:
        self.api_key = config.get('api_key', '')
        self.opencode_auth_path = config.get('opencode_auth_path', '') or DEFAULT_OPENCODE_AUTH
        self.poll_interval = max(int(config.get('poll_interval', 300)), 60)
        self.display_mode = config.get('display_mode', 'compact')
        self.rotate_interval = int(config.get('rotate_interval', 5))
        self.log(LogLevel.INFO, "Configuration updated")
        if self._fetch_usage():
            self.error_message = None
        self.last_poll_time = time.time()
        self._update_display()

    def update(self) -> None:
        current_time = time.time()

        if current_time - self.last_poll_time >= self.poll_interval:
            if self._fetch_usage():
                self.error_message = None
            self.last_poll_time = current_time
            self._update_display()

        if self.display_mode == 'rotate' and self.balance_data:
            if current_time - self.last_rotate_time >= self.rotate_interval:
                self.current_view += 1
                self.last_rotate_time = current_time
                self._update_display()


def main():
    if len(sys.argv) < 3:
        print("Usage: deepseek_usage_plugin.py <socket_path> <config_json>")
        sys.exit(1)

    socket_path = sys.argv[1]
    try:
        config = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(f"Invalid config JSON: {e}")
        sys.exit(1)

    plugin = DeepSeekUsagePlugin(socket_path, config)
    plugin.run()


if __name__ == '__main__':
    main()
