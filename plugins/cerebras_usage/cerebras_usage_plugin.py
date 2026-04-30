#!/usr/bin/env python3
"""Cerebras usage monitoring plugin for StreamDeck UI."""

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

USAGE_URL = "https://api.cerebras.ai/v1/usage"
DEFAULT_OPENCODE_AUTH = str(Path.home() / ".local" / "share" / "opencode" / "auth.json")


class CerebrasUsagePlugin(BasePlugin):
    """Plugin for monitoring Cerebras token quotas."""

    def __init__(self, socket_path: str, config: dict[str, Any]):
        super().__init__(socket_path, config)

        self.api_key = config.get('api_key', '')
        self.opencode_auth_path = config.get('opencode_auth_path', '') or DEFAULT_OPENCODE_AUTH
        self.poll_interval = max(int(config.get('poll_interval', 300)), 60)
        self.display_mode = config.get('display_mode', 'compact')
        self.rotate_interval = int(config.get('rotate_interval', 5))

        self.quota_sentinel_url = config.get('quota_sentinel_url', '').split('/v1')[0].rstrip('/')
        self._sentinel_api_key = ''
        self._sentinel_instance_id = ''

        # State: {window_name: utilization_percent}
        self.windows: dict[str, float] = {}
        self.last_poll_time = 0
        self.error_message: str | None = None
        self.has_data = False
        self.current_view = 0
        self.last_rotate_time = 0

    def _resolve_key(self) -> str:
        if self.api_key:
            return self.api_key
        try:
            with open(self.opencode_auth_path) as f:
                auth = json.load(f)
            for key_name in ('cerebras-coding-plan', 'cerebras'):
                entry = auth.get(key_name, {})
                if entry.get('key'):
                    self.log(LogLevel.INFO, f"Using API key from opencode auth ({key_name})")
                    return entry['key']
        except FileNotFoundError:
            self.log(LogLevel.WARNING, f"opencode auth not found: {self.opencode_auth_path}")
        except (json.JSONDecodeError, KeyError) as e:
            self.log(LogLevel.ERROR, f"Failed to read opencode auth: {e}")
        return ""

    def _sentinel_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._sentinel_api_key:
            headers['X-API-Key'] = self._sentinel_api_key
        return headers

    def _register_with_sentinel(self) -> bool:
        if self._sentinel_api_key:
            return True
        key = self._resolve_key()
        if not key:
            return False
        try:
            payload = {
                'project_name': 'streamdeck-cerebras',
                'framework': 'opencode',
                'auth': {'opencode_auth': {'cerebras': {'key': key}}},
            }
            resp = requests.post(f"{self.quota_sentinel_url}/v1/instances", json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self._sentinel_api_key = data.get('api_key', '')
            self._sentinel_instance_id = data.get('instance_id', '')
            self.log(LogLevel.INFO, f"Registered with Sentinel as {self._sentinel_instance_id}")
            return bool(self._sentinel_api_key)
        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Sentinel registration failed: {e}")
            return False

    def _provider_exists_in_sentinel(self, provider: str) -> bool:
        try:
            response = requests.get(f"{self.quota_sentinel_url}/v1/providers", headers=self._sentinel_headers(), timeout=10)
            if response.status_code == 401:
                self.log(LogLevel.WARNING, "Sentinel auth expired, will re-register")
                self._sentinel_api_key = ''
                return False
            response.raise_for_status()
            providers = response.json()
            if isinstance(providers, dict):
                return provider in providers
            return provider in [p.get('name', p) if isinstance(p, dict) else p for p in providers]
        except requests.exceptions.RequestException:
            return False

    def _fetch_from_sentinel(self) -> bool:
        try:
            if not self._register_with_sentinel():
                return False

            if not self._provider_exists_in_sentinel('cerebras'):
                self.log(LogLevel.INFO, "Provider 'cerebras' not registered in Sentinel")
                return False

            response = requests.get(f"{self.quota_sentinel_url}/v1/providers/cerebras", headers=self._sentinel_headers(), timeout=10)
            if response.status_code == 404:
                self.log(LogLevel.INFO, "Provider 'cerebras' not yet registered in Sentinel")
                return False
            response.raise_for_status()
            data = response.json()
            if data.get('error'):
                self.error_message = "Sentinel\nerror"
                return False
            windows = data.get('windows', {})
            self.windows = {name: float(w.get('utilization', 0) or 0) for name, w in windows.items()}
            self.has_data = True
            return True
        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch from Sentinel: {e}")
            self.error_message = "Sentinel\nerror"
            return False

    def _fetch_usage(self) -> bool:
        if self.quota_sentinel_url:
            return self._fetch_from_sentinel()

        key = self._resolve_key()
        if not key:
            self.error_message = "No\nAPI key"
            return False

        try:
            headers = {"Authorization": f"Bearer {key}"}
            response = requests.get(USAGE_URL, headers=headers, timeout=10)

            if response.status_code == 401:
                self.log(LogLevel.ERROR, "Cerebras auth failed (401)")
                self.error_message = "Auth\nfailed"
                return False
            if response.status_code == 429:
                self.log(LogLevel.WARNING, "Cerebras rate limited")
                self.error_message = "Rate\nlimited"
                return False

            response.raise_for_status()
            data = response.json()

            self.windows = {}
            for prefix, name in [('daily', 'daily'), ('weekly', 'weekly'),
                                 ('monthly', 'monthly'), ('subscription', 'subscription')]:
                used = data.get(f'{prefix}_tokens_used', 0)
                limit = data.get(f'{prefix}_tokens_limit', 0)
                if limit > 0:
                    self.windows[name] = min(used / limit * 100, 100)

            self.has_data = True
            return True

        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch Cerebras usage: {e}")
            self.error_message = "API\nerror"
            return False

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
            return (217, 70, 49)  # Cerebras red/orange

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
            if self.error_message and not self.has_data:
                self.update_image_render(
                    text=f"Cerebras\n{self.error_message}",
                    background_color="#B71C1C",
                    font_color="#FFFFFF",
                    font_size=10,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if not self.has_data:
                self.update_image_render(
                    text="Cerebras\n...",
                    background_color="#D94631",
                    font_color="#FFFFFF",
                    font_size=11,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if not self.windows:
                self.update_image_render(
                    text="Cerebras\nNo data",
                    background_color="#37474F",
                    font_color="#FFFFFF",
                    font_size=10,
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

    def _ordered_windows(self) -> list[tuple[str, float]]:
        priority = ['daily', 'weekly', 'monthly', 'subscription']
        ordered = [(n, self.windows[n]) for n in priority if n in self.windows]
        ordered += [(n, p) for n, p in self.windows.items() if n not in priority]
        return ordered

    def _render_compact_view(self) -> None:
        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_label = self._load_font(8)

        # Status indicator
        max_pct = max(self.windows.values()) if self.windows else 0
        status_color = self._get_bar_color(max_pct)
        draw.ellipse([2, 2, 8, 8], fill=status_color)

        draw.text((12, 2), "Cerebras", fill=(255, 255, 255), font=font_title)

        ordered = self._ordered_windows()
        y = 16
        for name, pct in ordered[:2]:
            label = name[:5].upper()
            draw.text((4, y), label, fill=(180, 180, 180), font=font_label)
            draw.text((68, y), f"{pct:.0f}%", fill=(255, 255, 255), font=font_label, anchor="rt")
            self._draw_bar(draw, 4, y + 11, 64, 6, pct)
            y += 22

        self.update_image_raw(img)

    def _render_rotate_view(self) -> None:
        ordered = self._ordered_windows()
        if not ordered:
            return

        idx = self.current_view % len(ordered)
        name, pct = ordered[idx]

        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_label = self._load_font(11)
        font_value = self._load_font(18)

        max_pct = max(self.windows.values()) if self.windows else 0
        status_color = self._get_bar_color(max_pct)
        draw.ellipse([2, 2, 8, 8], fill=status_color)

        draw.text((12, 2), "Cerebras", fill=(255, 255, 255), font=font_title)
        draw.text((36, 18), name.capitalize(), fill=(180, 180, 180), font=font_label, anchor="mt")

        color = self._get_bar_color(pct)
        draw.text((36, 34), f"{pct:.0f}%", fill=color, font=font_value, anchor="mt")

        self._draw_bar(draw, 4, 60, 64, 8, pct)

        self.update_image_raw(img)

    def on_start(self) -> None:
        self.log(LogLevel.INFO, "Cerebras Usage plugin started")
        self.log(LogLevel.INFO, f"Poll interval: {self.poll_interval}s, Display: {self.display_mode}")
        self._update_display()

    def on_button_pressed(self) -> None:
        self.log(LogLevel.INFO, "Button pressed, forcing usage refresh")
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
        self.quota_sentinel_url = config.get('quota_sentinel_url', '').split('/v1')[0].rstrip('/')
        self._sentinel_api_key = ''
        self._sentinel_instance_id = ''
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

        if self.display_mode == 'rotate' and self.has_data and self.windows:
            if current_time - self.last_rotate_time >= self.rotate_interval:
                self.current_view += 1
                self.last_rotate_time = current_time
                self._update_display()


def main():
    if len(sys.argv) < 3:
        print("Usage: cerebras_usage_plugin.py <socket_path> <config_json>")
        sys.exit(1)

    socket_path = sys.argv[1]
    try:
        config = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(f"Invalid config JSON: {e}")
        sys.exit(1)

    plugin = CerebrasUsagePlugin(socket_path, config)
    plugin.run()


if __name__ == '__main__':
    main()
