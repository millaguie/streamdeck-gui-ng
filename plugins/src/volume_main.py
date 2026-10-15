"""Volumen de salida en Linux: net.millaguie.minino.volume (wpctl).

Un solo programa sirve a las dos acciones del bundle; decide si sube o baja mirando qué acción
colocó el usuario en la tecla.
"""

import subprocess

from artwork import canvas, meter
from minino_runtime import Plugin, png_of, run

SINK = "@DEFAULT_AUDIO_SINK@"
SIZE = 144


def _run(*arguments):
    try:
        completed = subprocess.run(arguments, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (completed.stdout or "") + (completed.stderr or "")


def read_volume(sink: str = SINK):
    report = _run("wpctl", "get-volume", sink)
    muted = "MUTED" in report
    fraction = 0.0
    for piece in report.replace("Volume:", " ").split():
        try:
            fraction = float(piece)
        except ValueError:
            continue
        break
    return max(0.0, min(1.0, fraction)), muted


def change_volume(step: int, up: bool, sink: str = SINK):
    direction = "%+" if up else "%-"
    _run("wpctl", "set-volume", sink, f"{abs(step)}{direction}")


def toggle_mute(sink: str = SINK):
    _run("wpctl", "set-mute", sink, "toggle")


def draw_volume(fraction, muted=False, size=SIZE):
    image, draw = canvas(size)
    scale = size / 144
    meter(
        image,
        draw,
        12 * scale,
        66 * scale,
        size - 24 * scale,
        22 * scale,
        fraction,
        "VOL",
        f"{fraction * 100:.0f}%",
        (235, 70, 60) if muted else (255, 140, 0),
    )
    return png_of(image)


class VolumePlugin(Plugin):
    def on_started(self, handshake):
        self.up = str(handshake.action_info.get("action", "")).endswith(".up")

    def on_appear(self, message):
        self._repaint()
        self.every(2.0)

    def on_press(self, message):
        if message.get("event") != "keyUp":
            return
        settings = self._settings(message)
        if settings.get("muteOnClick"):
            toggle_mute(str(settings.get("sink") or SINK))
        else:
            change_volume(int(settings.get("step") or 5), self.up, str(settings.get("sink") or SINK))
        self._repaint()

    def on_tick(self):
        self._repaint()
        if self.contexts:
            self.every(2.0)

    def _settings(self, message) -> dict:
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        return payload.get("settings") or self.contexts.get(message.get("context", ""), {}).get("settings", {}) or {}

    def _repaint(self):
        fraction, muted = read_volume()
        for context in self.visible():
            self.set_image(context, draw_volume(fraction, muted))
            self.set_title(context, "")


if __name__ == "__main__":
    run(VolumePlugin())
