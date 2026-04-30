#!/usr/bin/env python3
"""Synthetic.new subscription quota monitor for StreamDeck UI."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont

# Add parent directory to path to import base plugin
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from streamdeck_ui.plugin_system.base_plugin import BasePlugin
from streamdeck_ui.plugin_system.protocol import LogLevel

QUOTAS_URL = "https://api.synthetic.new/v2/quotas"
DEFAULT_OPENCODE_AUTH = str(Path.home() / ".local" / "share" / "opencode" / "auth.json")


class SyntheticUsagePlugin(BasePlugin):
    """Plugin for monitoring Synthetic.new subscription quota."""

    def __init__(self, socket_path: str, config: dict[str, Any]):
        super().__init__(socket_path, config)

        self.api_key = config.get("api_key", "")
        self.opencode_auth_path = (
            config.get("opencode_auth_path", "") or DEFAULT_OPENCODE_AUTH
        )
        self.poll_interval = max(int(config.get("poll_interval", 300)), 60)
        self.display_mode = config.get("display_mode", "compact")
        self.rotate_interval = int(config.get("rotate_interval", 5))

        self.quota_sentinel_url = (
            config.get("quota_sentinel_url", "").split("/v1")[0].rstrip("/")
        )
        self._sentinel_api_key = ""
        self._sentinel_instance_id = ""

        # State
        self.last_poll_time = 0.0
        self.usage_data: dict[str, Any] | None = None
        self.error_message: str | None = None
        self.current_view = 0
        self.last_rotate_time = 0.0

    # ------------------------------------------------------------------ key

    def _resolve_key(self) -> str:
        """Get API key from config or opencode auth.json."""
        if self.api_key:
            return self.api_key
        try:
            with open(self.opencode_auth_path) as f:
                auth = json.load(f)
            entry = auth.get("synthetic") or {}
            if entry.get("key"):
                self.log(LogLevel.INFO, "Using API key from opencode auth (synthetic)")
                return str(entry["key"])
        except FileNotFoundError:
            self.log(
                LogLevel.WARNING,
                f"opencode auth not found: {self.opencode_auth_path}",
            )
        except (json.JSONDecodeError, KeyError) as e:
            self.log(LogLevel.ERROR, f"Failed to read opencode auth: {e}")
        return ""

    # -------------------------------------------------------------- sentinel

    def _sentinel_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._sentinel_api_key:
            headers["X-API-Key"] = self._sentinel_api_key
        return headers

    def _register_with_sentinel(self) -> bool:
        if self._sentinel_api_key:
            return True
        key = self._resolve_key()
        if not key:
            return False
        try:
            payload = {
                "project_name": "streamdeck-synthetic",
                "framework": "opencode",
                "auth": {"opencode_auth": {"synthetic": {"key": key}}},
            }
            resp = requests.post(
                f"{self.quota_sentinel_url}/v1/instances", json=payload, timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            self._sentinel_api_key = data.get("api_key", "")
            self._sentinel_instance_id = data.get("instance_id", "")
            self.log(
                LogLevel.INFO,
                f"Registered with Sentinel as {self._sentinel_instance_id}",
            )
            return bool(self._sentinel_api_key)
        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Sentinel registration failed: {e}")
            return False

    def _provider_exists_in_sentinel(self, provider: str) -> bool:
        try:
            response = requests.get(
                f"{self.quota_sentinel_url}/v1/providers",
                headers=self._sentinel_headers(),
                timeout=10,
            )
            if response.status_code == 401:
                self.log(
                    LogLevel.WARNING, "Sentinel auth expired, will re-register"
                )
                self._sentinel_api_key = ""
                return False
            response.raise_for_status()
            providers = response.json()
            if isinstance(providers, dict):
                return provider in providers
            return provider in [
                p.get("name", p) if isinstance(p, dict) else p for p in providers
            ]
        except requests.exceptions.RequestException:
            return False

    def _fetch_from_sentinel(self) -> bool:
        try:
            if not self._register_with_sentinel():
                return False
            if not self._provider_exists_in_sentinel("synthetic"):
                self.log(
                    LogLevel.INFO,
                    "Provider 'synthetic' not registered in Sentinel",
                )
                return False
            response = requests.get(
                f"{self.quota_sentinel_url}/v1/providers/synthetic",
                headers=self._sentinel_headers(),
                timeout=10,
            )
            if response.status_code == 404:
                return False
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                self.error_message = "Sentinel\nerror"
                return False
            windows = data.get("windows", {})
            sub_w = windows.get("subscription", {})
            meta = sub_w.get("metadata", {})
            self.usage_data = {
                "limit": meta.get("limit_requests", 0),
                "used": meta.get("used_requests", 0),
                "remaining": meta.get("remaining_requests", 0),
                "renews_at": sub_w.get("resets_at"),
                "utilization": sub_w.get("utilization", 0.0),
            }
            return True
        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch from Sentinel: {e}")
            self.error_message = "Sentinel\nerror"
            return False

    # ------------------------------------------------------------------ direct

    def _fetch_direct(self) -> bool:
        key = self._resolve_key()
        if not key:
            self.error_message = "No\nAPI key"
            return False
        try:
            response = requests.get(
                QUOTAS_URL, headers={"Authorization": f"Bearer {key}"}, timeout=10
            )
            if response.status_code == 401:
                self.error_message = "Auth\nfailed"
                return False
            if response.status_code == 429:
                self.error_message = "Rate\nlimited"
                return False
            response.raise_for_status()
            data = response.json()
            sub = data.get("subscription") or {}
            limit = float(sub.get("limit") or 0)
            used = float(sub.get("requests") or 0)
            self.usage_data = {
                "limit": int(limit),
                "used": int(used),
                "remaining": max(0, int(limit - used)),
                "renews_at": sub.get("renewsAt"),
                "utilization": (used / limit * 100.0) if limit > 0 else 0.0,
            }
            return True
        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch Synthetic quota: {e}")
            self.error_message = "API\nerror"
            return False

    def _fetch_usage(self) -> bool:
        if self.quota_sentinel_url:
            return self._fetch_from_sentinel()
        return self._fetch_direct()

    # ---------------------------------------------------------------- rendering

    @staticmethod
    def _bar_color(pct: float) -> tuple[int, int, int]:
        if pct >= 95:
            return (183, 28, 28)
        if pct >= 80:
            return (230, 81, 0)
        if pct >= 50:
            return (245, 127, 23)
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

    @staticmethod
    def _format_renewal(iso: str | None) -> str:
        if not iso:
            return "—"
        try:
            dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ValueError:
            return "—"
        now = datetime.now(timezone.utc)
        delta = dt - now
        secs = int(delta.total_seconds())
        if secs <= 0:
            return "now"
        if secs < 3600:
            return f"{secs // 60}m"
        if secs < 86400:
            return f"{secs // 3600}h"
        return f"{secs // 86400}d"

    def _update_display(self) -> None:
        try:
            if self.error_message and not self.usage_data:
                self.update_image_render(
                    text=f"Synthetic\n{self.error_message}",
                    background_color="#B71C1C",
                    font_color="#FFFFFF",
                    font_size=10,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return
            if not self.usage_data:
                self.update_image_render(
                    text="Synthetic\n…",
                    background_color="#37474F",
                    font_color="#FFFFFF",
                    font_size=11,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return
            if self.display_mode == "rotate":
                self._render_rotate()
            else:
                self._render_compact()
        except Exception as e:  # noqa: BLE001
            self.log(LogLevel.ERROR, f"Display update failed: {e}")

    def _render_compact(self) -> None:
        d = self.usage_data or {}
        limit = int(d.get("limit") or 0)
        used = int(d.get("used") or 0)
        pct = float(d.get("utilization") or 0.0)
        renew_label = self._format_renewal(d.get("renews_at"))

        img = Image.new("RGB", (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_value = self._load_font(13)
        font_small = self._load_font(9)

        # Status dot — green if <80%, orange if <95%, red otherwise
        dot = (76, 175, 80) if pct < 80 else (245, 127, 23) if pct < 95 else (183, 28, 28)
        draw.ellipse([2, 2, 8, 8], fill=dot)
        draw.text((12, 2), "Synthetic", fill=(255, 255, 255), font=font_title)

        pct_color = self._bar_color(pct)
        draw.text((36, 18), f"{pct:.0f}%", fill=pct_color, font=font_value, anchor="mt")

        draw.text(
            (4, 38), f"{used} / {limit}", fill=(180, 180, 180), font=font_small
        )
        draw.text(
            (4, 50), f"renews {renew_label}", fill=(180, 180, 180), font=font_small
        )

        # Utilization bar
        draw.rectangle([4, 63, 68, 69], fill=(30, 30, 30), outline=(80, 80, 80))
        fill_w = int(64 * min(pct, 100) / 100)
        if fill_w > 0:
            draw.rectangle([5, 64, 4 + fill_w, 68], fill=pct_color)

        self.update_image_raw(img)

    def _render_rotate(self) -> None:
        d = self.usage_data or {}
        limit = int(d.get("limit") or 0)
        used = int(d.get("used") or 0)
        remaining = int(d.get("remaining") or max(0, limit - used))
        pct = float(d.get("utilization") or 0.0)
        renew_label = self._format_renewal(d.get("renews_at"))

        views = [
            ("Used", f"{used}/{limit}"),
            ("Free", str(remaining)),
            ("Util", f"{pct:.0f}%"),
            ("Renew", renew_label),
        ]
        idx = self.current_view % len(views)
        label, value = views[idx]

        img = Image.new("RGB", (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_label = self._load_font(12)
        font_value = self._load_font(14)

        dot = (76, 175, 80) if pct < 80 else (245, 127, 23) if pct < 95 else (183, 28, 28)
        draw.ellipse([2, 2, 8, 8], fill=dot)
        draw.text((12, 2), "Synthetic", fill=(255, 255, 255), font=font_title)
        draw.text((36, 28), label, fill=(180, 180, 180), font=font_label, anchor="mt")
        draw.text(
            (36, 46), value, fill=self._bar_color(pct), font=font_value, anchor="mt"
        )

        self.update_image_raw(img)

    # ----------------------------------------------------------- lifecycle

    def on_start(self) -> None:
        self.log(LogLevel.INFO, "Synthetic Usage plugin started")
        self.log(
            LogLevel.INFO,
            f"Poll interval: {self.poll_interval}s, display: {self.display_mode}",
        )
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
        self.api_key = config.get("api_key", "")
        self.opencode_auth_path = (
            config.get("opencode_auth_path", "") or DEFAULT_OPENCODE_AUTH
        )
        self.poll_interval = max(int(config.get("poll_interval", 300)), 60)
        self.display_mode = config.get("display_mode", "compact")
        self.rotate_interval = int(config.get("rotate_interval", 5))
        self.quota_sentinel_url = (
            config.get("quota_sentinel_url", "").split("/v1")[0].rstrip("/")
        )
        self._sentinel_api_key = ""
        self._sentinel_instance_id = ""
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
        if self.display_mode == "rotate" and self.usage_data:
            if current_time - self.last_rotate_time >= self.rotate_interval:
                self.current_view += 1
                self.last_rotate_time = current_time
                self._update_display()


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: synthetic_usage_plugin.py <socket_path> <config_json>")
        sys.exit(1)
    socket_path = sys.argv[1]
    try:
        config = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(f"Invalid config JSON: {e}")
        sys.exit(1)
    plugin = SyntheticUsagePlugin(socket_path, config)
    plugin.run()


if __name__ == "__main__":
    main()
