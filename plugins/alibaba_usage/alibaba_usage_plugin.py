#!/usr/bin/env python3
"""Alibaba Cloud Coding Plan (Tongyi Lingma) usage monitoring plugin for StreamDeck UI.

This plugin uses an undocumented console RPC endpoint to query Coding Plan quota.
There is no official public API for individual plan usage monitoring.

The endpoint and authentication method were reverse-engineered by the CodexBar project:
  - Repository: https://github.com/steipete/CodexBar
  - PR with implementation: https://github.com/steipete/CodexBar/pull/453
  - Feature request: https://github.com/steipete/CodexBar/issues/418
  - Documentation: https://github.com/steipete/CodexBar/blob/main/docs/alibaba-coding-plan.md

Official Alibaba Cloud references:
  - Coding Plan pricing & quotas: https://www.alibabacloud.com/help/en/model-studio/coding-plan
  - Coding Plan FAQ: https://www.alibabacloud.com/help/en/model-studio/coding-plan-faq

Known limitations:
  - This is an undocumented internal API that could change without notice.
  - Some China mainland accounts may return 'ConsoleNeedLogin' errors with API key auth
    and require browser cookie-based authentication instead (not supported by this plugin).
"""

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

# Undocumented console RPC endpoint
# Reverse-engineered by CodexBar: https://github.com/steipete/CodexBar/pull/453
REGIONS = {
    "intl": {
        "host": "https://modelstudio.console.alibabacloud.com",
        "region_id": "ap-southeast-1",
        "commodity_code": "sfm_codingplan_public_intl",
    },
    "cn": {
        "host": "https://bailian.console.aliyun.com",
        "region_id": "cn-beijing",
        "commodity_code": "sfm_codingplan_public_cn",
    },
}
DEFAULT_OPENCODE_AUTH = str(Path.home() / ".local" / "share" / "opencode" / "auth.json")


class AlibabaCodingPlanPlugin(BasePlugin):
    """Plugin for monitoring Alibaba Cloud Coding Plan consumption."""

    def __init__(self, socket_path: str, config: dict[str, Any]):
        super().__init__(socket_path, config)

        self.api_key = config.get('api_key', '')
        self.region = config.get('region', 'intl')
        self.opencode_auth_path = config.get('opencode_auth_path', '') or DEFAULT_OPENCODE_AUTH
        self.poll_interval = max(int(config.get('poll_interval', 300)), 60)
        self.display_mode = config.get('display_mode', 'compact')
        self.rotate_interval = int(config.get('rotate_interval', 5))

        # State
        self.last_poll_time = 0
        self.quotas: list[dict[str, Any]] = []  # [{label, used, total, reset_time}, ...]
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
            for key_name in ('bailian-coding-plan', 'alibaba-coding-plan', 'dashscope', 'alibaba'):
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
        """Fetch coding plan quota from Alibaba console RPC endpoint."""
        key = self._resolve_key()
        if not key:
            self.error_message = "No\nkey"
            return False

        region_cfg = REGIONS.get(self.region, REGIONS["intl"])

        url = (
            f"{region_cfg['host']}/data/api.json"
            f"?action=zeldaEasy.broadscope-bailian.codingPlan.queryCodingPlanInstanceInfoV2"
            f"&product=broadscope-bailian"
            f"&api=queryCodingPlanInstanceInfoV2"
            f"&currentRegionId={region_cfg['region_id']}"
        )

        body = {
            "queryCodingPlanInstanceInfoRequest": {
                "commodityCode": region_cfg["commodity_code"],
            }
        }

        try:
            response = requests.post(
                url,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {key}",
                    "x-api-key": key,
                    "X-DashScope-API-Key": key,
                },
                timeout=15,
            )

            if response.status_code == 401:
                self.error_message = "Auth\nfailed"
                self.log(LogLevel.ERROR, "Alibaba auth failed (401)")
                return False
            if response.status_code == 429:
                self.error_message = "Rate\nlimit"
                self.log(LogLevel.WARNING, "Alibaba rate limited")
                return False

            response.raise_for_status()
            data = response.json()

            # Check for ConsoleNeedLogin or other errors
            if data.get('code') == 'ConsoleNeedLogin':
                self.error_message = "Login\nneeded"
                self.log(LogLevel.ERROR, "Alibaba: ConsoleNeedLogin - API key mode not supported for this account")
                return False

            # Navigate nested response to find quota info
            quota_info = self._extract_quota_info(data)
            if not quota_info:
                self.log(LogLevel.ERROR, f"Could not extract quota info from response: {json.dumps(data)[:500]}")
                self.error_message = "Parse\nerror"
                return False

            self.quotas = quota_info
            self.log(LogLevel.INFO, f"Alibaba: got {len(self.quotas)} quota window(s)")
            return True

        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch Alibaba usage: {e}")
            self.error_message = "API\nerror"
            return False

    def _extract_quota_info(self, data: dict) -> list[dict[str, Any]]:
        """Extract quota windows from the nested API response."""
        quotas = []

        # The response nests data in various ways; try to find codingPlanQuotaInfo
        # Response structure: data -> codingPlanInstanceInfos[0] -> codingPlanQuotaInfo
        try:
            # Try direct data path
            instances = None
            if 'data' in data:
                d = data['data']
                if isinstance(d, dict):
                    instances = d.get('codingPlanInstanceInfos') or d.get('result', {}).get('codingPlanInstanceInfos')
            if not instances and 'codingPlanInstanceInfos' in data:
                instances = data['codingPlanInstanceInfos']

            # Also try under result
            if not instances:
                result = data.get('result', data.get('data', {}).get('result', {}))
                if isinstance(result, dict):
                    instances = result.get('codingPlanInstanceInfos')

            if not instances or not isinstance(instances, list) or len(instances) == 0:
                self.log(LogLevel.WARNING, f"No codingPlanInstanceInfos found. Keys: {list(data.keys())}")
                # Try flat response
                return self._extract_flat_quota(data)

            instance = instances[0]
            quota_info = instance.get('codingPlanQuotaInfo', instance)

            # Extract the three quota windows
            window_configs = [
                ("5h", "per5Hour"),
                ("Week", "perWeek"),
                ("Month", "perBillMonth"),
            ]

            for label, prefix in window_configs:
                used = quota_info.get(f'{prefix}UsedQuota')
                total = quota_info.get(f'{prefix}TotalQuota')
                reset_time = quota_info.get(f'{prefix}QuotaNextRefreshTime')

                if total is not None and used is not None:
                    quotas.append({
                        "label": label,
                        "used": int(used),
                        "total": int(total),
                        "reset_time": reset_time,
                    })

        except Exception as e:
            self.log(LogLevel.ERROR, f"Error extracting quota: {e}")

        return quotas

    def _extract_flat_quota(self, data: dict) -> list[dict[str, Any]]:
        """Try to extract quota from a flat response structure."""
        quotas = []
        # Search all nested dicts for quota fields
        def search(obj: Any, depth: int = 0) -> None:
            if depth > 5 or not isinstance(obj, dict):
                return
            if 'per5HourTotalQuota' in obj:
                for label, prefix in [("5h", "per5Hour"), ("Week", "perWeek"), ("Month", "perBillMonth")]:
                    used = obj.get(f'{prefix}UsedQuota')
                    total = obj.get(f'{prefix}TotalQuota')
                    reset_time = obj.get(f'{prefix}QuotaNextRefreshTime')
                    if total is not None and used is not None:
                        quotas.append({"label": label, "used": int(used), "total": int(total), "reset_time": reset_time})
                return
            for v in obj.values():
                if isinstance(v, dict):
                    search(v, depth + 1)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            search(item, depth + 1)

        search(data)
        return quotas

    def _format_reset_time(self, reset_time: Any) -> str:
        """Format reset time (ISO string or epoch ms) as relative string."""
        if not reset_time:
            return "?"
        try:
            if isinstance(reset_time, (int, float)):
                # Epoch milliseconds
                reset_dt = datetime.fromtimestamp(reset_time / 1000, tz=timezone.utc)
            else:
                # ISO string
                reset_dt = datetime.fromisoformat(str(reset_time))
                if reset_dt.tzinfo is None:
                    reset_dt = reset_dt.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            delta = reset_dt - now
            total_seconds = int(delta.total_seconds())

            if total_seconds <= 0:
                return "now"

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
        except Exception:
            return "?"

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
            if self.error_message and not self.quotas:
                self.update_image_render(
                    text=f"Alibaba\n{self.error_message}",
                    background_color="#B71C1C",
                    font_color="#FFFFFF",
                    font_size=11,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if not self.quotas:
                self.update_image_render(
                    text="Alibaba\n...",
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
        """Render compact view showing quota windows with progress bars."""
        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)

        n = min(len(self.quotas), 2)
        section_h = 72 // n
        font_label = self._load_font(11)
        font_small = self._load_font(9)

        for i, q in enumerate(self.quotas[:n]):
            total = q['total']
            used = q['used']
            pct = (used / total * 100) if total > 0 else 0
            reset = self._format_reset_time(q.get('reset_time'))
            label = q['label']

            y_base = i * section_h

            draw.text((4, y_base + 2), f"{label} {pct:.0f}%", fill=(255, 255, 255), font=font_label)
            self._draw_bar(draw, 4, y_base + 16, 64, 8, pct)
            draw.text((4, y_base + 26), reset, fill=(180, 180, 180), font=font_small)

        self.update_image_raw(img)

    def _render_rotate_view(self) -> None:
        """Render rotating views cycling through quota windows."""
        if not self.quotas:
            return

        idx = self.current_view % len(self.quotas)
        q = self.quotas[idx]

        total = q['total']
        used = q['used']
        pct = (used / total * 100) if total > 0 else 0
        reset = self._format_reset_time(q.get('reset_time'))
        label = q['label']

        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_label = self._load_font(14)
        font_mid = self._load_font(11)
        font_small = self._load_font(9)

        draw.text((36, 6), label, fill=(255, 255, 255), font=font_label, anchor="mt")
        draw.text((36, 22), f"{used}/{total}", fill=(255, 255, 255), font=font_mid, anchor="mt")
        self._draw_bar(draw, 4, 38, 64, 10, pct)
        draw.text((36, 41), f"{pct:.0f}%", fill=(255, 255, 255), font=font_small, anchor="mm")
        draw.text((36, 56), reset, fill=(180, 180, 180), font=font_small, anchor="mt")

        self.update_image_raw(img)

    def on_start(self) -> None:
        self.log(LogLevel.INFO, f"Alibaba Coding Plan plugin started, Region: {self.region}")
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
        self.region = config.get('region', 'intl')
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

        if self.display_mode == 'rotate' and self.quotas:
            if current_time - self.last_rotate_time >= self.rotate_interval:
                self.current_view += 1
                self.last_rotate_time = current_time
                self._update_display()


def main():
    if len(sys.argv) < 3:
        print("Usage: alibaba_usage_plugin.py <socket_path> <config_json>")
        sys.exit(1)

    socket_path = sys.argv[1]
    try:
        config = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(f"Invalid config JSON: {e}")
        sys.exit(1)

    plugin = AlibabaCodingPlanPlugin(socket_path, config)
    plugin.run()


if __name__ == '__main__':
    main()
