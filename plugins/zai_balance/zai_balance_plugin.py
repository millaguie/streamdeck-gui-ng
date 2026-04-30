#!/usr/bin/env python3
"""Z.ai (ZhipuAI) pay-as-you-go balance monitoring plugin for StreamDeck UI.

Shows remaining API balance in CNY from the undocumented finance endpoint.

ZhipuAI uses JWT-based authentication: the API key format is '{id}.{secret}'
and a short-lived JWT token must be generated for each request.

References:
  - API docs: https://open.bigmodel.cn/dev/api
  - Finance endpoint is undocumented, discovered from the web console at
    open.bigmodel.cn. It may change without notice.
"""

import base64
import hashlib
import hmac
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

BALANCE_URL = "https://open.bigmodel.cn/api/paas/v4/finance/balance"
DEFAULT_OPENCODE_AUTH = str(Path.home() / ".local" / "share" / "opencode" / "auth.json")


class ZaiBalancePlugin(BasePlugin):
    """Plugin for monitoring Z.ai pay-as-you-go API balance."""

    def __init__(self, socket_path: str, config: dict[str, Any]):
        super().__init__(socket_path, config)

        self.api_key = config.get('api_key', '')
        self.opencode_auth_path = config.get('opencode_auth_path', '') or DEFAULT_OPENCODE_AUTH
        self.poll_interval = max(int(config.get('poll_interval', 600)), 60)

        self.quota_sentinel_url = config.get('quota_sentinel_url', '').split('/v1')[0].rstrip('/')
        self._sentinel_api_key = ''
        self._sentinel_instance_id = ''

        # State
        self.last_poll_time = 0
        self.balance: float | None = None
        self.currency: str = "CNY"
        self.error_message: str | None = None

    def _resolve_key(self) -> str:
        """Get API key from config or opencode auth.json."""
        if self.api_key:
            return self.api_key
        try:
            with open(self.opencode_auth_path) as f:
                auth = json.load(f)
            # Use 'zai' (pay-as-you-go), not 'zai-coding-plan'
            entry = auth.get('zai', {})
            if entry.get('key'):
                self.log(LogLevel.INFO, "Using API key from opencode auth (zai)")
                return entry['key']
        except FileNotFoundError:
            self.log(LogLevel.WARNING, f"opencode auth not found: {self.opencode_auth_path}")
        except (json.JSONDecodeError, KeyError) as e:
            self.log(LogLevel.ERROR, f"Failed to read opencode auth: {e}")
        return ""

    def _generate_jwt(self, api_key: str) -> str:
        """Generate a short-lived JWT token from the Z.ai API key.

        ZhipuAI uses HS256 JWT with a custom 'sign_type' header.
        Implemented without PyJWT dependency using stdlib hmac.
        """
        api_id, api_secret = api_key.split(".", 1)
        now = int(time.time())

        def b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

        header = b64url(json.dumps({"alg": "HS256", "sign_type": "SIGN", "typ": "JWT"}).encode())
        payload = b64url(json.dumps({"api_key": api_id, "exp": now + 300, "timestamp": now}).encode())
        signature = b64url(hmac.new(api_secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())

        return f"{header}.{payload}.{signature}"

    def _sentinel_headers(self) -> dict[str, str]:
        """Build headers for Quota Sentinel requests."""
        headers: dict[str, str] = {}
        if self._sentinel_api_key:
            headers['X-API-Key'] = self._sentinel_api_key
        return headers

    def _register_with_sentinel(self) -> bool:
        """Auto-register with Quota Sentinel and obtain API key."""
        if self._sentinel_api_key:
            return True
        key = self._resolve_key()
        if not key:
            return False
        try:
            payload = {
                'project_name': 'streamdeck-zai-balance',
                'framework': 'opencode',
                'auth': {'opencode_auth': {'zai': {'key': key}}},
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
        """Check if a provider is registered in Quota Sentinel."""
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
        """Fetch balance data from Quota Sentinel."""
        try:
            if not self._register_with_sentinel():
                return False

            if not self._provider_exists_in_sentinel('zai-balance'):
                self.log(LogLevel.INFO, "Provider 'zai-balance' not registered in Sentinel")
                return False

            response = requests.get(f"{self.quota_sentinel_url}/v1/providers/zai-balance", headers=self._sentinel_headers(), timeout=10)
            if response.status_code == 404:
                self.log(LogLevel.INFO, "Provider 'zai-balance' not yet registered in Sentinel")
                return False
            response.raise_for_status()
            data = response.json()
            if data.get('error'):
                self.error_message = "Sentinel\nerror"
                return False
            windows = data.get('windows', {})
            balance_w = windows.get('balance', {})
            pct = balance_w.get('utilization', 0) or 0
            self.balance = round(100 - pct, 2)
            self.currency = 'CNY'
            return True
        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch from Sentinel: {e}")
            self.error_message = "Sentinel\nerror"
            return False

    def _fetch_balance(self) -> bool:
        """Fetch balance from Z.ai finance endpoint or Quota Sentinel."""
        if self.quota_sentinel_url:
            return self._fetch_from_sentinel()

        key = self._resolve_key()
        if not key:
            self.error_message = "No\nkey"
            return False

        if '.' not in key:
            self.error_message = "Bad\nkey"
            self.log(LogLevel.ERROR, "API key must be in format 'id.secret'")
            return False

        try:
            token = self._generate_jwt(key)
            response = requests.get(
                BALANCE_URL,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )

            if response.status_code == 401:
                self.error_message = "Auth\nfailed"
                self.log(LogLevel.ERROR, "Z.ai auth failed (401)")
                return False
            if response.status_code == 429:
                self.error_message = "Rate\nlimit"
                self.log(LogLevel.WARNING, "Z.ai rate limited")
                return False

            response.raise_for_status()
            data = response.json()

            balance_str = data.get('balance')
            if balance_str is not None:
                self.balance = float(balance_str)
                self.currency = data.get('currency', 'CNY')
                self.log(LogLevel.INFO, f"Z.ai balance: {self.balance} {self.currency}")
                return True

            self.log(LogLevel.ERROR, f"Unexpected response format: {data}")
            self.error_message = "Parse\nerror"
            return False

        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch Z.ai balance: {e}")
            self.error_message = "API\nerror"
            return False
        except Exception as e:
            self.log(LogLevel.ERROR, f"Error: {e}")
            self.error_message = "Error"
            return False

    def _get_bar_color(self, balance: float) -> tuple[int, int, int]:
        """Color based on absolute balance level."""
        if balance <= 1:
            return (183, 28, 28)      # Red - critical
        elif balance <= 5:
            return (230, 81, 0)       # Orange - low
        elif balance <= 20:
            return (245, 127, 23)     # Yellow
        elif balance <= 50:
            return (27, 94, 32)       # Green
        else:
            return (21, 101, 192)     # Blue - plenty

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

    def _update_display(self) -> None:
        try:
            if self.error_message and self.balance is None:
                self.update_image_render(
                    text=f"Z.ai\n{self.error_message}",
                    background_color="#B71C1C",
                    font_color="#FFFFFF",
                    font_size=11,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if self.balance is None:
                self.update_image_render(
                    text="Z.ai\n...",
                    background_color="#37474F",
                    font_color="#FFFFFF",
                    font_size=12,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            self._render_balance()

        except Exception as e:
            self.log(LogLevel.ERROR, f"Display update failed: {e}")

    def _render_balance(self) -> None:
        """Render balance display."""
        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_big = self._load_font(16)
        font_small = self._load_font(10)

        color = self._get_bar_color(self.balance)

        # Title
        draw.text((36, 6), "Z.ai API", fill=(255, 255, 255), font=font_title, anchor="mt")

        # Balance amount
        if self.balance >= 100:
            balance_text = f"{self.balance:.0f}"
        elif self.balance >= 10:
            balance_text = f"{self.balance:.1f}"
        else:
            balance_text = f"{self.balance:.2f}"

        draw.text((36, 30), balance_text, fill=color, font=font_big, anchor="mm")

        # Currency
        draw.text((36, 46), self.currency, fill=(180, 180, 180), font=font_small, anchor="mt")

        # Status indicator bar at bottom
        draw.rectangle([4, 62, 68, 68], fill=(30, 30, 30), outline=(80, 80, 80))
        draw.rectangle([5, 63, 67, 67], fill=color)

        self.update_image_raw(img)

    def on_start(self) -> None:
        self.log(LogLevel.INFO, "Z.ai Balance plugin started")
        self.log(LogLevel.INFO, f"Poll interval: {self.poll_interval}s")
        self._update_display()

    def on_button_pressed(self) -> None:
        self.log(LogLevel.INFO, "Button pressed, forcing refresh")
        if self._fetch_balance():
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
        self.poll_interval = max(int(config.get('poll_interval', 600)), 60)
        self.quota_sentinel_url = config.get('quota_sentinel_url', '').split('/v1')[0].rstrip('/')
        self._sentinel_api_key = ''
        self._sentinel_instance_id = ''
        self.log(LogLevel.INFO, "Configuration updated")
        if self._fetch_balance():
            self.error_message = None
        self.last_poll_time = time.time()
        self._update_display()

    def update(self) -> None:
        current_time = time.time()
        if current_time - self.last_poll_time >= self.poll_interval:
            if self._fetch_balance():
                self.error_message = None
            self.last_poll_time = current_time
            self._update_display()


def main():
    if len(sys.argv) < 3:
        print("Usage: zai_balance_plugin.py <socket_path> <config_json>")
        sys.exit(1)

    socket_path = sys.argv[1]
    try:
        config = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(f"Invalid config JSON: {e}")
        sys.exit(1)

    plugin = ZaiBalancePlugin(socket_path, config)
    plugin.run()


if __name__ == '__main__':
    main()
