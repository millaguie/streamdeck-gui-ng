#!/usr/bin/env python3
"""MiniMax Coding Plan usage monitoring plugin for StreamDeck UI."""

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

REMAINS_URL = "https://platform.minimax.io/v1/api/openplatform/coding_plan/remains"
DEFAULT_OPENCODE_AUTH = str(Path.home() / ".local" / "share" / "opencode" / "auth.json")


class MiniMaxUsagePlugin(BasePlugin):
    """Plugin for monitoring MiniMax Coding Plan consumption."""

    def __init__(self, socket_path: str, config: dict[str, Any]):
        super().__init__(socket_path, config)

        self.api_key = config.get('api_key', '')
        self.group_id = config.get('group_id', '')
        self.opencode_auth_path = config.get('opencode_auth_path', '') or DEFAULT_OPENCODE_AUTH
        self.poll_interval = max(int(config.get('poll_interval', 300)), 60)
        self.display_mode = config.get('display_mode', 'compact')
        self.rotate_interval = int(config.get('rotate_interval', 5))

        # State
        self.last_poll_time = 0
        self.model_remains: list[dict[str, Any]] = []
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
            for key_name in ('minimax-coding-plan', 'minimax'):
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
        """Fetch coding plan remains from MiniMax API."""
        key = self._resolve_key()
        if not key:
            self.error_message = "No\nkey"
            return False

        if not self.group_id:
            self.error_message = "No\ngroup"
            return False

        try:
            response = requests.get(
                REMAINS_URL,
                params={"GroupId": self.group_id},
                headers={
                    "accept": "application/json, text/plain, */*",
                    "authorization": f"Bearer {key}",
                    "referer": "https://platform.minimax.io/user-center/payment/coding-plan",
                    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)",
                },
                timeout=10,
            )

            if response.status_code == 401:
                self.error_message = "Auth\nfailed"
                self.log(LogLevel.ERROR, "MiniMax auth failed (401)")
                return False
            if response.status_code == 429:
                self.error_message = "Rate\nlimit"
                self.log(LogLevel.WARNING, "MiniMax rate limited")
                return False

            response.raise_for_status()
            data = response.json()

            status = data.get('base_resp', {}).get('status_code', -1)
            if status != 0:
                msg = data.get('base_resp', {}).get('status_msg', 'unknown error')
                self.log(LogLevel.ERROR, f"MiniMax API error: {msg}")
                self.error_message = "API\nerror"
                return False

            self.model_remains = data.get('model_remains', [])
            self.log(LogLevel.INFO, f"MiniMax: got {len(self.model_remains)} model(s)")
            return True

        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch MiniMax usage: {e}")
            self.error_message = "API\nerror"
            return False

    def _format_reset_time(self, remains_ms: int) -> str:
        """Format remaining time in milliseconds as human-readable."""
        if remains_ms <= 0:
            return "now"

        total_seconds = remains_ms // 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60

        if hours >= 24:
            days = hours // 24
            remaining_hours = hours % 24
            return f"{days}d{remaining_hours}h"
        elif hours > 0:
            return f"{hours}h{minutes:02d}m"
        else:
            return f"{minutes}m"

    def _short_model_name(self, model: str) -> str:
        """Shorten model name for display."""
        replacements = {
            "MiniMax-M2.7": "M2.7",
            "MiniMax-M2.5": "M2.5",
            "MiniMax-": "MM-",
            "minimax-": "mm-",
        }
        for old, new in replacements.items():
            model = model.replace(old, new)
        # Truncate if still too long
        if len(model) > 8:
            model = model[:8]
        return model

    def _get_bar_color(self, pct: float) -> tuple[int, int, int]:
        if pct >= 90:
            return (183, 28, 28)
        elif pct >= 75:
            return (230, 81, 0)
        elif pct >= 50:
            return (245, 127, 23)
        elif pct >= 25:
            return (27, 94, 32)
        else:
            return (21, 101, 192)

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
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
        draw.rectangle([x, y, x + w, y + h], fill=(30, 30, 30), outline=(80, 80, 80))
        fill_w = int(w * min(pct, 100) / 100)
        if fill_w > 0:
            color = self._get_bar_color(pct)
            draw.rectangle([x + 1, y + 1, x + fill_w, y + h - 1], fill=color)

    def _update_display(self) -> None:
        try:
            if self.error_message and not self.model_remains:
                self.update_image_render(
                    text=f"MiniMax\n{self.error_message}",
                    background_color="#B71C1C",
                    font_color="#FFFFFF",
                    font_size=11,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if not self.model_remains:
                self.update_image_render(
                    text="MiniMax\n...",
                    background_color="#37474F",
                    font_color="#FFFFFF",
                    font_size=12,
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
        """Render compact view showing models with progress bars."""
        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)

        n = min(len(self.model_remains), 2)
        section_h = 72 // n
        font_label = self._load_font(11)
        font_small = self._load_font(9)

        for i, model in enumerate(self.model_remains[:n]):
            total = model.get('current_interval_total_count', 0)
            remaining = model.get('current_interval_usage_count', 0)
            used = total - remaining
            pct = (used / total * 100) if total > 0 else 0
            reset = self._format_reset_time(model.get('remains_time', 0))
            name = self._short_model_name(model.get('model_name', '?'))

            y_base = i * section_h

            draw.text((4, y_base + 2), f"{name} {pct:.0f}%", fill=(255, 255, 255), font=font_label)
            self._draw_bar(draw, 4, y_base + 16, 64, 8, pct)
            draw.text((4, y_base + 26), f"{used}/{total} {reset}", fill=(180, 180, 180), font=font_small)

        self.update_image_raw(img)

    def _render_rotate_view(self) -> None:
        """Render rotating views cycling through models."""
        if not self.model_remains:
            return

        idx = self.current_view % len(self.model_remains)
        model = self.model_remains[idx]

        total = model.get('current_interval_total_count', 0)
        remaining = model.get('current_interval_usage_count', 0)
        used = total - remaining
        pct = (used / total * 100) if total > 0 else 0
        reset = self._format_reset_time(model.get('remains_time', 0))
        name = self._short_model_name(model.get('model_name', '?'))

        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_label = self._load_font(14)
        font_mid = self._load_font(11)
        font_small = self._load_font(9)

        draw.text((36, 6), name, fill=(255, 255, 255), font=font_label, anchor="mt")
        draw.text((36, 22), f"{used}/{total}", fill=(255, 255, 255), font=font_mid, anchor="mt")
        self._draw_bar(draw, 4, 38, 64, 10, pct)
        draw.text((36, 41), f"{pct:.0f}%", fill=(255, 255, 255), font=font_small, anchor="mm")
        draw.text((36, 56), reset, fill=(180, 180, 180), font=font_small, anchor="mt")

        self.update_image_raw(img)

    def on_start(self) -> None:
        self.log(LogLevel.INFO, f"MiniMax Usage plugin started, Group: {self.group_id}")
        self.log(LogLevel.INFO, f"Poll interval: {self.poll_interval}s, Display: {self.display_mode}")
        self._update_display()

    def on_button_pressed(self) -> None:
        self.log(LogLevel.INFO, "Button pressed, forcing refresh")
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
        self.group_id = config.get('group_id', '')
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

        if self.display_mode == 'rotate' and self.model_remains:
            if current_time - self.last_rotate_time >= self.rotate_interval:
                self.current_view += 1
                self.last_rotate_time = current_time
                self._update_display()


def main():
    if len(sys.argv) < 3:
        print("Usage: minimax_usage_plugin.py <socket_path> <config_json>")
        sys.exit(1)

    socket_path = sys.argv[1]
    try:
        config = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(f"Invalid config JSON: {e}")
        sys.exit(1)

    plugin = MiniMaxUsagePlugin(socket_path, config)
    plugin.run()


if __name__ == '__main__':
    main()
