#!/usr/bin/env python3
"""GitHub Copilot usage monitoring plugin for StreamDeck UI."""

import json
import subprocess
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

PLAN_ALLOWANCES = {
    "free": 50,
    "pro": 300,
    "pro_plus": 1500,
}


class CopilotUsagePlugin(BasePlugin):
    """Plugin for monitoring GitHub Copilot premium request usage."""

    def __init__(self, socket_path: str, config: dict[str, Any]):
        super().__init__(socket_path, config)

        self.github_token = config.get('github_token', '')
        self.github_username = config.get('github_username', '')
        self.poll_interval = max(int(config.get('poll_interval', 600)), 60)

        plan = config.get('plan', 'pro')
        if plan in PLAN_ALLOWANCES:
            self.plan_allowance = PLAN_ALLOWANCES[plan]
        else:
            try:
                self.plan_allowance = int(plan)
            except ValueError:
                self.plan_allowance = 300

        # State
        self.last_poll_time = 0
        self.total_used = 0
        self.usage_by_model: dict[str, int] = {}
        self.error_message: str | None = None
        self.has_data = False

    def _resolve_token(self) -> str:
        """Get GitHub token from config or gh CLI."""
        if self.github_token:
            return self.github_token
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return ""

    def _fetch_usage(self) -> bool:
        """Fetch premium request usage from GitHub API."""
        token = self._resolve_token()
        if not token:
            self.error_message = "No\ntoken"
            return False

        if not self.github_username:
            self.error_message = "No\nuser"
            return False

        now = datetime.now(timezone.utc)
        url = f"https://api.github.com/users/{self.github_username}/settings/billing/premium_request/usage"

        try:
            response = requests.get(
                url,
                params={"year": now.year, "month": now.month},
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2026-03-10",
                },
                timeout=10,
            )

            if response.status_code == 401:
                self.error_message = "Auth\nfailed"
                self.log(LogLevel.ERROR, "GitHub auth failed (401)")
                return False
            if response.status_code == 403:
                self.error_message = "No\nperm"
                self.log(LogLevel.ERROR, "GitHub forbidden (403) - needs 'user' scope")
                return False
            if response.status_code == 429:
                self.error_message = "Rate\nlimit"
                self.log(LogLevel.WARNING, "GitHub rate limited")
                return False

            response.raise_for_status()
            data = response.json()

            # Sum up usage from all items
            self.total_used = 0
            self.usage_by_model = {}
            for item in data.get('usageItems', []):
                qty = item.get('grossQuantity', 0)
                model = item.get('model', 'unknown')
                self.total_used += qty
                self.usage_by_model[model] = self.usage_by_model.get(model, 0) + qty

            self.has_data = True
            self.log(LogLevel.INFO, f"Copilot usage: {self.total_used}/{self.plan_allowance} premium requests")
            return True

        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch Copilot usage: {e}")
            self.error_message = "API\nerror"
            return False

    def _days_until_reset(self) -> str:
        """Calculate days until the 1st of next month (UTC)."""
        now = datetime.now(timezone.utc)
        if now.month == 12:
            reset = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            reset = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)

        delta = reset - now
        days = delta.days
        hours = delta.seconds // 3600

        if days > 0:
            return f"{days}d{hours}h"
        else:
            return f"{hours}h"

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
            if self.error_message and not self.has_data:
                self.update_image_render(
                    text=f"Copilot\n{self.error_message}",
                    background_color="#B71C1C",
                    font_color="#FFFFFF",
                    font_size=11,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if not self.has_data:
                self.update_image_render(
                    text="Copilot\n...",
                    background_color="#37474F",
                    font_color="#FFFFFF",
                    font_size=12,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            self._render_view()

        except Exception as e:
            self.log(LogLevel.ERROR, f"Display update failed: {e}")

    def _render_view(self) -> None:
        """Render usage with progress bar."""
        pct = (self.total_used / self.plan_allowance * 100) if self.plan_allowance > 0 else 0
        remaining = max(0, self.plan_allowance - self.total_used)
        reset = self._days_until_reset()

        img = Image.new('RGB', (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_big = self._load_font(13)
        font_small = self._load_font(9)

        # Title
        draw.text((36, 4), "Copilot", fill=(255, 255, 255), font=font_title, anchor="mt")

        # Usage count
        draw.text((36, 18), f"{self.total_used}/{self.plan_allowance}", fill=(255, 255, 255), font=font_big, anchor="mt")

        # Progress bar
        self._draw_bar(draw, 4, 36, 64, 10, pct)

        # Percentage inside bar
        pct_text = f"{pct:.0f}%"
        draw.text((36, 41), pct_text, fill=(255, 255, 255), font=font_small, anchor="mm")

        # Reset time
        draw.text((36, 54), f"Reset: {reset}", fill=(180, 180, 180), font=font_small, anchor="mt")

        self.update_image_raw(img)

    def on_start(self) -> None:
        self.log(LogLevel.INFO, f"Copilot Usage plugin started for {self.github_username}")
        self.log(LogLevel.INFO, f"Plan allowance: {self.plan_allowance}, Poll: {self.poll_interval}s")
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
        self.github_token = config.get('github_token', '')
        self.github_username = config.get('github_username', '')
        self.poll_interval = max(int(config.get('poll_interval', 600)), 60)
        plan = config.get('plan', 'pro')
        if plan in PLAN_ALLOWANCES:
            self.plan_allowance = PLAN_ALLOWANCES[plan]
        else:
            try:
                self.plan_allowance = int(plan)
            except ValueError:
                self.plan_allowance = 300
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


def main():
    if len(sys.argv) < 3:
        print("Usage: copilot_usage_plugin.py <socket_path> <config_json>")
        sys.exit(1)

    socket_path = sys.argv[1]
    try:
        config = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(f"Invalid config JSON: {e}")
        sys.exit(1)

    plugin = CopilotUsagePlugin(socket_path, config)
    plugin.run()


if __name__ == '__main__':
    main()
