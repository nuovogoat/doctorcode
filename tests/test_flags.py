"""Tests for the code-drawn flag bitmaps (v2.9 §5)."""

from __future__ import annotations

from doctorcode.i18n import LANGUAGES
from doctorcode.ui.flags import FLAG_PIXELS, flag_size


def test_all_languages_have_flags() -> None:
    """Every supported UI language must have a corresponding flag bitmap."""
    for code in LANGUAGES:
        assert code in FLAG_PIXELS, f"missing flag bitmap for language '{code}'"


def test_flag_dimensions_are_consistent() -> None:
    w, h = flag_size()
    assert w > 0 and h > 0
    for code, rows in FLAG_PIXELS.items():
        assert len(rows) == h, f"{code}: expected {h} rows, got {len(rows)}"
        for i, row in enumerate(rows):
            assert len(row) == w, f"{code} row {i}: expected {w} pixels, got {len(row)}"


def test_flag_pixels_are_valid_rgb() -> None:
    for code, rows in FLAG_PIXELS.items():
        for y, row in enumerate(rows):
            for x, pixel in enumerate(row):
                assert len(pixel) == 3, f"{code}[{y}][{x}]: expected (R,G,B)"
                for ch in pixel:
                    assert 0 <= ch <= 255, f"{code}[{y}][{x}]: channel {ch} out of range"


def test_no_extra_flags_beyond_supported_languages() -> None:
    extra = set(FLAG_PIXELS) - set(LANGUAGES)
    assert not extra, f"flag bitmaps exist for unsupported languages: {extra}"
