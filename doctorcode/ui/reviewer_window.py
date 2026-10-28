"""Independent read-only reviewer window (separate endpoint from the writer)."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from doctorcode.engine.events import ActionEvent, ActionStatus, ActionType
from doctorcode.engine.feed import ActionFeed
from doctorcode.engine.reviewer import ReviewerService
from doctorcode.ui.feed_model import selected_action_material
from doctorcode.ui.theme import configure_text


class ReviewerWindow(tk.Toplevel):
    def __init__(self, app: Any) -> None:
        super().__init__(app.root)
        self.app = app
        self.translator = app.translator
        self.colors = app.colors
        self._texts: list[tuple[Any, str]] = []
        self._busy = False
        self._future = None
        self.title(self.translator.t("reviewer.title"))
        self.geometry("900x760")
        self.minsize(760, 620)
        self.transient(app.root)
        self._build()
        self.apply_language()
        self.apply_colors()
        self._bind_drop()

    @property
    def t(self):
        return self.translator.t

    def _register(self, widget: Any, key: str) -> Any:
        self._texts.append((widget, key))
        return widget

    def _label(self, master: Any, key: str, **kwargs: Any) -> ttk.Label:
        label = ttk.Label(master, text="", **kwargs)
        self._register(label, key)
        return label

    def _build(self) -> None:
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=12, pady=10)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        self.model_label = ttk.Label(header, text="", style="Muted.TLabel")
        self.model_label.pack(side="left")
        self.status_label = ttk.Label(header, text="", style="Muted.TLabel")
        self.status_label.pack(side="right")

        self._label(outer, "reviewer.instructions").pack(anchor="w", pady=(10, 2))
        self.instructions = tk.Text(outer, height=3, wrap="word")
        self.instructions.pack(fill="x")

        material_header = ttk.Frame(outer)
        material_header.pack(fill="x", pady=(10, 2))
        self._label(material_header, "reviewer.material").pack(side="left")
        self.dnd_hint = self._label(
            material_header, "reviewer.drop_hint", style="Muted.TLabel"
        )
        self.dnd_hint.pack(side="right")

        self.material = tk.Text(outer, height=15, wrap="word")
        self.material.pack(fill="both", expand=True)
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=6)
        self.attach_button = ttk.Button(actions, text="", command=self._attach)
        self._register(self.attach_button, "reviewer.attach")
        self.attach_button.pack(side="left", padx=(0, 6))
        self.take_button = ttk.Button(
            actions, text="", command=lambda: self._take_event(True)
        )
        self._register(self.take_button, "reviewer.take_event")
        self.take_button.pack(side="left", padx=6)
        self.raw_button = ttk.Button(
            actions, text="", command=lambda: self._take_event(False)
        )
        self._register(self.raw_button, "reviewer.attach_raw")
        self.raw_button.pack(side="left", padx=6)
        self.context_button = ttk.Button(actions, text="", command=self._take_context)
        self._register(self.context_button, "reviewer.session_context")
        self.context_button.pack(side="left", padx=6)
        self.run_button = ttk.Button(
            actions,
            text="",
            style="Accent.TButton",
            command=self._run,
        )
        self._register(self.run_button, "reviewer.run")
        self.run_button.pack(side="right")

        self._label(outer, "reviewer.result").pack(anchor="w", pady=(8, 2))
        self.result = tk.Text(outer, height=10, wrap="word", state="disabled")
        self.result.pack(fill="both", expand=True)
        self._label(outer, "reviewer.disclaimer", style="Muted.TLabel").pack(
            anchor="w", pady=(6, 0)
        )

    def apply_language(self) -> None:
        self.title(self.t("reviewer.title"))
        for widget, key in self._texts:
            try:
                widget.configure(text=self.t(key))
            except tk.TclError:
                continue
        self._update_model_label()

    def apply_colors(self) -> None:
        self.colors = self.app.colors
        configure_text(self.instructions, self.colors)
        configure_text(self.material, self.colors, mono=True)
        configure_text(self.result, self.colors, mono=True)
        self.result.configure(state="disabled")

    def _update_model_label(self) -> None:
        profile = self.app.settings_store.settings.profile("reviewer")
        model = profile.model or self.t("gui.model_none")
        self.model_label.configure(text=self.t("reviewer.model", model=model))
        if not profile.is_configured:
            self.status_label.configure(text=self.t("reviewer.no_profile"))

    def _bind_drop(self) -> None:
        try:
            from tkinterdnd2 import DND_FILES

            self.material.drop_target_register(DND_FILES)
            self.material.dnd_bind("<<Drop>>", self._drop)
            self.dnd_hint.configure(text="")
        except Exception:
            self.dnd_hint.configure(text=self.t("reviewer.drop_hint"))

    def _drop(self, event: Any) -> str:
        paths = self.tk.splitlist(event.data)
        for raw in paths:
            path = Path(str(raw))
            if path.is_file():
                self._load_file(path)
                break
        return "break"

    def _attach(self) -> None:
        path = filedialog.askopenfilename(parent=self)
        if path:
            self._load_file(Path(path))

    def _load_file(self, path: Path) -> None:
        limit = self.app.settings_store.settings.agent.max_file_bytes
        try:
            if path.stat().st_size > limit:
                raise OSError(f"file is larger than {limit} bytes")
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self.status_label.configure(text=self.t("reviewer.file_failed", error=exc))
            return
        self.material.delete("1.0", "end")
        self.material.insert("1.0", text)
        self.status_label.configure(text=self.t("reviewer.file_loaded", name=path.name))

    def _selected_event(self) -> ActionEvent | None:
        tab = self.app.selected_tab()
        return tab.feed.selected_event() if tab is not None else None

    def _take_event(self, prefer_raw: bool) -> None:
        event = self._selected_event()
        if event is None:
            self.status_label.configure(text=self.t("gui.no_event_selected"))
            return
        text = selected_action_material(event, prefer_raw=prefer_raw)
        self.material.delete("1.0", "end")
        self.material.insert("1.0", text)
        self.status_label.configure(text=f"{event.action_type.value}: {event.human_summary}")

    def _take_context(self) -> None:
        session = self.app.selected_session()
        if session is None:
            self.status_label.configure(text=self.t("gui.no_selection"))
            return
        self.material.delete("1.0", "end")
        self.material.insert("1.0", session.review_material())
        self.status_label.configure(text=session.project_name)

    def _run(self) -> None:
        if self._busy:
            return
        session = self.app.selected_session()
        if session is None:
            self.status_label.configure(text=self.t("gui.no_selection"))
            return
        profile = self.app.settings_store.settings.profile("reviewer")
        if not profile.is_configured:
            self.status_label.configure(text=self.t("reviewer.no_profile"))
            return
        material = self.material.get("1.0", "end").strip()
        if not material:
            self.status_label.configure(text=self.t("reviewer.empty"))
            return
        instructions = self.instructions.get("1.0", "end").strip()
        self._set_busy(True)
        self.status_label.configure(text=self.t("reviewer.running"))
        feed = ActionFeed()
        service = ReviewerService(
            settings_store=self.app.settings_store,
            feed=feed,
            logger=self.app.runtime.core.logger,
        )
        self._future = self.app.runner.submit(service.review(material, instructions))
        self.after(100, self._poll, session.session_id)

    def _poll(self, session_id: str) -> None:
        if not self.winfo_exists():
            return
        future = self._future
        if future is None or not future.done():
            self.after(100, self._poll, session_id)
            return
        try:
            review = future.result()
        except Exception as exc:
            self._set_busy(False)
            self.status_label.configure(text=self.t("reviewer.failed", error=exc))
            self._set_result(str(exc))
            return
        self._set_busy(False)
        if review.status is ActionStatus.SUCCESS:
            self._set_result(review.text)
            self.status_label.configure(text=self.t("reviewer.done"))
        else:
            self._set_result(self.t("reviewer.failed", error=review.error_code or ""))
            self.status_label.configure(
                text=self.t("reviewer.failed", error=review.error_code or "")
            )
        try:
            session = self.app.workspace.get(session_id)
        except KeyError:
            return
        session.feed.append(
            ActionEvent(
                session_id=session.session_id,
                action_type=ActionType.REVIEW,
                status=review.status,
                human_summary=(
                    f"Reviewer analysed the material with {review.model or 'the reviewer model'}."
                    if review.status is ActionStatus.SUCCESS
                    else f"Review failed: {review.error_code or 'unknown error'}."
                ),
                raw_payload=review.text or review.raw_payload,
                metadata={"model": review.model, "read_only": True},
            )
        )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.run_button.configure(state=state)
        self.attach_button.configure(state=state)
        self.take_button.configure(state=state)
        self.raw_button.configure(state=state)
        self.context_button.configure(state=state)

    def _set_result(self, text: str) -> None:
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text)
        self.result.configure(state="disabled")
