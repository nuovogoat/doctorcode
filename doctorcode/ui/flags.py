"""Country flag icons rendered as pixel grids — no emoji-font dependency.

v2.9 §5 requires that language-switcher flags are drawn by code (pixel grid /
icon), not sourced from emoji glyphs whose rendering depends on the host
font stack.  Each flag is a small RGB bitmap (16×12 pixels) that both the
Tkinter and Qt contours can paint from.

Public API
----------
FLAG_PIXELS : dict[str, list[list[tuple[int, int, int]]]]
    Mapping ``language code -> rows of (R, G, B) tuples``.
    Supported codes: ``ru``, ``en``, ``de``.

flag_size() -> tuple[int, int]
    ``(width, height)`` of every flag bitmap.
"""

from __future__ import annotations

# All flags share the same dimensions.
_WIDTH = 16
_HEIGHT = 12

# --- Colour palettes -------------------------------------------------------

_WHITE = (255, 255, 255)
_BLUE = (0, 57, 166)
_RED = (213, 43, 30)
_BLACK = (0, 0, 0)
_GOLD = (255, 206, 0)


def _rows(*bands: tuple[tuple[int, int, int], int]) -> list[list[tuple[int, int, int]]]:
    """Build a flag from horizontal colour bands (colour, row_count)."""
    out: list[list[tuple[int, int, int]]] = []
    for colour, count in bands:
        row = [colour] * _WIDTH
        for _ in range(count):
            out.append(row)
    return out


# Russia: white-blue-red horizontal tricolour (equal thirds).
_RU = _rows((_WHITE, 4), (_BLUE, 4), (_RED, 4))

# Germany: black-red-gold horizontal tricolour (equal thirds).
_DE = _rows((_BLACK, 4), (_RED, 4), (_GOLD, 4))

# United Kingdom: simplified Union Jack on a 16×12 grid.
# Background blue, white cross + red cross, white diagonals + red diagonals.
_EN: list[list[tuple[int, int, int]]] = [[_BLUE] * _WIDTH for _ in range(_HEIGHT)]

# White St George's cross (horizontal + vertical bars).
for y in range(_HEIGHT):
    for x in range(_WIDTH):
        # Horizontal bar: rows 5-6 (0-indexed), full width.
        if 5 <= y <= 6:
            _EN[y][x] = _WHITE
        # Vertical bar: cols 7-8, full height.
        if 7 <= x <= 8:
            _EN[y][x] = _WHITE

# Red St George's cross (thinner, centred inside the white cross).
for y in range(_HEIGHT):
    for x in range(_WIDTH):
        if y == 5 or y == 6:
            if 0 <= x < _WIDTH:
                _EN[y][x] = _RED if (7 <= x <= 8) else _EN[y][x]
        if 7 <= x <= 8:
            _EN[y][x] = _RED

# White saltire (diagonals) — St Andrew's cross.
for i in range(min(_WIDTH, _HEIGHT)):
    # Top-left to bottom-right.
    tx, ty = i, int(i * _HEIGHT / _WIDTH)
    if 0 <= tx < _WIDTH and 0 <= ty < _HEIGHT:
        if _EN[ty][tx] == _BLUE:
            _EN[ty][tx] = _WHITE
    # Top-right to bottom-left.
    bx = _WIDTH - 1 - i
    by = int(i * _HEIGHT / _WIDTH)
    if 0 <= bx < _WIDTH and 0 <= by < _HEIGHT:
        if _EN[by][bx] == _BLUE:
            _EN[by][bx] = _WHITE

# Red saltire (St Patrick's cross) — thinner diagonal inside white saltire.
for i in range(min(_WIDTH, _HEIGHT)):
    tx, ty = i, int(i * _HEIGHT / _WIDTH)
    if 0 <= tx < _WIDTH and 0 <= ty < _HEIGHT and _EN[ty][tx] == _WHITE:
        _EN[ty][tx] = _RED
    bx = _WIDTH - 1 - i
    by = int(i * _HEIGHT / _WIDTH)
    if 0 <= bx < _WIDTH and 0 <= by < _HEIGHT and _EN[by][bx] == _WHITE:
        _EN[by][bx] = _RED


FLAG_PIXELS: dict[str, list[list[tuple[int, int, int]]]] = {
    "ru": _RU,
    "en": _EN,
    "de": _DE,
}


def flag_size() -> tuple[int, int]:
    """Return ``(width, height)`` of every flag bitmap."""
    return (_WIDTH, _HEIGHT)
