#!/usr/bin/env python3
"""OpenAI usage monitoring plugin for StreamDeck UI."""

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

USAGE_URL = "https://api.openai.com/v1/usage"
WHAM_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
DEFAULT_OPENCODE_AUTH = str(Path.home() / ".local" / "share" / "opencode" / "auth.json")


class OpenAIUsagePlugin(BasePlugin):
    """Plugin for monitoring OpenAI subscription/usage quotas."""

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

        # Cached opencode auth entry (OAuth or API key)
        self._auth_kind = ''  # 'api' or 'oauth'
        self._oauth_access = ''
        self._oauth_refresh = ''
        self._oauth_expires = 0
        self._oauth_account_id = ''

        # State: {window_name: utilization_percent}
        self.windows: dict[str, float] = {}
        self.has_overage = False
        self.plan_type: str | None = None
        self.last_poll_time = 0
        self.error_message: str | None = None
        self.has_data = False
        self.current_view = 0
        self.last_rotate_time = 0

    def _resolve_key(self) -> str:
        """Get API key (or OAuth access token) from config or opencode auth.json.

        Populates self._auth_kind and OAuth fields when an OAuth entry is found.
        Returns the access token / API key string, or empty string if none.
        """
        if self.api_key:
            self._auth_kind = 'api'
            return self.api_key
        try:
            with open(self.opencode_auth_path) as f:
                auth = json.load(f)
            for key_name in ('openai-coding-plan', 'openai'):
                entry = auth.get(key_name, {})
                if not entry:
                    continue
                etype = entry.get('type', 'api')
                if etype == 'oauth':
                    access = entry.get('access', '')
                    if access:
                        self._auth_kind = 'oauth'
                        self._oauth_access = access
                        self._oauth_refresh = entry.get('refresh', '')
                        self._oauth_expires = int(entry.get('expires', 0) or 0)
                        self._oauth_account_id = entry.get('accountId', '')
                        self.log(LogLevel.INFO, f"Using OAuth access token from opencode ({key_name})")
                        return access
                elif entry.get('key'):
                    self._auth_kind = 'api'
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
        """Auto-register with Quota Sentinel and obtain API key."""
        if self._sentinel_api_key:
            return True
        key = self._resolve_key()
        if not key:
            return False
        try:
            if self._auth_kind == 'oauth':
                payload: dict[str, Any] = {
                    'project_name': 'streamdeck-openai',
                    'framework': 'opencode',
                    'auth': {
                        'openai_oauth': {
                            'access_token': self._oauth_access,
                            'refresh_token': self._oauth_refresh,
                            'expires_at': self._oauth_expires,
                            'account_id': self._oauth_account_id,
                        }
                    },
                }
            else:
                payload = {
                    'project_name': 'streamdeck-openai',
                    'framework': 'opencode',
                    'auth': {'opencode_auth': {'openai': {'key': key}}},
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
        """Fetch usage data from Quota Sentinel."""
        try:
            if not self._register_with_sentinel():
                return False

            if not self._provider_exists_in_sentinel('openai'):
                self.log(LogLevel.INFO, "Provider 'openai' not registered in Sentinel")
                return False

            response = requests.get(f"{self.quota_sentinel_url}/v1/providers/openai", headers=self._sentinel_headers(), timeout=10)
            if response.status_code == 404:
                self.log(LogLevel.INFO, "Provider 'openai' not yet registered in Sentinel")
                return False
            response.raise_for_status()
            data = response.json()
            if data.get('error'):
                self.error_message = "Sentinel\nerror"
                return False
            windows = data.get('windows', {})
            self.windows = {}
            self.has_overage = False
            self.plan_type = None
            for name, w in windows.items():
                pct = float(w.get('utilization', 0) or 0)
                meta = w.get('metadata') or {}
                if name in ('overage', 'limit_reached'):
                    self.has_overage = pct >= 100
                    continue
                if name == 'credits' and not meta.get('has_credits') and not meta.get('unlimited'):
                    continue  # skip credits window for plan-only users
                self.windows[name] = pct
                if not self.plan_type and meta.get('plan_type'):
                    self.plan_type = meta['plan_type']
            self.has_data = True
            return True
        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch from Sentinel: {e}")
            self.error_message = "Sentinel\nerror"
            return False

    def _fetch_wham_direct(self) -> bool:
        """Fetch usage directly from ChatGPT Codex WHAM endpoint (OAuth only).

        Delegates token refresh to opencode (which keeps auth.json up to date).
        Re-reads auth.json on every poll to pick up token rotations.
        On 401, re-reads once more in case opencode refreshed mid-flight.
        """
        # Always re-read in case opencode rotated the token since last poll
        self._oauth_access = ''
        self._resolve_key()
        if not self._oauth_access:
            self.error_message = "No\nOAuth"
            return False
        try:
            headers = {
                'Authorization': f"Bearer {self._oauth_access}",
                'Accept': 'application/json',
            }
            if self._oauth_account_id:
                headers['ChatGPT-Account-Id'] = self._oauth_account_id
            response = requests.get(WHAM_USAGE_URL, headers=headers, timeout=10)

            if response.status_code == 401:
                # Token may have just been refreshed by opencode; re-read and retry once
                self.log(LogLevel.INFO, "WHAM 401, re-reading opencode auth")
                self._oauth_access = ''
                self._resolve_key()
                if self._oauth_access:
                    headers['Authorization'] = f"Bearer {self._oauth_access}"
                    response = requests.get(WHAM_USAGE_URL, headers=headers, timeout=10)
                if response.status_code == 401:
                    self.error_message = "Auth\nfailed"
                    return False
            if response.status_code == 429:
                self.error_message = "Rate\nlimited"
                return False
            response.raise_for_status()
            data = response.json()

            self.windows = {}
            self.has_overage = False
            self.plan_type = data.get('plan_type')

            rl = data.get('rate_limit') or {}
            for win_key, win_name in (('primary_window', 'primary'), ('secondary_window', 'secondary')):
                w = rl.get(win_key)
                if not w:
                    continue
                pct = float(w.get('used_percent', 0) or 0)
                self.windows[win_name] = min(pct, 100.0)
            if rl.get('limit_reached'):
                self.has_overage = True

            credits = data.get('credits') or {}
            if credits.get('has_credits') or credits.get('unlimited'):
                bal = float(credits.get('balance', 0) or 0)
                self.windows['credits'] = 0.0 if credits.get('unlimited') else (100.0 if bal <= 0 else 0.0)

            self.has_data = True
            return True

        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch WHAM usage: {e}")
            self.error_message = "API\nerror"
            return False

    def _fetch_usage(self) -> bool:
        """Fetch usage. Priority: Sentinel → WHAM direct (OAuth) → /v1/usage (API key)."""
        if self.quota_sentinel_url:
            if self._fetch_from_sentinel():
                return True
            # Fall through to direct if sentinel doesn't have us registered yet

        # Resolve credentials (populates _auth_kind)
        key = self._resolve_key()
        if not key:
            self.error_message = "No\nAPI key"
            return False

        if self._auth_kind == 'oauth':
            return self._fetch_wham_direct()

        try:
            headers = {"Authorization": f"Bearer {key}"}
            response = requests.get(USAGE_URL, headers=headers, timeout=10)

            if response.status_code == 401:
                self.log(LogLevel.ERROR, "OpenAI auth failed (401)")
                self.error_message = "Auth\nfailed"
                return False
            if response.status_code == 429:
                self.log(LogLevel.WARNING, "OpenAI rate limited")
                self.error_message = "Rate\nlimited"
                return False

            response.raise_for_status()
            data = response.json()

            self.windows = {}
            self.has_overage = False

            sub_total = data.get('subscription_total', 0)
            sub_used = data.get('subscription_used', 0)
            if sub_total > 0:
                self.windows['subscription'] = min(sub_used / sub_total * 100, 100)

            usage_total = data.get('usage_total', 0)
            usage_used = data.get('usage_used', 0)
            if usage_total > 0:
                self.windows['usage'] = min(usage_used / usage_total * 100, 100)

            if data.get('has_any_overage', False):
                self.has_overage = True

            self.has_data = True
            return True

        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch OpenAI usage: {e}")
            self.error_message = "API\nerror"
            return False

    def _get_bar_color(self, pct: float) -> tuple[int, int, int]:
        """Get bar fill color based on utilization percentage."""
        if pct >= 90:
            return (183, 28, 28)
        elif pct >= 75:
            return (230, 81, 0)
        elif pct >= 50:
            return (245, 127, 23)
        elif pct >= 25:
            return (27, 94, 32)
        else:
            return (16, 163, 127)  # OpenAI green

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
                    text=f"OpenAI\n{self.error_message}",
                    background_color="#B71C1C",
                    font_color="#FFFFFF",
                    font_size=11,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if not self.has_data:
                self.update_image_render(
                    text="OpenAI\n...",
                    background_color="#10A37F",
                    font_color="#FFFFFF",
                    font_size=12,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if not self.windows:
                self.update_image_render(
                    text="OpenAI\nNo data",
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
        """Compact view: title + up to 2 windows with bars."""
        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_label = self._load_font(8)

        # Status indicator (red dot if overage, green dot otherwise)
        status_color = (183, 28, 28) if self.has_overage else (16, 163, 127)
        draw.ellipse([2, 2, 8, 8], fill=status_color)

        # Title
        draw.text((12, 2), "OpenAI", fill=(255, 255, 255), font=font_title)

        # Sort windows: primary (5h) first, then secondary (7d), then others
        priority = ['primary', 'secondary', 'subscription', 'usage', 'credits']
        ordered = [(n, self.windows[n]) for n in priority if n in self.windows]
        ordered += [(n, p) for n, p in self.windows.items() if n not in priority]

        # Friendly labels for the well-known windows
        labels = {'primary': '5H', 'secondary': '7D', 'subscription': 'SUB',
                  'usage': 'USE', 'credits': 'CR'}

        # Show up to 2 windows
        y = 16
        for name, pct in ordered[:2]:
            label = labels.get(name, name[:4].upper())
            draw.text((4, y), label, fill=(180, 180, 180), font=font_label)
            draw.text((68, y), f"{pct:.0f}%", fill=(255, 255, 255), font=font_label, anchor="rt")
            self._draw_bar(draw, 4, y + 11, 64, 6, pct)
            y += 22

        # Overage indicator
        if self.has_overage:
            draw.text((36, 62), "OVERAGE", fill=(255, 100, 100), font=font_label, anchor="mm")

        self.update_image_raw(img)

    def _render_rotate_view(self) -> None:
        """Rotating view: cycles through each window."""
        priority = ['primary', 'secondary', 'subscription', 'usage', 'credits']
        ordered = [(n, self.windows[n]) for n in priority if n in self.windows]
        ordered += [(n, p) for n, p in self.windows.items() if n not in priority]

        if not ordered:
            return

        idx = self.current_view % len(ordered)
        name, pct = ordered[idx]

        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_label = self._load_font(11)
        font_value = self._load_font(18)

        status_color = (183, 28, 28) if self.has_overage else (16, 163, 127)
        draw.ellipse([2, 2, 8, 8], fill=status_color)

        draw.text((12, 2), "OpenAI", fill=(255, 255, 255), font=font_title)
        nice = {'primary': '5h window', 'secondary': '7d window'}.get(name, name.capitalize())
        draw.text((36, 18), nice, fill=(180, 180, 180), font=font_label, anchor="mt")

        color = self._get_bar_color(pct)
        draw.text((36, 34), f"{pct:.0f}%", fill=color, font=font_value, anchor="mt")

        self._draw_bar(draw, 4, 60, 64, 8, pct)

        self.update_image_raw(img)

    def on_start(self) -> None:
        self.log(LogLevel.INFO, "OpenAI Usage plugin started")
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
        print("Usage: openai_usage_plugin.py <socket_path> <config_json>")
        sys.exit(1)

    socket_path = sys.argv[1]
    try:
        config = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(f"Invalid config JSON: {e}")
        sys.exit(1)

    plugin = OpenAIUsagePlugin(socket_path, config)
    plugin.run()


if __name__ == '__main__':
    main()
