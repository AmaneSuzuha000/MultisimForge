"""Foreground-free evidence contact sheets built only from genuine window bitmaps.

The contact sheet labels itself as an assembly to avoid any confusion with
an unmodified desktop screenshot.
"""
from PIL import Image, ImageDraw


def evidence_montage(circuit: Image.Image, panels: list[tuple[str, Image.Image]]) -> Image.Image:
    circuit = circuit.convert("RGB")
    panels = [(name, image.convert("RGB")) for name, image in panels]
    header, margin, gap = 30, 16, 12
    pw = max((image.width for _, image in panels), default=0)
    ph = sum(image.height + header + gap for _, image in panels)
    width = margin * 2 + circuit.width + (gap + pw if panels else 0)
    height = max(circuit.height + header + 2 * margin, ph + 2 * margin) + 16
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, margin), "Circuit - PrintWindow", fill="black")
    canvas.paste(circuit, (margin, margin + header))
    x, y = margin + circuit.width + gap, margin
    for name, image in panels:
        draw.text((x, y), "Panel: " + name[:80], fill="black")
        y += header
        canvas.paste(image, (x, y))
        y += image.height + gap
    draw.text((margin, height - 14), "COMPOSITE from real windows - NOT a desktop screenshot", fill="black")
    return canvas
