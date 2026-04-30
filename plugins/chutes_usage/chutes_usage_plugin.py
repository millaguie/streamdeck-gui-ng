#!/usr/bin/env python3
"""Chutes.ai balance/usage monitoring plugin for StreamDeck UI."""

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

CHUTES_ME_URL = "https://api.chutes.ai/users/me"
CHUTES_QUOTA_URL = "https://api.chutes.ai/users/me/quota_usage"
DEFAULT_OPENCODE_AUTH = str(Path.home() / ".local" / "share" / "opencode" / "auth.json")


class ChutesUsagePlugin(BasePlugin):
    """Plugin for monitoring Chutes.ai account balance and quota."""

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

        # State
        self.balance: float | None = None
        self.username: str | None = None
        self.quota_used: float | None = None
        self.quota_limit: float | None = None
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
            for key_name in ('chutes-coding-plan', 'chutes'):
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
                'project_name': 'streamdeck-chutes',
                'framework': 'opencode',
                'auth': {'opencode_auth': {'chutes': {'key': key}}},
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

            if not self._provider_exists_in_sentinel('chutes'):
                self.log(LogLevel.INFO, "Provider 'chutes' not registered in Sentinel")
                return False

            response = requests.get(f"{self.quota_sentinel_url}/v1/providers/chutes", headers=self._sentinel_headers(), timeout=10)
            if response.status_code == 404:
                self.log(LogLevel.INFO, "Provider 'chutes' not yet registered in Sentinel")
                return False
            response.raise_for_status()
            data = response.json()
            if data.get('error'):
                self.error_message = "Sentinel\nerror"
                return False
            windows = data.get('windows', {})
            balance_w = windows.get('balance', {})
            meta = balance_w.get('metadata', {}) or {}
            self.balance = float(meta.get('balance', 0))
            self.username = meta.get('username')
            quota_w = windows.get('quota', {})
            qmeta = quota_w.get('metadata', {}) or {}
            if 'used' in qmeta and 'limit' in qmeta:
                self.quota_used = float(qmeta['used'])
                self.quota_limit = float(qmeta['limit'])
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

            # Get account info (balance, username)
            response = requests.get(CHUTES_ME_URL, headers=headers, timeout=10)
            if response.status_code == 401:
                self.log(LogLevel.ERROR, "Chutes auth failed (401)")
                self.error_message = "Auth\nfailed"
                return False
            if response.status_code == 429:
                self.log(LogLevel.WARNING, "Chutes rate limited")
                self.error_message = "Rate\nlimited"
                return False
            response.raise_for_status()
            me = response.json()

            self.balance = float(me.get('balance', 0))
            self.username = me.get('username')

            # Try to get quota usage (may not exist for free accounts)
            try:
                qresp = requests.get(CHUTES_QUOTA_URL, headers=headers, timeout=10)
                if qresp.status_code == 200:
                    qdata = qresp.json()
                    used = qdata.get('used') or qdata.get('quota_used') or 0
                    limit = qdata.get('limit') or qdata.get('quota_limit') or 0
                    if limit > 0:
                        self.quota_used = float(used)
                        self.quota_limit = float(limit)
            except requests.exceptions.RequestException:
                pass

            self.has_data = True
            self.log(LogLevel.INFO, f"Chutes balance: ${self.balance:.2f}")
            return True

        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch Chutes balance: {e}")
            self.error_message = "API\nerror"
            return False

    def _balance_color(self, balance: float) -> tuple[int, int, int]:
        if balance <= 0:
            return (183, 28, 28)
        elif balance < 1.0:
            return (230, 81, 0)
        elif balance < 5.0:
            return (245, 127, 23)
        else:
            return (124, 58, 237)  # Chutes purple

    def _bar_color(self, pct: float) -> tuple[int, int, int]:
        if pct >= 90:
            return (183, 28, 28)
        elif pct >= 75:
            return (230, 81, 0)
        elif pct >= 50:
            return (245, 127, 23)
        elif pct >= 25:
            return (27, 94, 32)
        else:
            return (124, 58, 237)

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
            color = self._bar_color(pct)
            draw.rectangle([x + 1, y + 1, x + fill_w, y + h - 1], fill=color)

    def _update_display(self) -> None:
        try:
            if self.error_message and not self.has_data:
                self.update_image_render(
                    text=f"Chutes\n{self.error_message}",
                    background_color="#B71C1C",
                    font_color="#FFFFFF",
                    font_size=11,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if not self.has_data:
                self.update_image_render(
                    text="Chutes\n...",
                    background_color="#7C3AED",
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
        balance = self.balance or 0
        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_value = self._load_font(14)
        font_small = self._load_font(9)

        # Status indicator
        status_color = self._balance_color(balance)
        draw.ellipse([2, 2, 8, 8], fill=status_color)

        # Title
        draw.text((12, 2), "Chutes", fill=(255, 255, 255), font=font_title)

        # Balance
        balance_color = self._balance_color(balance)
        draw.text((36, 18), f"${balance:.2f}", fill=balance_color, font=font_value, anchor="mt")

        # Quota bar (if quota info available)
        if self.quota_limit and self.quota_limit > 0:
            quota_pct = min((self.quota_used or 0) / self.quota_limit * 100, 100)
            draw.text((4, 38), "Quota", fill=(180, 180, 180), font=font_small)
            draw.text((68, 38), f"{quota_pct:.0f}%", fill=(255, 255, 255), font=font_small, anchor="rt")
            self._draw_bar(draw, 4, 50, 64, 8, quota_pct)
        elif self.username:
            draw.text((36, 50), str(self.username)[:12], fill=(180, 180, 180), font=font_small, anchor="mm")

        # Bottom: balance bar (relative to $10 reference)
        ref = max(balance, 10)
        bal_pct = min(balance / ref * 100, 100)
        self._draw_bar(draw, 4, 62, 64, 6, bal_pct)

        self.update_image_raw(img)

    def _render_rotate_view(self) -> None:
        balance = self.balance or 0
        views: list[tuple[str, str, tuple[int, int, int]]] = [
            ("Balance", f"${balance:.2f}", self._balance_color(balance)),
        ]
        if self.quota_limit and self.quota_limit > 0:
            quota_pct = min((self.quota_used or 0) / self.quota_limit * 100, 100)
            views.append(("Quota", f"{quota_pct:.0f}%", self._bar_color(quota_pct)))
        if self.username:
            views.append(("User", str(self.username)[:10], (180, 180, 180)))

        idx = self.current_view % len(views)
        label, value, color = views[idx]

        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_label = self._load_font(11)
        font_value = self._load_font(16)

        status_color = self._balance_color(balance)
        draw.ellipse([2, 2, 8, 8], fill=status_color)

        draw.text((12, 2), "Chutes", fill=(255, 255, 255), font=font_title)
        draw.text((36, 22), label, fill=(180, 180, 180), font=font_label, anchor="mt")
        draw.text((36, 40), value, fill=color, font=font_value, anchor="mt")

        self.update_image_raw(img)

    def on_start(self) -> None:
        self.log(LogLevel.INFO, "Chutes plugin started")
        self.log(LogLevel.INFO, f"Poll interval: {self.poll_interval}s, Display: {self.display_mode}")
        self._update_display()

    def on_button_pressed(self) -> None:
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

        if self.display_mode == 'rotate' and self.has_data:
            if current_time - self.last_rotate_time >= self.rotate_interval:
                self.current_view += 1
                self.last_rotate_time = current_time
                self._update_display()


def main():
    if len(sys.argv) < 3:
        print("Usage: chutes_usage_plugin.py <socket_path> <config_json>")
        sys.exit(1)

    socket_path = sys.argv[1]
    try:
        config = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(f"Invalid config JSON: {e}")
        sys.exit(1)

    plugin = ChutesUsagePlugin(socket_path, config)
    plugin.run()


if __name__ == '__main__':
    main()
