"""Shared colour palettes that do not depend on a GUI toolkit."""

from __future__ import annotations

DARK = {
    "bg": "#15181e",
    "panel": "#1d222b",
    "panel_alt": "#252b36",
    "text": "#e4eaf4",
    "muted": "#93a0b4",
    "border": "#333b49",
    "accent": "#5aa9ff",
    "success": "#4fbf7b",
    "failure": "#e05a5a",
    "progress": "#d7a33d",
    "conflict_bg": "#6e2430",
    "selection": "#2d4a6b",
}

LIGHT = {
    "bg": "#f4f6fa",
    "panel": "#ffffff",
    "panel_alt": "#e9edf4",
    "text": "#1b2230",
    "muted": "#5f6d80",
    "border": "#c9d2df",
    "accent": "#1f6fd1",
    "success": "#237a46",
    "failure": "#b22b2b",
    "progress": "#8a6100",
    "conflict_bg": "#ffd7db",
    "selection": "#cfe2ff",
}


def palette(theme: str) -> dict[str, str]:
    return LIGHT if theme == "light" else DARK
