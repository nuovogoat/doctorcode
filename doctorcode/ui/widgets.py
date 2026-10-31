"""Reusable Tk widgets for the project feed and scrollable panels."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable
from tkinter import ttk

from doctorcode.engine.events import ActionEvent
from doctorcode.i18n import Translator
from doctorcode.ui.feed_model import (
    event_action_text,
    event_human_row,
    event_status_text,
    event_summary,
    event_time,
    key_value_rows,
    raw_text,
    status_style,
    warning_text,
)
from doctorcode.ui.theme import configure_text, style_tree_tags


class ScrollableFrame(ttk.Frame):
    """Vertically scrollable panel used by settings and mobile windows."""

    def __init__(self, master: tk.Misc, **kwargs: object) -> None:
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_body_configure(self, _event: tk.Event[tk.Misc]) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event[tk.Misc]) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def set_background(self, color: str) -> None:
        self.canvas.configure(background=color)
        self.body.configure(style="TFrame")


class EventFeedView(ttk.Frame):
    """Three synchronized views: Human, Key-Value, Raw.

    The status is authoritative.  Contradictory failure summaries are painted
    with a dedicated conflict tag and called out above the inspector.
    """

    def __init__(
        self,
        master: tk.Misc,
        translator: Translator,
        *,
        on_select: Callable[[ActionEvent | None], None] | None = None,
    ) -> None:
        super().__init__(master, style="TFrame")
        self.translator = translator
        self.on_select = on_select
        self._events: dict[str, ActionEvent] = {}
        self._colors: dict[str, str] = {}
        self._last_id: str | None = None
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self._build_human()
        self._build_key_value()
        self._build_raw()
        self._retranslate()

    def _build_human(self) -> None:
        frame = ttk.Frame(self.notebook)
        paned = ttk.Panedwindow(frame, orient="vertical")
        paned.pack(fill="both", expand=True)
        list_frame = ttk.Frame(paned)
        columns = ("time", "status", "action", "summary")
        self.human_tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        widths = {"time": 78, "status": 105, "action": 120, "summary": 620}
        anchors = {"time": "center", "status": "center", "action": "w", "summary": "w"}
        for column in columns:
            self.human_tree.heading(column, text=column, anchor="w")
            self.human_tree.column(
                column,
                width=widths[column],
                minwidth=50,
                anchor=anchors[column],
                stretch=column == "summary",
            )
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.human_tree.yview)
        self.human_tree.configure(yscrollcommand=scroll.set)
        self.human_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.human_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        paned.add(list_frame, weight=4)

        detail = ttk.Frame(paned)
        self.warning_label = ttk.Label(detail, text="", style="Danger.TLabel")
        self.warning_label.pack(anchor="w", padx=8, pady=(6, 0))
        self.details = tk.Text(detail, height=7, state="disabled")
        self.details.pack(fill="both", expand=True, padx=6, pady=6)
        paned.add(detail, weight=2)
        self.notebook.add(frame)

    def _build_key_value(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.kv_tree = ttk.Treeview(
            frame,
            columns=("field", "value"),
            show="headings",
            selectmode="browse",
        )
        self.kv_tree.column("field", width=180, minwidth=100, anchor="w")
        self.kv_tree.column("value", width=760, minwidth=200, anchor="w", stretch=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.kv_tree.yview)
        self.kv_tree.configure(yscrollcommand=scroll.set)
        self.kv_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.notebook.add(frame)

    def _build_raw(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.raw = tk.Text(frame, state="disabled", wrap="none")
        raw_y = ttk.Scrollbar(frame, orient="vertical", command=self.raw.yview)
        raw_x = ttk.Scrollbar(frame, orient="horizontal", command=self.raw.xview)
        self.raw.configure(yscrollcommand=raw_y.set, xscrollcommand=raw_x.set)
        self.raw.grid(row=0, column=0, sticky="nsew")
        raw_y.grid(row=0, column=1, sticky="ns")
        raw_x.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.notebook.add(frame)

    def _retranslate(self) -> None:
        t = self.translator.t
        self.notebook.tab(0, text=t("feed.human"))
        self.notebook.tab(1, text=t("feed.key_value"))
        self.notebook.tab(2, text=t("feed.raw"))
        for column in ("time", "status", "action", "summary"):
            labels = {
                "time": "feed.time",
                "status": "feed.status",
                "action": "feed.action",
                "summary": "feed.summary",
            }
            self.human_tree.heading(column, text=t(labels[column]))
        self.kv_tree.heading("field", text=t("feed.key_value"))
        self.kv_tree.heading("value", text=t("feed.details"))
        self._render_selected()

    def apply_colors(self, colors: dict[str, str]) -> None:
        self._colors = colors
        configure_text(self.details, colors)
        configure_text(self.raw, colors, mono=True)
        self.details.configure(state="disabled")
        self.raw.configure(state="disabled")
        style_tree_tags(self.human_tree, colors)
        style_tree_tags(self.kv_tree, colors)
        self._render_selected()

    def apply_language(self) -> None:
        self._retranslate()
        # Row cells hold rendered text, so a language switch must re-render
        # them from the stable event keys, not only the headers.
        self.set_events(list(self._events.values()))

    def set_events(self, events: Iterable[ActionEvent]) -> None:
        selected = self.selected_event_id
        self.human_tree.delete(*self.human_tree.get_children())
        self._events.clear()
        for event in events:
            self._insert(event)
        if selected and selected in self._events:
            self.select(selected)
        else:
            self._render_selected()

    def upsert(self, event: ActionEvent) -> None:
        exists = event.event_id in self._events
        self._events[event.event_id] = event
        selected = self.selected_event_id
        follow = selected is None or selected == self._last_id
        status_tag = status_style(event)
        values = event_human_row(event, self.translator)
        if exists and self.human_tree.exists(event.event_id):
            self.human_tree.item(event.event_id, values=values, tags=(status_tag,))
            if selected == event.event_id:
                self._render_selected()
            return
        self._insert(event)
        if follow:
            self.select(event.event_id)

    def _insert(self, event: ActionEvent) -> None:
        self._events[event.event_id] = event
        self.human_tree.insert(
            "",
            "end",
            iid=event.event_id,
            values=event_human_row(event, self.translator),
            tags=(status_style(event),),
        )
        self._last_id = event.event_id
        self.human_tree.see(event.event_id)

    def remove_missing(self, ids: set[str]) -> None:
        for event_id in list(self._events):
            if event_id not in ids:
                self._events.pop(event_id, None)
                if self.human_tree.exists(event_id):
                    self.human_tree.delete(event_id)

    @property
    def selected_event_id(self) -> str | None:
        selection = self.human_tree.selection()
        return selection[0] if selection else None

    def selected_event(self) -> ActionEvent | None:
        event_id = self.selected_event_id
        return self._events.get(event_id) if event_id else None

    def select(self, event_id: str) -> None:
        if not self.human_tree.exists(event_id):
            return
        self.human_tree.selection_set(event_id)
        self.human_tree.focus(event_id)
        self.human_tree.see(event_id)
        self._render_selected()

    def _on_tree_select(self, _event: tk.Event[tk.Misc]) -> None:
        self._render_selected()
        if self.on_select is not None:
            self.on_select(self.selected_event())

    def _render_selected(self) -> None:
        event = self.selected_event()
        if event is None:
            message = self.translator.t("feed.no_selection")
            self._set_text(self.details, message)
            self._set_text(self.raw, message)
            self.warning_label.configure(text="")
            self.kv_tree.delete(*self.kv_tree.get_children())
            if self.on_select is not None:
                self.on_select(None)
            return
        warning = warning_text(event, self.translator)
        self.warning_label.configure(text=warning)
        details = (
            f"{event_status_text(event, self.translator)} | "
            f"{event_action_text(event, self.translator)} | "
            f"{event_time(event)}\n\n"
            f"{event_summary(event, self.translator)}"
        )
        if event.duration_ms is not None:
            details += f"\n\n{event.duration_ms:.0f} ms"
        self._set_text(self.details, details)
        self._set_text(self.raw, raw_text(event, self.translator))
        self.kv_tree.delete(*self.kv_tree.get_children())
        for field, value in key_value_rows(event):
            self.kv_tree.insert("", "end", values=(field, value))

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")
