"""Dibujos compartidos de los bundles net.millaguie.minino.* (todo hecho con Pillow aquí)."""

from PIL import Image, ImageDraw

from minino_runtime import png_of

BACKGROUND = (18, 18, 22)
INK = (240, 240, 245)
DIM = (120, 120, 130)
ACCENT = (255, 140, 0)
ALERT = (235, 70, 60)
GOOD = (90, 200, 120)
PANEL = (42, 42, 50)


def canvas(size: int):
    image = Image.new("RGB", (size, size), BACKGROUND)
    return image, ImageDraw.Draw(image)


def text(draw: ImageDraw.ImageDraw, where, body: str, fill=INK, size=11):
    draw.text(where, body, fill=fill)


def meter(image, draw, x, y, width, height, fraction, label, value, colour=ACCENT):
    """Barra horizontal con etiqueta y valor, para monitor y volumen."""
    fraction = max(0.0, min(1.0, fraction))
    draw.rectangle([x, y, x + width, y + height], fill=PANEL)
    if fraction > 0:
        draw.rectangle([x, y, x + int(width * fraction), y + height], fill=colour)
    draw.text((x, y - 13), label, fill=DIM)
    draw.text((x + width - 30, y - 13), value, fill=INK)


def icon_mic(muted: bool, size: int = 96) -> bytes:
    image, draw = canvas(size)
    centre = size / 2
    scale = size / 96
    capsule = (centre - 9 * scale, centre - 26 * scale, centre + 9 * scale, centre + 6 * scale)
    draw.rounded_rectangle(capsule, radius=9 * scale, fill=ALERT if muted else INK)
    draw.arc(
        (centre - 18 * scale, centre - 16 * scale, centre + 18 * scale, centre + 16 * scale),
        0,
        180,
        fill=INK,
        width=int(3 * scale),
    )
    draw.line((centre, centre + 16 * scale, centre, centre + 26 * scale), fill=INK, width=int(3 * scale))
    draw.line(
        (centre - 12 * scale, centre + 26 * scale, centre + 12 * scale, centre + 26 * scale),
        fill=INK,
        width=int(3 * scale),
    )
    if muted:
        draw.line(
            (centre - 26 * scale, centre - 26 * scale, centre + 26 * scale, centre + 26 * scale),
            fill=ALERT,
            width=int(5 * scale),
        )
    return png_of(image)


def icon_speaker(delta: int, size: int = 96) -> bytes:
    image, draw = canvas(size)
    centre = size / 2
    scale = size / 96
    draw.polygon(
        [
            (centre - 24 * scale, centre - 8 * scale),
            (centre - 12 * scale, centre - 8 * scale),
            (centre - 2 * scale, centre - 18 * scale),
            (centre - 2 * scale, centre + 18 * scale),
            (centre - 12 * scale, centre + 8 * scale),
            (centre - 24 * scale, centre + 8 * scale),
        ],
        fill=INK,
    )
    colour = GOOD if delta > 0 else ALERT if delta < 0 else DIM
    for step in range(1, 4):
        radius = (12 + step * 7) * scale
        draw.arc(
            (centre - radius, centre - radius, centre + radius, centre + radius),
            -45,
            45,
            fill=colour,
            width=int(3 * scale),
        )
    draw.text((centre + 12 * scale, centre - 34 * scale), "+" if delta > 0 else "-" if delta < 0 else "", fill=colour)
    return png_of(image)


def icon_chip(size: int = 96) -> bytes:
    image, draw = canvas(size)
    centre = size / 2
    scale = size / 96
    side = 26 * scale
    draw.rectangle((centre - side, centre - side, centre + side, centre + side), outline=INK, width=int(3 * scale))
    draw.rectangle(
        (centre - side / 2, centre - side / 2, centre + side / 2, centre + side / 2),
        outline=ACCENT,
        width=int(3 * scale),
    )
    for step in range(-1, 2):
        offset = step * 15 * scale
        for edge in (-1, 1):
            draw.line(
                (centre + offset, centre + edge * side, centre + offset, centre + edge * (side + 8 * scale)),
                fill=INK,
                width=int(2 * scale),
            )
            draw.line(
                (centre + edge * side, centre + offset, centre + edge * (side + 8 * scale), centre + offset),
                fill=INK,
                width=int(2 * scale),
            )
    return png_of(image)
