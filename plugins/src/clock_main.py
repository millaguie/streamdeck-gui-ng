"""Reloj analógico de net.millaguie.minino.clock.

Dibuja la esfera con Pillow y manda la imagen por setImage una vez por segundo. El dibujo es
nuestro: fondo oscuro, marcas de hora, manecilla de hora y minuto claras y segundero de color.
"""

import math
from datetime import datetime

from PIL import Image, ImageDraw

from minino_runtime import Plugin, png_of, run

BACKGROUND = (18, 18, 22)
RIM = (70, 70, 80)
TICK = (150, 150, 160)
TICK_MAJOR = (235, 235, 240)
HAND = (245, 245, 250)
SECOND = (255, 140, 0)
CLOCK_SIZE = 144


def draw_clock(now: datetime, size: int = CLOCK_SIZE, show_seconds: bool = True) -> bytes:
    scale = size / CLOCK_SIZE
    image = Image.new("RGB", (size, size), BACKGROUND)
    draw = ImageDraw.Draw(image)
    centre = size / 2
    radius = centre - 3 * scale

    draw.ellipse(
        [centre - radius, centre - radius, centre + radius, centre + radius],
        outline=RIM,
        width=max(1, int(2 * scale)),
    )

    for hour in range(12):
        angle = math.radians(hour * 30 - 90)
        major = hour % 3 == 0
        inner = radius - (14 if major else 8) * scale
        point = (
            centre + math.cos(angle) * inner,
            centre + math.sin(angle) * inner,
        )
        outer = (centre + math.cos(angle) * (radius - 3 * scale), centre + math.sin(angle) * (radius - 3 * scale))
        draw.line([point, outer], fill=TICK_MAJOR if major else TICK, width=max(1, int((3 if major else 2) * scale)))

    seconds = now.second + now.microsecond / 1_000_000
    minutes = now.minute + seconds / 60
    hours = (now.hour % 12) + minutes / 60

    _hand(draw, centre, radius * 0.52, hours * 30 - 90, 5 * scale, HAND)
    _hand(draw, centre, radius * 0.78, minutes * 6 - 90, 3.5 * scale, HAND)
    if show_seconds:
        _hand(draw, centre, radius * 0.86, seconds * 6 - 90, 1.6 * scale, SECOND)

    draw.ellipse(
        [centre - 4 * scale, centre - 4 * scale, centre + 4 * scale, centre + 4 * scale],
        fill=HAND if not show_seconds else SECOND,
    )
    return png_of(image)


def _hand(draw: ImageDraw.ImageDraw, centre: float, length: float, angle: float, width: float, colour) -> None:
    radians = math.radians(angle)
    tail = length * 0.18
    draw.line(
        [
            (centre - math.cos(radians) * tail, centre - math.sin(radians) * tail),
            (centre + math.cos(radians) * length, centre + math.sin(radians) * length),
        ],
        fill=colour,
        width=max(1, int(width)),
    )


class ClockPlugin(Plugin):
    def on_appear(self, message: dict) -> None:
        self._repaint()
        self.every(1.0)

    def on_settings(self, message: dict) -> None:
        self._repaint()

    def on_disappear(self, message: dict) -> None:
        if not self.contexts:
            self._deadline_off()

    def on_tick(self) -> None:
        self._repaint()
        if self.contexts:
            self.every(1.0)

    def _deadline_off(self) -> None:
        self.every(0.0)
        self._deadline = 0.0

    def _repaint(self) -> None:
        now = datetime.now()
        for context, binding in self.visible().items():
            settings = binding.get("settings") or {}
            image = draw_clock(
                now,
                size=int(settings.get("size") or CLOCK_SIZE),
                show_seconds=bool(settings.get("showSeconds", True)),
            )
            self.set_image(context, image)
            self.set_title(context, "")


if __name__ == "__main__":
    run(ClockPlugin())
