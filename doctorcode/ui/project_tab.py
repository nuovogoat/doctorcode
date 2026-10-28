"""One project tab: prompt input, live three-view action feed, state header."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable
from tkinter import ttk

from doctorcode.engine.events import ActionEvent
from doctorcode.engine.session import AgentSession
from doctorcode.i18n import Translator
from doctorcode.ui.theme import configure_text
from doctorcode.ui.widgets import EventFeedView

SendHandler = Callable[["ProjectTab", str], None]
CloseHandler = Callable[["ProjectTab"], None]


class ProjectTab(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        session: AgentSession,
        translator: Translator,
        colors: dict[str, str],
        on_send: SendHandler,
        on_close: CloseHandler | None = None,
    ) -> None:
        super().__init__(master, style="TFrame")
        self.session = session
        self.translator = translator
        self.colors = colors
        self.on_send = on_send
        self.on_close = on_close
        self._placeholder = True
        self._build()
        self.set_events(session.feed.events())
        self.apply_colors(colors)
        self.refresh_state()

    @property
    def t(self):
        return self.translator.t

    def _build(self) -> None:
        header = ttk.Frame(self, style="Panel.TFrame")
        header.pack(fill="x", padx=0, pady=(0, 6))
        self.title_label = ttk.Label(header, text="", style="Panel.TLabel")
        self.title_label.configure(font=("Segoe UI", 12, "bold"))
        self.title_label.pack(side="left", padx=10, pady=6)
        self.path_label = ttk.Label(header, text="", style="Muted.TLabel")
        self.path_label.pack(side="left", padx=(4, 10), pady=6)
        if self.on_close is not None:
            self.close_button = ttk.Button(
                header,
                text="✕",
                width=3,
                command=lambda: self.on_close(self) if self.on_close else None,
            )
            self.close_button.pack(side="right", padx=(0, 8), pady=4)
        self.state_label = ttk.Label(header, text="", style="Panel.TLabel")
        self.state_label.pack(side="right", padx=10)
        self.count_label = ttk.Label(header, text="", style="Muted.TLabel")
        self.count_label.pack(side="right", padx=10)

        self.feed = EventFeedView(self, self.translator)
        self.feed.on_select = self._on_feed_select
        self.feed.pack(fill="both", expand=True, padx=2, pady=(0, 6))

        prompt_panel = ttk.Frame(self, style="Panel.TFrame")
        prompt_panel.pack(fill="x", padx=0, pady=(0, 2))
        self.prompt_label = ttk.Label(prompt_panel, text="", style="Panel.TLabel")
        self.prompt_label.pack(anchor="w", padx=10, pady=(6, 2))
        row = ttk.Frame(prompt_panel, style="Panel.TFrame")
        row.pack(fill="x", padx=8, pady=(0, 8))
        self.prompt_text = tk.Text(row, height=4, wrap="word")
        configure_text(self.prompt_text, self.colors)
        self.prompt_text.pack(side="left", fill="x", expand=True)
        self.send_button = ttk.Button(
            row,
            text="",
            style="Accent.TButton",
            command=self._send,
            width=12,
        )
        self.send_button.pack(side="right", fill="y", padx=(8, 0))
        self.prompt_text.bind("<Control-Return>", lambda _event: self._send())
        self.prompt_text.bind("<FocusIn>", self._clear_placeholder)
        self.prompt_text.bind("<FocusOut>", self._restore_placeholder)
        self._show_placeholder()
        self.apply_language()

    def apply_language(self) -> None:
        self.title_label.configure(text=self.session.project_name)
        self.path_label.configure(text=self.session.project_path.as_posix())
        self.prompt_label.configure(text=self.t("prompt.label"))
        self.send_button.configure(text=self.t("prompt.send"))
        self.feed.apply_language()
        self.refresh_state()

    def apply_colors(self, colors: dict[str, str]) -> None:
        self.colors = colors
        configure_text(self.prompt_text, colors)
        self.feed.apply_colors(colors)

    def set_events(self, events: Iterable[ActionEvent]) -> None:
        self.feed.set_events(events)
        self.refresh_state()

    def upsert_event(self, event: ActionEvent) -> None:
        self.feed.upsert(event)
        self.refresh_state()

    def refresh_state(self) -> None:
        busy = self.session.busy
        self.state_label.configure(
            text=self.t("toolbar.running") if busy else self.t("toolbar.idle"),
            style="Danger.TLabel" if busy else "Success.TLabel",
        )
        auto = self.t("toolbar.auto_on") if self.session.auto_mode else self.t("toolbar.auto_off")
        self.count_label.configure(
            text=f"{auto}  |  {self.t('gui.status_events', count=len(self.session.feed))}"
        )
        self._sync_warning()
        self.send_button.configure(state="disabled" if busy else "normal")

    def _on_feed_select(self, _event: object) -> None:
        self._sync_warning()

    def _sync_warning(self) -> None:
        event = self.feed.selected_event()
        self.feed.warning_label.configure(
            text=(
                self.t("feed.warning")
                if event is not None and event.has_status_summary_conflict
                else ""
            )
        )

    def _show_placeholder(self) -> None:
        self._placeholder = True
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", self.t("prompt.placeholder"))
        self.prompt_text.configure(foreground=self.colors.get("muted", "#888888"))

    def _clear_placeholder(self, _event: tk.Event[tk.Misc]) -> None:
        if self._placeholder:
            self._placeholder = False
            self.prompt_text.delete("1.0", "end")
            self.prompt_text.configure(foreground=self.colors.get("text", "#ffffff"))

    def _restore_placeholder(self, _event: tk.Event[tk.Misc]) -> None:
        if not self._placeholder and not self.prompt_text.get("1.0", "end").strip():
            self._show_placeholder()

    def prompt_value(self) -> str:
        if self._placeholder:
            return ""
        return self.prompt_text.get("1.0", "end").strip()

    def clear_prompt(self) -> None:
        self._placeholder = False
        self.prompt_text.delete("1.0", "end")
        self._show_placeholder()

    def submit(self) -> None:
        self._send()

    def _send(self) -> None:
        prompt = self.prompt_value()
        if prompt:
            self.on_send(self, prompt)
