#!/usr/bin/env python3
"""OpenCode Go subscription usage monitoring plugin for StreamDeck UI.

OpenCode Go (https://opencode.ai/docs/go/) has no public usage API —
anomalyco/opencode#10448 and #18648 are still open upstream. The only
way to read the user's $/5h, weekly and monthly utilisation today is to
scrape the workspace dashboard (``https://opencode.ai/workspace/<id>/go``),
which embeds the numbers in a SolidJS SSR hydration payload.

The plugin has two modes:

1. **Sentinel mode** (preferred): set ``quota_sentinel_url`` and the
   plugin will read pre-aggregated usage from ``/v1/providers/opencode_go``.
   The sentinel handles workspace_id + auth_cookie centrally and shares
   them across every other consumer (Hefestia, web UI, etc.).

2. **Direct mode**: leave ``quota_sentinel_url`` empty and provide
   ``workspace_id`` + ``auth_cookie`` so the plugin scrapes the
   dashboard itself. Same regex as the slkiser/opencode-quota project.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from streamdeck_ui.plugin_system.base_plugin import BasePlugin  # noqa: E402
from streamdeck_ui.plugin_system.browser_cookies import CookieError, list_cookies  # noqa: E402
from streamdeck_ui.plugin_system.protocol import LogLevel  # noqa: E402

DASHBOARD_URL = "https://opencode.ai/workspace/{workspace_id}/go"
CONSOLE_STATUS_URL = "https://opencode.ai/console/api/go/status"
COOKIE_DOMAIN = "opencode.ai"
# __Host- prefixed cookie: exact-host only, list_cookies must see 'opencode.ai'
CONSOLE_COOKIE = "__Host-console_session"
_CHROMIUM_BROWSERS = ("chrome", "brave", "chromium")
_BROWSER_ORDER = ("firefox",) + _CHROMIUM_BROWSERS

# Console meter key -> window name used in self.windows/self.resets.
METER_WINDOWS = {
    "fiveHour": "rolling_5h",
    "week": "weekly",
    "month": "monthly",
}
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Regexes for the SolidJS SSR hydration payload (see slkiser/opencode-quota).
_NUM = r"(-?\d+(?:\.\d+)?)"


def _make_window_patterns(key: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    pct_first = re.compile(
        rf"{key}:\$R\[\d+\]=\{{[^}}]*usagePercent:{_NUM}[^}}]*resetInSec:{_NUM}[^}}]*\}}"
    )
    reset_first = re.compile(
        rf"{key}:\$R\[\d+\]=\{{[^}}]*resetInSec:{_NUM}[^}}]*usagePercent:{_NUM}[^}}]*\}}"
    )
    return pct_first, reset_first


_WINDOW_PATTERNS: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {
    "rolling_5h": _make_window_patterns("rollingUsage"),
    "weekly": _make_window_patterns("weeklyUsage"),
    "monthly": _make_window_patterns("monthlyUsage"),
}


def _parse_window(
    html: str, patterns: tuple[re.Pattern[str], re.Pattern[str]]
) -> tuple[float, float] | None:
    pct_first, reset_first = patterns
    m = pct_first.search(html)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except (TypeError, ValueError):
            pass
    m = reset_first.search(html)
    if m:
        try:
            return float(m.group(2)), float(m.group(1))
        except (TypeError, ValueError):
            pass
    return None


class OpencodeGoUsagePlugin(BasePlugin):
    """Plugin for monitoring OpenCode Go subscription utilisation."""

    def __init__(self, socket_path: str, config: dict[str, Any]):
        super().__init__(socket_path, config)

        self.workspace_id = (config.get("workspace_id") or "").strip()
        self.auth_cookie = (config.get("auth_cookie") or "").strip()
        self.browser = (config.get("browser") or "").strip().lower()
        self.cookie_domain = (config.get("cookie_domain") or COOKIE_DOMAIN).strip()
        self.poll_interval = max(int(config.get("poll_interval", 300)), 60)
        self.display_mode = config.get("display_mode", "compact")
        self.rotate_interval = int(config.get("rotate_interval", 5))

        self.quota_sentinel_url = (
            (config.get("quota_sentinel_url") or "").split("/v1")[0].rstrip("/")
        )
        self._sentinel_api_key = ""
        self._sentinel_instance_id = ""

        # State: {window_name: utilization_percent}
        self.windows: dict[str, float] = {}
        self.resets: dict[str, float] = {}  # seconds-until-reset
        self.last_poll_time = 0.0
        self.error_message: str | None = None
        self.has_data = False
        self.current_view = 0
        self.last_rotate_time = 0.0

    # ── Sentinel-mode helpers ──────────────────────────────────────────────

    def _sentinel_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._sentinel_api_key:
            headers["X-API-Key"] = self._sentinel_api_key
        return headers

    def _register_with_sentinel(self) -> bool:
        if self._sentinel_api_key:
            return True
        # Sentinel needs *something* to register against — we use the same
        # workspace_id+auth_cookie as direct mode and let the sentinel's
        # provider_config:opencode_go take precedence later if it exists.
        cookie = self._resolve_auth_cookie()
        if not self.workspace_id or not cookie:
            self.log(
                LogLevel.INFO,
                "Sentinel mode set but workspace_id/auth_cookie are empty — "
                "the sentinel needs them to seed registration",
            )
            return False
        try:
            payload = {
                "project_name": "streamdeck-opencode-go",
                "framework": "opencode",
                "auth": {"opencode_auth": {"opencode-go": {"key": "streamdeck"}}},
                "provider_config": {
                    "opencode_go": {
                        "workspace_id": self.workspace_id,
                        "auth_cookie": cookie,
                    }
                },
            }
            resp = requests.post(
                f"{self.quota_sentinel_url}/v1/instances",
                json=payload,
                timeout=10,
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

    def _fetch_from_sentinel(self) -> bool:
        try:
            if not self._register_with_sentinel():
                # Maybe another consumer already registered; query directly.
                pass
            response = requests.get(
                f"{self.quota_sentinel_url}/v1/providers/opencode_go",
                headers=self._sentinel_headers(),
                timeout=10,
            )
            if response.status_code == 404:
                self.error_message = "Not\nregistered"
                return False
            if response.status_code == 401:
                self.log(LogLevel.WARNING, "Sentinel auth expired, will re-register")
                self._sentinel_api_key = ""
                return False
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                self.error_message = "Sentinel\nerror"
                return False
            windows = data.get("windows", {})
            self.windows = {
                name: float(w.get("utilization", 0) or 0)
                for name, w in windows.items()
            }
            # Sentinel reports resets_at as ISO timestamps; convert to
            # seconds-until-reset for display.
            self.resets = {}
            for name, w in windows.items():
                meta = w.get("metadata") or {}
                if "reset_in_sec" in meta:
                    try:
                        self.resets[name] = float(meta["reset_in_sec"])
                        continue
                    except (TypeError, ValueError):
                        pass
                resets_at = self._parse_iso(w.get("resets_at"))
                self.resets[name] = (
                    max(0.0, (resets_at - datetime.now(timezone.utc)).total_seconds())
                    if resets_at
                    else 0.0
                )
            self.has_data = True
            return True
        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"Failed to fetch from Sentinel: {e}")
            self.error_message = "Sentinel\nerror"
            return False

    # ── Direct-mode scrape ─────────────────────────────────────────────────

    def _browser_candidates(self) -> list[str]:
        if self.browser in ("auto", ""):
            return list(_BROWSER_ORDER)
        return [self.browser]

    def _resolve_console_session(self) -> str:
        """__Host-console_session is exact-host; 'auto' would miss it because
        Firefox stores the parent domain, so Chromium browsers are tried in
        order and any hit counts (not first-browser-wins like auth)."""
        if self.browser == "disabled":
            return ""
        for browser in self._browser_candidates():
            if browser not in _CHROMIUM_BROWSERS:
                continue
            try:
                cookies = list_cookies(self.cookie_domain, browser=browser)
            except CookieError as e:
                self.log(LogLevel.WARNING, f"browser cookie lookup failed ({browser}): {e}")
                continue
            value = cookies.get(CONSOLE_COOKIE)
            if value:
                return value
        return ""

    def _resolve_workspace_id(self) -> str:
        """A pasted workspace_id wins; otherwise ask the console API which
        orgs this session can see (first one, same as the dashboard)."""
        if self.workspace_id:
            return self.workspace_id
        token = self._resolve_console_session()
        if not token:
            return ""
        try:
            resp = requests.get(
                "https://opencode.ai/console/api/orgs",
                headers={"User-Agent": _BROWSER_UA, "Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if resp.status_code == 200:
                orgs = resp.json()
                if orgs and isinstance(orgs[0], dict):
                    return orgs[0].get("id") or ""
        except (requests.exceptions.RequestException, ValueError, KeyError) as e:
            self.log(LogLevel.WARNING, f"could not resolve workspace via console API: {e}")
        return ""

    @staticmethod
    def _parse_iso(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _fetch_from_console_api(self) -> bool:
        token = self._resolve_console_session()
        workspace_id = self._resolve_workspace_id()
        if not token or not workspace_id:
            return False
        try:
            resp = requests.get(
                CONSOLE_STATUS_URL,
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    "x-org-id": workspace_id,
                },
                timeout=10,
            )
            if resp.status_code in (401, 403):
                self.log(LogLevel.WARNING, "console session expired — re-login at opencode.ai")
                return False
            resp.raise_for_status()
            status = resp.json()
        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"console API fetch failed: {e}")
            return False

        access = status.get("access") or {}
        meters = access.get("meters") or {}
        now = datetime.now(timezone.utc)
        windows: dict[str, float] = {}
        resets: dict[str, float] = {}
        for meter_key, name in METER_WINDOWS.items():
            meter = meters.get(meter_key)
            if not isinstance(meter, dict):
                continue
            try:
                limit = int(meter["limitMicroCents"])
                used = int(meter["usedMicroCents"])
            except (KeyError, TypeError, ValueError):
                continue
            windows[name] = max(0.0, min(100.0, used / limit * 100.0)) if limit > 0 else 0.0
            resets_at = self._parse_iso(meter.get("resetsAt")) or self._parse_iso(
                access.get("endsAt")
            )
            resets[name] = max(0.0, (resets_at - now).total_seconds()) if resets_at else 0.0

        if not windows:
            self.log(LogLevel.WARNING, "console status had no meters (access missing?)")
            return False
        self.windows = windows
        self.resets = resets
        self.has_data = True
        self.error_message = None
        return True

    def _resolve_auth_cookie(self) -> str:
        """Browser-extraction wins over the pasted value when ``browser`` is set."""
        if not self.browser:
            return self.auth_cookie
        try:
            cookies = list_cookies(self.cookie_domain, browser=self.browser)
        except CookieError as e:
            self.log(LogLevel.WARNING, f"browser cookie lookup failed: {e}")
            return self.auth_cookie
        value = cookies.get("auth")
        if not value:
            self.log(
                LogLevel.WARNING,
                f"no 'auth' cookie for {self.cookie_domain} in {self.browser}",
            )
            return self.auth_cookie
        return value

    def _fetch_direct(self) -> bool:
        if not self.workspace_id:
            self.error_message = "No\nworkspace"
            return False
        cookie = self._resolve_auth_cookie()
        if not cookie:
            self.error_message = "No\ncookie"
            return False

        url = DASHBOARD_URL.format(workspace_id=self.workspace_id)
        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Cookie": f"auth={cookie}",
        }
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code in (401, 403):
                self.log(LogLevel.ERROR, "auth cookie expired (HTTP 401/403)")
                self.error_message = "Cookie\nexpired"
                return False
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            self.log(LogLevel.ERROR, f"dashboard fetch failed: {e}")
            self.error_message = "Fetch\nerror"
            return False

        html = response.text
        self.windows = {}
        self.resets = {}
        for name, patterns in _WINDOW_PATTERNS.items():
            parsed = _parse_window(html, patterns)
            if parsed is None:
                continue
            usage_pct, reset_in_sec = parsed
            self.windows[name] = max(0.0, min(100.0, usage_pct))
            self.resets[name] = max(0.0, reset_in_sec)

        if not self.windows:
            self.log(LogLevel.ERROR, "could not parse dashboard hydration payload")
            self.error_message = "Parse\nfailed"
            return False

        self.has_data = True
        return True

    def _fetch_usage(self) -> bool:
        # Console API first (exact meters, no scraping), then sentinel, then
        # the legacy SSR scrape — a dead or outdated path never blacks out
        # the badge as long as another still has valid credentials.
        if self._fetch_from_console_api():
            return True
        if self.quota_sentinel_url and self._fetch_from_sentinel():
            self.error_message = None
            return True
        return self._fetch_direct()

    # ── Drawing primitives ────────────────────────────────────────────────

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
            return (250, 199, 29)  # OpenCode ember / yellow

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

    def _draw_bar(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        w: int,
        h: int,
        pct: float,
    ) -> None:
        draw.rectangle([x, y, x + w, y + h], fill=(30, 30, 30), outline=(80, 80, 80))
        fill_w = int(w * min(pct, 100) / 100)
        if fill_w > 0:
            color = self._get_bar_color(pct)
            draw.rectangle([x + 1, y + 1, x + fill_w, y + h - 1], fill=color)

    def _ordered_windows(self) -> list[tuple[str, float]]:
        priority = ["rolling_5h", "weekly", "monthly"]
        ordered = [(n, self.windows[n]) for n in priority if n in self.windows]
        ordered += [(n, p) for n, p in self.windows.items() if n not in priority]
        return ordered

    def _short_label(self, name: str) -> str:
        return {
            "rolling_5h": "5H",
            "weekly": "WEEK",
            "monthly": "MONTH",
        }.get(name, name[:5].upper())

    def _renewal_label(self, name: str | None = None) -> str:
        """Short local-date label for a window reset, e.g. ``ren 24/10``.

        With no ``name`` it picks the soonest upcoming reset across windows.
        Returns "" when there is no reset information.
        """
        if name is None:
            # Prefer the plan-level (monthly) renewal; fall back to the next
            # window that actually has a reset time.
            for pref in ("monthly", "weekly", "rolling_5h"):
                if self.resets.get(pref, 0) > 0:
                    name = pref
                    break
            else:
                for win, secs in self.resets.items():
                    if secs and secs > 0 and (name is None or secs < self.resets[name]):
                        name = win
        if name is None:
            return ""
        secs = self.resets.get(name) or 0
        if secs <= 0:
            return ""
        when = datetime.now().astimezone() + timedelta(seconds=secs)
        return f"ren {when:%-d/%-m}"

    # ── Display ───────────────────────────────────────────────────────────

    def _update_display(self) -> None:
        try:
            if self.error_message and not self.has_data:
                self.update_image_render(
                    text=f"OC Go\n{self.error_message}",
                    background_color="#B71C1C",
                    font_color="#FFFFFF",
                    font_size=10,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if not self.has_data:
                self.update_image_render(
                    text="OC Go\n…",
                    background_color="#1F2937",
                    font_color="#FAC71D",
                    font_size=11,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if not self.windows:
                self.update_image_render(
                    text="OC Go\nNo data",
                    background_color="#37474F",
                    font_color="#FFFFFF",
                    font_size=10,
                    text_vertical_align="middle",
                    text_horizontal_align="center",
                )
                return

            if self.display_mode == "rotate":
                self._render_rotate_view()
            else:
                self._render_compact_view()

        except Exception as e:  # noqa: BLE001
            self.log(LogLevel.ERROR, f"Display update failed: {e}")

    def _render_compact_view(self) -> None:
        img = Image.new("RGB", (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_label = self._load_font(8)

        max_pct = max(self.windows.values()) if self.windows else 0
        status_color = self._get_bar_color(max_pct)
        draw.ellipse([2, 2, 8, 8], fill=status_color)

        draw.text((12, 2), "OC Go", fill=(250, 199, 29), font=font_title)

        ordered = self._ordered_windows()
        # In compact mode show at most 2; prefer non-empty windows so a
        # fresh (0%) rolling/weekly doesn't crowd out the monthly bar.
        shown = [t for t in ordered if t[1] >= 0.05][:2] or ordered[:2]
        y = 16
        for name, pct in shown:
            label = self._short_label(name)
            draw.text((4, y), label, fill=(180, 180, 180), font=font_label)
            draw.text(
                (68, y),
                f"{pct:.0f}%",
                fill=(255, 255, 255),
                font=font_label,
                anchor="rt",
            )
            self._draw_bar(draw, 4, y + 11, 64, 6, pct)
            y += 22

        renewal = self._renewal_label()
        if renewal:
            draw.text(
                (36, y + 4), renewal, fill=(150, 150, 150), font=font_label, anchor="mt"
            )

        self.update_image_raw(img)

    def _render_rotate_view(self) -> None:
        ordered = self._ordered_windows()
        if not ordered:
            return

        idx = self.current_view % len(ordered)
        name, pct = ordered[idx]

        img = Image.new("RGB", (72, 72), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        font_title = self._load_font(10)
        font_label = self._load_font(11)
        font_value = self._load_font(18)

        max_pct = max(self.windows.values()) if self.windows else 0
        status_color = self._get_bar_color(max_pct)
        draw.ellipse([2, 2, 8, 8], fill=status_color)

        draw.text((12, 2), "OC Go", fill=(250, 199, 29), font=font_title)
        draw.text(
            (36, 18),
            self._short_label(name),
            fill=(180, 180, 180),
            font=font_label,
            anchor="mt",
        )

        color = self._get_bar_color(pct)
        draw.text((36, 34), f"{pct:.0f}%", fill=color, font=font_value, anchor="mt")

        self._draw_bar(draw, 4, 60, 64, 8, pct)

        renewal = self._renewal_label(name)
        if renewal:
            draw.text(
                (36, 50),
                renewal,
                fill=(150, 150, 150),
                font=self._load_font(8),
                anchor="mt",
            )

        self.update_image_raw(img)

    # ── BasePlugin lifecycle ──────────────────────────────────────────────

    def on_start(self) -> None:
        self.log(LogLevel.INFO, "OpenCode Go usage plugin started")
        mode = "sentinel" if self.quota_sentinel_url else "direct"
        self.log(
            LogLevel.INFO,
            f"Mode: {mode}, poll: {self.poll_interval}s, display: {self.display_mode}",
        )
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
        self.workspace_id = (config.get("workspace_id") or "").strip()
        self.auth_cookie = (config.get("auth_cookie") or "").strip()
        self.browser = (config.get("browser") or "").strip().lower()
        self.cookie_domain = (config.get("cookie_domain") or COOKIE_DOMAIN).strip()
        self.poll_interval = max(int(config.get("poll_interval", 300)), 60)
        self.display_mode = config.get("display_mode", "compact")
        self.rotate_interval = int(config.get("rotate_interval", 5))
        self.quota_sentinel_url = (
            (config.get("quota_sentinel_url") or "").split("/v1")[0].rstrip("/")
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

        if self.display_mode == "rotate" and self.has_data and self.windows:
            if current_time - self.last_rotate_time >= self.rotate_interval:
                self.current_view += 1
                self.last_rotate_time = current_time
                self._update_display()


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: opencode_go_usage_plugin.py <socket_path> <config_json>")
        sys.exit(1)

    socket_path = sys.argv[1]
    try:
        config = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(f"Invalid config JSON: {e}")
        sys.exit(1)

    plugin = OpencodeGoUsagePlugin(socket_path, config)
    plugin.run()


if __name__ == "__main__":
    main()
