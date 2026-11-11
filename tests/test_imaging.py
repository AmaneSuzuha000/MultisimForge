"""The default evidence montage is testable without Windows."""
import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402
from multisimforge.imaging import evidence_montage  # noqa: E402


def test_montage_preserves_source_pixels():
    circuit = Image.new("RGB", (20, 20), "red")
    panel = Image.new("RGB", (8, 12), "blue")
    original = circuit.tobytes()
    montage = evidence_montage(circuit, [("XSC1", panel)])
    assert montage.width > 28 and montage.height > 20
    assert montage.getpixel((18, 48)) == (255, 0, 0)
    assert montage.getpixel((50, 48)) == (0, 0, 255)
    assert circuit.tobytes() == original


def test_montage_without_panels():
    image = Image.new("RGB", (20, 20), "white")
    assert evidence_montage(image, []).width >= image.width
