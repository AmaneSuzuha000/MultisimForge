"""The screenshot helpers decide whether an image is any good, so they need proofs.

None of this needs Multisim: the helpers work on a PIL image, and a synthetic image
with a known answer is a stronger test than a real screenshot, whose answer nobody knows.
"""
import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from multisimforge import capture  # noqa: E402


def _scope(wave_columns):
    """A black oscilloscope display with a one-pixel trace at each x in *wave_columns*.

    The trace must be thin: a column filled top to bottom merges with the display's own
    dark margin in _longest_run, and coverage then reports 0 for a screen that is full.
    """
    img = Image.new("RGB", (400, 300), (240, 240, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 379, 279], fill=(0, 0, 0))
    for x, colour in wave_columns:
        d.line([(x, 150), (x, 151)], fill=colour)
    return img


def test_full_width_trace_covers_the_display():
    img = _scope([(x, (220, 40, 40)) for x in range(30, 370)])
    assert capture.scope_coverage(img) == pytest.approx(1.0, abs=0.02)


def test_no_trace_covers_nothing():
    assert capture.scope_coverage(_scope([])) == 0.0


def test_grey_waveform_is_not_counted_as_a_trace():
    """Coverage asks for coloured ink; a grey grid must not be mistaken for a signal."""
    img = _scope([(x, (90, 90, 90)) for x in range(30, 370)])
    assert capture.scope_coverage(img) < 0.05


def test_trace_covering_half_the_screen_scores_near_one_half():
    img = _scope([(x, (220, 40, 40)) for x in range(30, 200)])
    assert capture.scope_coverage(img) == pytest.approx(0.5, abs=0.05)


def test_an_image_without_a_dark_area_scores_zero():
    assert capture.scope_coverage(Image.new("RGB", (400, 300), (255, 255, 255))) == 0.0


def test_a_filled_block_merges_with_the_display_and_scores_zero():
    """Documents the limit rather than hiding it.

    _longest_run bridges gaps of up to six, so a block of colour joined to the display's
    own dark border is read as one region and the row search collapses. A real trace is a
    one-pixel curve and never looks like this; the check is a sanity gate, not a measurement.
    """
    img = _scope([(x, (220, 40, 40)) for x in range(20, 380) for _ in range(1)])
    d = ImageDraw.Draw(img)
    d.rectangle([30, 30, 370, 270], fill=(220, 40, 40))
    assert capture.scope_coverage(img) < 0.05


def test_longest_run_returns_the_span_not_its_length():
    assert capture._longest_run([0, 1, 1, 1, 0, 0]) == (1, 4)


def test_longest_run_bridges_a_short_gap():
    """A one-column gap is absorbed by the default kernel of six."""
    assert capture._longest_run([1, 1, 0, 1, 1]) == (0, 5)


def test_longest_run_does_not_bridge_a_long_gap():
    assert capture._longest_run([1, 1, 0, 0, 0, 0, 0, 0, 0, 1, 1], gap=2) == (0, 2)


def test_longest_run_of_nothing_is_empty():
    assert capture._longest_run([0, 0, 0]) == (0, 0)


def _page(ink_boxes):
    img = Image.new("RGB", (600, 400), (235, 235, 235))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 560, 360], fill=(255, 255, 255))          # the white page
    for box in ink_boxes:
        d.rectangle(box, fill=(0, 0, 0))
    return img


def test_ink_mask_finds_a_component_and_its_offset():
    img = _page([[200, 160, 260, 220]])
    ink, (ox, oy) = capture.ink_mask(img)
    assert ink.sum() > 0
    assert ox > 0 and oy > 0, "the offset must locate the page inside the screenshot"


def test_ink_mask_ignores_speckle():
    """Single-pixel dust must not survive: it would otherwise widen the crop."""
    img = _page([[300, 200, 301, 201]])
    ink, _ = capture.ink_mask(img)
    assert ink.sum() == 0


def test_crop_ink_keeps_the_component_and_drops_the_empty_margin():
    img = _page([[200, 160, 260, 220]])
    out = capture.crop_ink(img, margin=20)
    assert out.width < img.width and out.height < img.height
    assert out.width > 40 and out.height > 40, "the component itself must survive the crop"


def test_crop_ink_returns_the_image_unchanged_when_there_is_nothing_to_crop():
    blank = Image.new("RGB", (600, 400), (255, 255, 255))
    assert capture.crop_ink(blank).size == blank.size


def test_frame_inner_finds_the_gray_border():
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 15, 380, 285], outline=(130, 130, 130))
    left, top, right, bottom = capture.frame_inner(img)
    assert (left, top, right, bottom) == (20, 15, 380, 285)


def test_frame_inner_falls_back_to_the_image_edges():
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    assert capture.frame_inner(img) == (0, 0, 400, 300)


def test_capture_copy_keeps_the_original_name_and_bytes(tmp_path):
    """The window title carries only the stem, so the copy must not be renamed."""
    src = tmp_path / "rc.ms14"
    src.write_bytes(b"MSMCompressed" + b"\x00" * 32)
    work = tmp_path / "work"
    work.mkdir()
    copy = capture.capture_copy(src, work)
    assert copy.name == src.name
    assert copy.read_bytes() == src.read_bytes()
    assert src.exists(), "the source must never be moved or replaced"


def test_netlist_error_carries_the_dialog_text():
    exc = capture.NetlistError({"text": "Netlist error. Continue anyway?"})
    assert "Continue anyway" in str(exc)
    assert exc.dialog["text"].startswith("Netlist error")