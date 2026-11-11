"""Foreground-free evidence contact sheets built only from genuine window bitmaps.

The contact sheet labels itself as an assembly to avoid any confusion with
an unmodified desktop screenshot.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image


def evidence_montage(circuit: "Image.Image", panels: list[tuple[str, "Image.Image"]]) -> "Image.Image":
    from PIL import Image, ImageDraw

    circuit_rgb = circuit.convert("RGB")
    panel_rgbs = [(name, image.convert("RGB")) for name, image in panels]
    header, margin, gap = 30, 16, 12
    pw = max((image.width for _, image in panel_rgbs), default=0)
    ph = sum(image.height + header + gap for _, image in panel_rgbs)
    width = margin * 2 + circuit_rgb.width + (gap + pw if panel_rgbs else 0)
    height = max(circuit_rgb.height + header + 2 * margin, ph + 2 * margin) + 16
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, margin), "Circuit - PrintWindow", fill="black")
    canvas.paste(circuit_rgb, (margin, margin + header))
    x, y = margin + circuit_rgb.width + gap, margin
    for name, image in panel_rgbs:
        draw.text((x, y), "Panel: " + name[:80], fill="black")
        y += header
        canvas.paste(image, (x, y))
        y += image.height + gap
    draw.text((margin, height - 14), "COMPOSITE from real windows - NOT a desktop screenshot", fill="black")
    return canvas
