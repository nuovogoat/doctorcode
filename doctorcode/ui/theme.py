"""Tkinter palette and ttk style application."""

from __future__ import annotations

import contextlib
import tkinter as tk
from tkinter import ttk

from doctorcode.ui.palette import DARK, LIGHT, palette

__all__ = [
    "DARK",
    "LIGHT",
    "apply_theme",
    "configure_text",
    "palette",
    "style_tree_tags",
]


def apply_theme(root: tk.Misc, theme: str) -> dict[str, str]:
    """Apply a readable clam-based palette; return the colors for custom tags."""
    colors = palette(theme)
    style = ttk.Style(root)
    with contextlib.suppress(tk.TclError):
        style.theme_use("clam")
    root.configure(background=colors["bg"])
    style.configure(".", background=colors["bg"], foreground=colors["text"])
    style.configure("TFrame", background=colors["bg"])
    style.configure("Panel.TFrame", background=colors["panel"])
    style.configure("TLabel", background=colors["bg"], foreground=colors["text"])
    style.configure("Muted.TLabel", foreground=colors["muted"])
    style.configure("Panel.TLabel", background=colors["panel"], foreground=colors["text"])
    style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
    style.configure("Subtitle.TLabel", font=("Segoe UI", 9), foreground=colors["muted"])
    style.configure("Danger.TLabel", foreground=colors["failure"])
    style.configure("Success.TLabel", foreground=colors["success"])
    style.configure("TButton", padding=(9, 4))
    style.configure("Accent.TButton", foreground="#ffffff", background=colors["accent"])
    style.map(
        "Accent.TButton",
        background=[("active", colors["accent"]), ("disabled", colors["border"])],
        foreground=[("disabled", colors["muted"])],
    )
    style.configure("Danger.TButton", foreground="#ffffff", background=colors["failure"])
    style.map("Danger.TButton", background=[("active", colors["failure"])])
    style.configure("TNotebook", background=colors["bg"], borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=colors["panel_alt"],
        foreground=colors["text"],
        padding=(12, 5),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", colors["panel"])],
        foreground=[("selected", colors["accent"])],
    )
    style.configure(
        "Treeview",
        background=colors["panel"],
        fieldbackground=colors["panel"],
        foreground=colors["text"],
        borderwidth=0,
        rowheight=25,
    )
    style.configure(
        "Treeview.Heading",
        background=colors["panel_alt"],
        foreground=colors["text"],
        relief="flat",
    )
    style.map(
        "Treeview",
        background=[("selected", colors["selection"])],
        foreground=[("selected", colors["text"])],
    )
    style.configure("TEntry", fieldbackground=colors["panel"], foreground=colors["text"])
    style.configure(
        "TCombobox",
        fieldbackground=colors["panel"],
        foreground=colors["text"],
        background=colors["panel"],
    )
    style.configure(
        "TLabelframe",
        background=colors["bg"],
        foreground=colors["text"],
        bordercolor=colors["border"],
    )
    style.configure(
        "TLabelframe.Label", background=colors["bg"], foreground=colors["accent"]
    )
    style.configure("TCheckbutton", background=colors["bg"], foreground=colors["text"])
    style.configure("Panel.TCheckbutton", background=colors["panel"], foreground=colors["text"])
    style.configure("TSeparator", background=colors["border"])
    style.configure("TScrollbar", background=colors["panel_alt"], troughcolor=colors["bg"])
    return colors


def configure_text(widget: tk.Text | tk.Text, colors: dict[str, str], *, mono: bool = False) -> None:
    widget.configure(
        background=colors["panel"],
        foreground=colors["text"],
        insertbackground=colors["text"],
        selectbackground=colors["selection"],
        selectforeground=colors["text"],
        relief="flat",
        borderwidth=0,
        wrap="word",
        padx=8,
        pady=6,
        font=("Consolas", 10) if mono else ("Segoe UI", 10),
    )


def style_tree_tags(tree: ttk.Treeview, colors: dict[str, str]) -> None:
    tree.tag_configure("success", foreground=colors["success"])
    tree.tag_configure("failure", foreground=colors["failure"])
    tree.tag_configure("progress", foreground=colors["progress"])
    tree.tag_configure(
        "conflict",
        background=colors["conflict_bg"],
        foreground=colors["text"],
    )
