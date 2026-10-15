"""Monitor de sistema de net.millaguie.minino.monitor: CPU y memoria leídas de /proc."""

from pathlib import Path

from PIL import ImageDraw  # noqa: F401  (artwork usa ImageDraw internamente)

from artwork import ACCENT, ALERT, GOOD, canvas, meter
from minino_runtime import Plugin, png_of, run

PROC = Path("/proc")
SIZE = 144


def _cpu_percent(previous):
    try:
        fields = PROC.joinpath("stat").read_text().splitlines()[0].split()
    except OSError:
        return 0.0, previous
    numbers = [int(value) for value in fields[1:]]
    idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
    total = sum(numbers)
    percent = 0.0
    if previous:
        previous_idle, previous_total = previous
        busy = (total - previous_total) - (idle - previous_idle)
        span = total - previous_total
        percent = 100.0 * busy / span if span > 0 else 0.0
    return max(0.0, min(100.0, percent)), (idle, total)


def _memory_percent():
    values = {}
    try:
        for line in PROC.joinpath("meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            values[key.strip()] = int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0
    total = values.get("MemTotal", 0)
    if not total:
        return 0.0
    used = total - values.get("MemAvailable", 0)
    return 100.0 * used / total


def _load_average():
    try:
        return PROC.joinpath("loadavg").read_text().split()[0]
    except (OSError, IndexError):
        return "0.00"


def _device_temperature():
    for path in PROC.glob("hardwaremon/*/temp*_input"):
        try:
            return int(path.read_text().strip()) / 1000
        except (OSError, ValueError):
            continue
    return None


def draw_monitor(cpu, memory, size=SIZE, temperature=None):
    image, draw = canvas(size)
    scale = size / 144
    meter(
        image,
        draw,
        12 * scale,
        46 * scale,
        size - 24 * scale,
        16 * scale,
        cpu / 100,
        "CPU",
        f"{cpu:.0f}%",
        ACCENT if cpu < 80 else ALERT,
    )
    meter(
        image,
        draw,
        12 * scale,
        92 * scale,
        size - 24 * scale,
        16 * scale,
        memory / 100,
        "RAM",
        f"{memory:.0f}%",
        GOOD if memory < 85 else ALERT,
    )
    footer = f"load {_load_average()}"
    if temperature is not None:
        footer += f"  {temperature:.0f}C"
    draw.text((12 * scale, 112 * scale), footer, fill=(150, 150, 160))
    return png_of(image)


class MonitorPlugin(Plugin):
    def __init__(self):
        super().__init__()
        self._previous = None

    def on_appear(self, message):
        self._repaint()
        self.every(1.0)

    def on_tick(self):
        self._repaint()
        if self.contexts:
            self.every(1.0)

    def _repaint(self):
        cpu, self._previous = _cpu_percent(self._previous)
        memory = _memory_percent()
        for context in self.visible():
            self.set_image(context, draw_monitor(cpu, memory, temperature=_device_temperature()))
            self.set_title(context, "")


if __name__ == "__main__":
    run(MonitorPlugin())
