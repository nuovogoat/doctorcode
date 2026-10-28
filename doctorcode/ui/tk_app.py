"""Default Tkinter desktop application.

The GUI is a thin, replaceable shell over the engine and the LAN bridge.  All
long-running work happens on the dedicated asyncio runner or the core
executor; the Tk thread only consumes queues, paints widgets, and resolves
approval dialogs.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import sys
import tkinter as tk
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from doctorcode import __version__
from doctorcode.engine.events import ActionEvent, ActionStatus
from doctorcode.engine.kimi_import import KimiImportService
from doctorcode.engine.llm_client import LLMError, load_models
from doctorcode.engine.session import AgentSession
from doctorcode.engine.workspace import WorkspaceManager
from doctorcode.i18n import LANGUAGES, Translator, resolve_language
from doctorcode.runtime import AppRuntime, create_runtime
from doctorcode.server.controller import MobileServerController, ServerInfo
from doctorcode.ui.approval import ApprovalBroker
from doctorcode.ui.import_window import KimiImportWindow
from doctorcode.ui.mobile_window import MobileWindow
from doctorcode.ui.modules_window import ModulesWindow
from doctorcode.ui.project_tab import ProjectTab
from doctorcode.ui.reviewer_window import ReviewerWindow
from doctorcode.ui.runner import AsyncRunner
from doctorcode.ui.settings_window import AboutWindow, SettingsWindow
from doctorcode.ui.theme import apply_theme
from doctorcode.ui.windows import WindowManager

logger = logging.getLogger(__name__)


def _create_root() -> tk.Tk:
    """Use drag-and-drop when the optional module is present."""
    try:
        from tkinterdnd2 import TkinterDnD
    except ImportError:
        return tk.Tk()
    return TkinterDnD.Tk()


class DoctorCodeApp:
    """The main window and the process-wide wiring for one desktop run."""

    def __init__(self, runtime: AppRuntime, *, root: tk.Tk | None = None) -> None:
        self.runtime = runtime
        self.settings_store = runtime.settings_store
        self.data_dir = runtime.data_dir
        self.version = __version__
        self.logger = runtime.core.logger
        self.translator = Translator(self.settings_store.settings.ui.language)
        self.root = root or _create_root()
        self.colors: dict[str, str] = {}
        self._closed = False
        self._closing = False
        self._syncing_controls = False
        self._fetching_models = False
        self._switching: set[str] = set()
        self._models: list[str] = []
        self._text_refs: list[tuple[Any, str]] = []
        self._tabs: dict[str, ProjectTab] = {}
        self._event_queue: queue.Queue[ActionEvent] = queue.Queue()
        self._run_futures: dict[str, Future[Any]] = {}

        self.root.withdraw()
        self.root.geometry("1280x840")
        self.root.minsize(980, 640)
        self.colors = apply_theme(self.root, self.settings_store.settings.ui.theme)

        self.runner = AsyncRunner(self.logger)
        self.workspace = WorkspaceManager(
            app_data_dir=runtime.data_dir,
            settings_store=self.settings_store,
            executor=runtime.executor,
            logger=self.logger,
        )
        self.workspace.subscribe_all(self._event_queue.put)
        self.kimi_import = KimiImportService(
            workspace=self.workspace,
            data_dir=runtime.data_dir,
        )
        self.approvals = ApprovalBroker()
        self.mobile_controller = MobileServerController(
            manager=self.workspace,
            settings_store=self.settings_store,
            translator=self.translator,
            logger=self.logger,
        )
        self.windows = WindowManager(self.root, self.translator)

        self._build_toolbar()
        self._build_statusbar()
        self._build_body()
        self._build_menu()
        self._bind_shortcuts()
        self._retranslate()
        self._restore_last_project()
        self._sync_empty_state()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(80, self._poll_events)
        self.root.after(120, self._poll_approvals)
        self.root.after(400, self._maybe_auto_start_server)
        self.root.deiconify()
        self.root.lift()
        self._set_detail(
            self.t("gui.status_events", count=0) if not self._tabs else ""
        )
        self.logger.info("ui.started", context={"version": __version__})

    # --------------------------------------------------------------- helpers
    def t(self, key: str, **params: object) -> str:
        return self.translator.t(key, **params)

    def selected_tab(self) -> ProjectTab | None:
        selected = self.notebook.select()
        if not selected:
            return None
        try:
            widget = self.notebook.nametowidget(selected)
        except (KeyError, tk.TclError):
            return None
        return widget if isinstance(widget, ProjectTab) else None

    def selected_session(self) -> AgentSession | None:
        tab = self.selected_tab()
        return tab.session if tab is not None else None

    def _register_text(self, widget: Any, key: str) -> Any:
        self._text_refs.append((widget, key))
        return widget

    def _text_button(
        self,
        master: tk.Misc,
        key: str,
        command: Callable[[], None],
        *,
        style: str = "TButton",
        width: int | None = None,
    ) -> ttk.Button:
        button = ttk.Button(master, text="", command=command, style=style)
        if width is not None:
            button.configure(width=width)
        return self._register_text(button, key)

    def _text_label(self, master: tk.Misc, key: str, **kwargs: Any) -> ttk.Label:
        label = ttk.Label(master, text="", **kwargs)
        return self._register_text(label, key)

    # ------------------------------------------------------------------- UI
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, style="Panel.TFrame", padding=(8, 6))
        bar.pack(fill="x", side="top")
        left = ttk.Frame(bar, style="Panel.TFrame")
        left.pack(side="left")
        right = ttk.Frame(bar, style="Panel.TFrame")
        right.pack(side="right")

        self._text_button(left, "toolbar.open", self.open_project_dialog).pack(
            side="left", padx=(0, 4)
        )
        self._text_button(left, "toolbar.add", self.open_project_dialog).pack(
            side="left", padx=(0, 10)
        )
        self.run_button = self._text_button(
            left, "toolbar.run", self.run_selected, style="Accent.TButton"
        )
        self.run_button.pack(side="left", padx=(0, 4))
        self.stop_button = self._text_button(
            left, "toolbar.stop", self.stop_selected, style="Danger.TButton", width=7
        )
        self.stop_button.pack(side="left", padx=(0, 12))
        self.auto_var = tk.BooleanVar(value=False)
        self.auto_check = ttk.Checkbutton(
            left,
            text="",
            variable=self.auto_var,
            command=self._toggle_auto,
        )
        self._register_text(self.auto_check, "toolbar.no_watch")
        self.auto_check.pack(side="left")

        model_label = self._text_label(right, "toolbar.model", style="Panel.TLabel")
        model_label.pack(side="left", padx=(0, 6))
        self.model_var = tk.StringVar(value="")
        self.model_combo = ttk.Combobox(
            right,
            textvariable=self.model_var,
            state="readonly",
            width=30,
        )
        self.model_combo.pack(side="left", padx=(0, 4))
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_chosen)
        self._text_button(
            right, "toolbar.refresh_models", self.refresh_models
        ).pack(side="left", padx=(0, 14))

        self.language_frame = ttk.Frame(right, style="Panel.TFrame")
        self.language_frame.pack(side="left")
        self._language_buttons: dict[str, ttk.Button] = {}
        for code in LANGUAGES:
            button = ttk.Button(
                self.language_frame,
                text=self.t(f"language.flag.{code}"),
                width=3,
                command=lambda code=code: self.set_language(code),
            )
            button.pack(side="left", padx=2)
            self._language_buttons[code] = button

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self.root, style="Panel.TFrame", padding=(10, 4))
        bar.pack(fill="x", side="bottom")
        self.status_label = ttk.Label(bar, text="", style="Muted.TLabel")
        self.status_label.pack(side="left")
        self.detail_label = ttk.Label(bar, text="", style="Muted.TLabel")
        self.detail_label.pack(side="right")

    def _build_body(self) -> None:
        self.body = ttk.Frame(self.root)
        self.body.pack(fill="both", expand=True, padx=8, pady=6)
        self.notebook = ttk.Notebook(self.body)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.center = ttk.Frame(self.body)
        self.center_title = self._text_label(
            self.center, "gui.center_text", style="Title.TLabel"
        )
        self.center_title.pack(pady=(140, 8))
        self.center_hint = self._text_label(
            self.center, "gui.open_project_hint", style="Muted.TLabel"
        )
        self.center_hint.pack(pady=(0, 18))
        self._text_button(
            self.center,
            "toolbar.open",
            self.open_project_dialog,
            style="Accent.TButton",
        ).pack()

    def _build_menu(self) -> None:
        if not hasattr(self, "menubar"):
            self.menubar = tk.Menu(self.root)
            self.root.configure(menu=self.menubar)
        else:
            self.menubar.delete(0, "end")

        t = self.t
        file_menu = tk.Menu(self.menubar, tearoff=False)
        file_menu.add_command(
            label=t("toolbar.open"),
            accelerator="Ctrl+O",
            command=self.open_project_dialog,
        )
        file_menu.add_command(
            label=t("toolbar.add"),
            accelerator="Ctrl+N",
            command=self.open_project_dialog,
        )
        file_menu.add_command(
            label=t("gui.close_tab"),
            accelerator="Ctrl+W",
            command=self.close_selected_tab,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label=t("gui.create_handoff"),
            command=self.create_handoff,
        )
        file_menu.add_command(
            label=t("menu.import_kimi"),
            command=self.open_kimi_import,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label=t("menu.quit"),
            accelerator="Alt+F4",
            command=self.close,
        )
        self.menubar.add_cascade(label=t("menu.file"), menu=file_menu)

        tools_menu = tk.Menu(self.menubar, tearoff=False)
        tools_menu.add_command(label=t("menu.settings"), command=self.open_settings)
        tools_menu.add_command(label=t("menu.modules"), command=self.open_modules)
        tools_menu.add_command(label=t("menu.reviewer"), command=self.open_reviewer)
        tools_menu.add_command(label=t("menu.mobile"), command=self.open_mobile)
        self.menubar.add_cascade(label=t("menu.tools"), menu=tools_menu)

        language_menu = tk.Menu(self.menubar, tearoff=False)
        self.language_menu_var = tk.StringVar(value=self.translator.language)
        for code in LANGUAGES:
            language_menu.add_radiobutton(
                label=f"{t(f'language.flag.{code}')}  {t(f'language.{code}')}",
                variable=self.language_menu_var,
                value=code,
                command=lambda code=code: self.set_language(code),
            )
        self.menubar.add_cascade(label=t("menu.language"), menu=language_menu)

        help_menu = tk.Menu(self.menubar, tearoff=False)
        help_menu.add_command(label=t("gui.about"), command=self.open_about)
        self.menubar.add_cascade(label=t("menu.help"), menu=help_menu)

    def _bind_shortcuts(self) -> None:
        def guarded(callback: Callable[[], None]) -> Callable[[tk.Event[Any]], str | None]:
            def handler(_event: tk.Event[Any]) -> str | None:
                focus = self.root.focus_get()
                if isinstance(focus, (tk.Text, ttk.Entry, ttk.Combobox)):
                    return None
                callback()
                return "break"

            return handler

        self.root.bind("<Control-o>", guarded(self.open_project_dialog))
        self.root.bind("<Control-n>", guarded(self.open_project_dialog))
        self.root.bind("<Control-w>", guarded(self.close_selected_tab))
        self.root.bind("<Control-comma>", guarded(self.open_settings))

    # -------------------------------------------------------------- language
    def set_language(self, code: str) -> None:
        if code not in LANGUAGES:
            return
        try:
            self.settings_store.set_language(code)
        except Exception as exc:
            messagebox.showerror(
                self.t("dialog.error"),
                self.t("language.switch_failed", error=exc),
                parent=self.root,
            )
            return
        self.translator.set_language(code)
        self._retranslate()
        self._set_status(self.t("gui.language_changed"))

    def preferences_changed(self) -> None:
        """Settings window saved: re-apply language, theme, and model choices."""
        if self._closing or self._closed:
            return
        settings = self.settings_store.settings
        self.translator.set_language(resolve_language(settings.ui.language))
        self._apply_theme()
        self._retranslate()
        self._refresh_model_choices()
        self._set_status(self.t("gui.settings_saved"))

    def _retranslate(self) -> None:
        self.root.title(self.t("app.title"))
        for widget, key in self._text_refs:
            with contextlib.suppress(tk.TclError):
                widget.configure(text=self.t(key))
        self._build_menu()
        for tab in self._tabs.values():
            with contextlib.suppress(tk.TclError):
                tab.apply_language()
        for window in self.windows.all():
            apply_language = getattr(window, "apply_language", None)
            if callable(apply_language):
                with contextlib.suppress(tk.TclError):
                    apply_language()
        self._update_language_buttons()
        self._sync_title()
        self._refresh_toolbar()

    def _update_language_buttons(self) -> None:
        for code, button in self._language_buttons.items():
            active = code == self.translator.language
            with contextlib.suppress(tk.TclError):
                button.configure(style="Accent.TButton" if active else "TButton")

    def _apply_theme(self) -> None:
        self.colors = apply_theme(self.root, self.settings_store.settings.ui.theme)
        for tab in self._tabs.values():
            with contextlib.suppress(tk.TclError):
                tab.apply_colors(self.colors)
        for window in self.windows.all():
            apply_colors = getattr(window, "apply_colors", None)
            if callable(apply_colors):
                with contextlib.suppress(tk.TclError):
                    apply_colors()

    # ------------------------------------------------------------------ tabs
    def open_project_dialog(self) -> None:
        initial = self.settings_store.settings.ui.last_project
        initial_dir = initial if initial and Path(initial).is_dir() else str(Path.home())
        path = filedialog.askdirectory(
            parent=self.root,
            title=self.t("project.open_title"),
            initialdir=initial_dir,
            mustexist=True,
        )
        if path:
            self.open_project(path)

    def open_project(self, folder: str | Path, *, announce: bool = True) -> ProjectTab | None:
        try:
            session = self.workspace.open_project(folder)
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                self.t("dialog.error"),
                self.t("project.open_failed", error=exc),
                parent=self.root,
            )
            return None
        tab = self._ensure_tab(session, select=True)
        if announce:
            self._set_status(self.t("gui.project_open", name=session.project_name))
        self._refresh_toolbar()
        return tab

    def _ensure_tab(self, session: AgentSession, *, select: bool = True) -> ProjectTab:
        existing = self._tabs.get(session.session_id)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    if select:
                        self.notebook.select(existing)
                        self._sync_title()
                    return existing
            except tk.TclError:
                pass
        tab = ProjectTab(
            self.notebook,
            session=session,
            translator=self.translator,
            colors=self.colors,
            on_send=self._send_prompt,
            on_close=self.close_tab,
        )
        self._tabs[session.session_id] = tab
        self.notebook.add(tab, text=session.project_name)
        session.set_approval_callback(self.approvals.request)
        self._sync_empty_state()
        if select:
            self.notebook.select(tab)
            self._sync_title()
        return tab

    def _restore_last_project(self) -> None:
        last = self.settings_store.settings.ui.last_project
        if not last:
            return
        path = Path(last)
        if not path.is_dir():
            return
        try:
            self.open_project(path, announce=False)
        except Exception:
            self.logger.exception("ui.restore_project_failed")

    def close_tab(self, tab: ProjectTab) -> None:
        session = tab.session
        if session.busy and not messagebox.askyesno(
            self.t("dialog.warning"),
            self.t("gui.close_tab_confirm", name=session.project_name),
            parent=self.root,
        ):
            return
        self.workspace.close_project(session.session_id)
        self._run_futures.pop(session.session_id, None)
        self._tabs.pop(session.session_id, None)
        with contextlib.suppress(tk.TclError):
            tab.destroy()
        self._sync_empty_state()
        self._sync_title()
        self._refresh_toolbar()
        self._set_status(self.t("gui.no_projects_open"))

    def close_selected_tab(self) -> None:
        tab = self.selected_tab()
        if tab is None:
            self._set_status(self.t("gui.no_selection"))
            return
        self.close_tab(tab)

    def _sync_empty_state(self) -> None:
        if self._tabs:
            self.center.pack_forget()
            if not self.notebook.winfo_manager():
                self.notebook.pack(fill="both", expand=True)
        else:
            self.notebook.pack_forget()
            if not self.center.winfo_manager():
                self.center.pack(fill="both", expand=True)
            self._set_detail(self.t("gui.no_projects_open"))

    def _sync_title(self) -> None:
        tab = self.selected_tab()
        if tab is None:
            self.root.title(self.t("app.title"))
        else:
            self.root.title(f"{self.t('app.title')} — {tab.session.project_name}")

    def _on_tab_changed(self, _event: tk.Event[Any]) -> None:
        self._sync_title()
        self._refresh_toolbar()

    # --------------------------------------------------------------- prompts
    def _send_prompt(self, tab: ProjectTab, prompt: str) -> None:
        session = tab.session
        if session.busy:
            self._set_status(self.t("gui.busy"), error=True)
            tab.refresh_state()
            return
        future = self.runner.submit_tracked(session.run_prompt(prompt))
        self._run_futures[session.session_id] = future
        tab.clear_prompt()
        tab.send_button.configure(state="disabled")
        self._set_status(self.t("toolbar.running"))
        self._refresh_toolbar()
        self._watch_future(
            future,
            on_done=lambda result: self._prompt_done(tab, result),
            on_error=lambda exc: self._prompt_failed(tab, exc),
        )

    def _prompt_done(self, tab: ProjectTab, result: Any) -> None:
        self._run_futures.pop(tab.session.session_id, None)
        with contextlib.suppress(tk.TclError):
            tab.refresh_state()
        self._refresh_toolbar()
        if getattr(result, "status", None) is ActionStatus.SUCCESS:
            self._set_status(self.t("toolbar.idle"))
        elif getattr(result, "error_code", "") == "CANCELLED":
            self._set_status(self.t("gui.stop_sent"))
        else:
            self._set_status(
                self.t("gui.error") + f": {getattr(result, 'error_code', '') or ''}",
                error=True,
            )

    def _prompt_failed(self, tab: ProjectTab, exc: Exception) -> None:
        self.logger.exception(
            "ui.prompt_failed",
            context={"error": f"{type(exc).__name__}: {exc}"},
        )
        self._run_futures.pop(tab.session.session_id, None)
        with contextlib.suppress(tk.TclError):
            tab.refresh_state()
        self._refresh_toolbar()
        self._set_status(self.t("gui.unknown_error", error=exc), error=True)

    def run_selected(self) -> None:
        tab = self.selected_tab()
        if tab is None:
            self._set_status(self.t("gui.no_selection"), error=True)
            return
        tab.submit()

    def stop_selected(self) -> None:
        session = self.selected_session()
        if session is None:
            self._set_status(self.t("gui.no_selection"), error=True)
            return
        stopped = session.stop()
        self._set_status(
            self.t("gui.stop_sent") if stopped else self.t("gui.stop_idle"),
            error=not stopped,
        )
        tab = self.selected_tab()
        if tab is not None:
            tab.refresh_state()
        self._refresh_toolbar()

    def _toggle_auto(self) -> None:
        if self._syncing_controls:
            return
        tab = self.selected_tab()
        if tab is None:
            self._set_auto_without_callback(False)
            self._set_status(self.t("gui.no_selection"), error=True)
            return
        session = tab.session
        desired = bool(self.auto_var.get())
        if desired and not session.auto_mode and not messagebox.askyesno(
            self.t("toolbar.no_watch"),
            self.t("gui.auto_confirm"),
            parent=self.root,
        ):
            self._set_auto_without_callback(False)
            return
        session.set_auto_mode(desired)
        tab.refresh_state()
        self._refresh_toolbar()

    def _set_auto_without_callback(self, value: bool) -> None:
        self._syncing_controls = True
        try:
            self.auto_var.set(value)
        finally:
            self._syncing_controls = False

    # ----------------------------------------------------------------- model
    def _refresh_toolbar(self) -> None:
        if not hasattr(self, "model_combo"):
            return
        tab = self.selected_tab()
        session = tab.session if tab is not None else None
        profile = self.settings_store.settings.profile("writer")
        self._syncing_controls = True
        try:
            if session is not None:
                self.model_var.set(session.model)
                self.auto_var.set(session.auto_mode)
                self.auto_check.configure(state="normal")
                self.model_combo.configure(state="readonly")
                busy = session.busy
                self.run_button.configure(state="disabled" if busy else "normal")
                self.stop_button.configure(state="normal" if busy else "disabled")
            else:
                self.model_var.set(profile.model)
                self.auto_var.set(False)
                self.auto_check.configure(state="disabled")
                self.model_combo.configure(state="disabled")
                self.run_button.configure(state="disabled")
                self.stop_button.configure(state="disabled")
        finally:
            self._syncing_controls = False
        self._refresh_model_choices()
        if session is not None:
            self._set_detail(self.t("gui.status_events", count=len(session.feed)))
        else:
            self._set_detail(self.t("gui.no_projects_open"))
        self._update_language_buttons()

    def _refresh_model_choices(self) -> None:
        tab = self.selected_tab()
        session = tab.session if tab is not None else None
        profile = self.settings_store.settings.profile("writer")
        values = [
            value
            for value in dict.fromkeys(
                [
                    session.model if session is not None else "",
                    profile.model,
                    *self._models,
                ]
            )
            if value
        ]
        with contextlib.suppress(tk.TclError):
            self.model_combo.configure(values=values)

    def _on_model_chosen(self, _event: tk.Event[Any] | None = None) -> None:
        if self._syncing_controls:
            return
        tab = self.selected_tab()
        if tab is None:
            return
        session = tab.session
        model = self.model_var.get().strip()
        if not model or model == session.model:
            return
        if session.busy:
            messagebox.showwarning(
                self.t("dialog.warning"),
                self.t("gui.busy"),
                parent=self.root,
            )
            self._refresh_toolbar()
            return
        if session.session_id in self._switching:
            return
        self._switching.add(session.session_id)
        self._set_status(self.t("gui.model_switch_running"))
        future = self.runtime.executor.submit(session.switch_model, model)
        self._watch_future(
            future,
            on_done=lambda _snapshot: self._model_switched(tab, model),
            on_error=lambda exc: self._model_switch_failed(tab, exc),
        )

    def _model_switched(self, tab: ProjectTab, model: str) -> None:
        self._switching.discard(tab.session.session_id)
        with contextlib.suppress(tk.TclError):
            tab.refresh_state()
        self._refresh_toolbar()
        self._set_status(self.t("gui.handoff_done", model=model))

    def _model_switch_failed(self, tab: ProjectTab, exc: Exception) -> None:
        self._switching.discard(tab.session.session_id)
        self._refresh_toolbar()
        self._set_status(self.t("gui.model_switch_failed", error=exc), error=True)
        messagebox.showerror(
            self.t("dialog.error"),
            self.t("gui.model_switch_failed", error=exc),
            parent=self.root,
        )

    def refresh_models(self) -> None:
        if self._fetching_models:
            return
        profile = self.settings_store.settings.profile("writer")
        if not profile.base_url:
            self._set_status(self.t("gui.settings_required"), error=True)
            return

        async def load() -> list[str]:
            return await load_models(profile, timeout=30.0)

        self._fetching_models = True
        self._set_status(self.t("settings.fetching"))
        future = self.runner.submit_tracked(load())
        self._watch_future(
            future,
            on_done=self._models_loaded,
            on_error=self._models_failed,
        )

    def _models_loaded(self, models: list[str]) -> None:
        self._fetching_models = False
        self._models = list(dict.fromkeys(models))
        self._refresh_model_choices()
        if self._models:
            self._set_status(
                self.t("settings.models_fetched", count=len(self._models))
            )
        else:
            self._set_status(self.t("settings.no_models"), error=True)

    def _models_failed(self, exc: Exception) -> None:
        self._fetching_models = False
        text = self.t("gui.model_refresh_failed", error=exc)
        if isinstance(exc, LLMError):
            self.logger.warning(
                "ui.models_failed",
                context={"code": exc.code, "error": str(exc)},
            )
        self._set_status(text, error=True)

    def create_handoff(self) -> None:
        tab = self.selected_tab()
        if tab is None:
            self._set_status(self.t("gui.no_selection"), error=True)
            return
        session = tab.session
        if session.busy:
            self._set_status(self.t("gui.busy"), error=True)
            return
        self._set_status(self.t("gui.handoff_running"))
        future = self.runtime.executor.submit(session.create_handoff, "manual")
        self._watch_future(
            future,
            on_done=lambda path: self._handoff_saved(tab, path),
            on_error=lambda exc: self._handoff_failed(exc),
        )

    def _handoff_saved(self, tab: ProjectTab, path: Path) -> None:
        with contextlib.suppress(tk.TclError):
            tab.refresh_state()
        self._refresh_toolbar()
        self._set_status(self.t("gui.handoff_saved", path=path))

    def _handoff_failed(self, exc: Exception) -> None:
        self._set_status(self.t("gui.handoff_failed", error=exc), error=True)
        messagebox.showerror(
            self.t("dialog.error"),
            self.t("gui.handoff_failed", error=exc),
            parent=self.root,
        )

    # --------------------------------------------------------------- windows
    def open_settings(self) -> None:
        self.windows.open("settings", lambda: SettingsWindow(self))

    def open_modules(self) -> None:
        self.windows.open("modules", lambda: ModulesWindow(self))

    def open_reviewer(self) -> None:
        self.windows.open("reviewer", lambda: ReviewerWindow(self))

    def open_mobile(self) -> None:
        self.windows.open("mobile", lambda: MobileWindow(self))

    def open_kimi_import(self) -> None:
        self.windows.open("kimi-import", lambda: KimiImportWindow(self))

    def open_about(self) -> None:
        self.windows.open("about", lambda: AboutWindow(self))

    # ----------------------------------------------------------------- queues
    def _poll_events(self) -> None:
        if self._closing or self._closed:
            return
        try:
            for _ in range(250):
                try:
                    event = self._event_queue.get_nowait()
                except queue.Empty:
                    break
                self._apply_event(event)
        except Exception:
            self.logger.exception("ui.event_poll_failed")
        finally:
            if not self._closing and not self._closed:
                self.root.after(80, self._poll_events)

    def _apply_event(self, event: ActionEvent) -> None:
        try:
            session = self.workspace.get(event.session_id)
        except KeyError:
            return
        tab = self._tabs.get(event.session_id)
        if tab is None or not tab.winfo_exists():
            tab = self._ensure_tab(session, select=False)
        tab.upsert_event(event)
        if self.selected_session() is session:
            self.stop_button.configure(
                state="normal" if session.busy else "disabled"
            )
            self.run_button.configure(
                state="disabled" if session.busy else "normal"
            )
            self._set_detail(self.t("gui.status_events", count=len(session.feed)))
            if tab.session.auto_mode != bool(self.auto_var.get()):
                self._set_auto_without_callback(tab.session.auto_mode)

    def _poll_approvals(self) -> None:
        if self._closing or self._closed:
            return
        pending = self.approvals.next_pending()
        if pending is not None:
            details = "\n".join(
                f"{key}: {value}" for key, value in pending.request.details.items()
            ) or self.t("common.not_available")
            body = self.t(
                "dialog.approval_body",
                summary=pending.request.title,
                details=details,
            )
            try:
                approved = messagebox.askyesno(
                    self.t("dialog.approval_title"),
                    body,
                    parent=self.root,
                )
            except tk.TclError:
                approved = False
            pending.resolve(approved)
            if not self._closing and not self._closed:
                self.root.after(20, self._poll_approvals)
            return
        self.root.after(120, self._poll_approvals)

    def _watch_future(
        self,
        future: Future[Any],
        *,
        on_done: Callable[[Any], None],
        on_error: Callable[[Exception], None],
        interval: int = 100,
    ) -> None:
        def poll() -> None:
            if self._closing or self._closed:
                return
            if not future.done():
                self.root.after(interval, poll)
                return
            try:
                value = future.result()
            except Exception as exc:
                try:
                    on_error(exc)
                except Exception:
                    self.logger.exception("ui.future_error_handler_failed")
                return
            try:
                on_done(value)
            except Exception:
                self.logger.exception("ui.future_done_handler_failed")

        self.root.after(interval, poll)

    # ----------------------------------------------------------------- server
    def _maybe_auto_start_server(self) -> None:
        if self._closing or self._closed:
            return
        if not self.settings_store.settings.server.auto_start:
            return
        if self.mobile_controller.running:
            return
        self._set_status(self.t("mobile.status_starting"))
        future = self.runtime.executor.submit(self.mobile_controller.start)
        self._watch_future(
            future,
            on_done=self._server_started,
            on_error=self._server_failed,
        )

    def _server_started(self, info: ServerInfo) -> None:
        urls = [url for url in info.urls if "127.0.0.1" not in url]
        address = urls[0] if urls else f"http://{info.host}:{info.port}"
        self._set_status(self.t("mobile.status_running", address=address))
        self.logger.info(
            "ui.mobile_auto_started",
            context={"address": address, "port": info.port},
        )

    def _server_failed(self, exc: Exception) -> None:
        self.logger.exception(
            "ui.mobile_auto_start_failed",
            context={"error": f"{type(exc).__name__}: {exc}"},
        )
        self._set_status(self.t("mobile.server_error", error=exc), error=True)

    # ----------------------------------------------------------------- status
    def _set_status(self, text: str, *, error: bool = False) -> None:
        with contextlib.suppress(tk.TclError):
            self.status_label.configure(
                text=text,
                style="Danger.TLabel" if error else "Muted.TLabel",
            )

    def _set_detail(self, text: str) -> None:
        with contextlib.suppress(tk.TclError):
            self.detail_label.configure(text=text)

    # --------------------------------------------------------------- shutdown
    def close(self, *, confirm: bool = True) -> None:
        if self._closed or self._closing:
            return
        sessions = self.workspace.list()
        if (
            confirm
            and any(session.busy for session in sessions)
            and not messagebox.askyesno(
                self.t("dialog.quit_title"),
                self.t("dialog.quit_body"),
                parent=self.root,
            )
        ):
            return
        self._closing = True
        try:
            self.approvals.close()
            for session in sessions:
                session.stop()
            with contextlib.suppress(Exception):
                self.mobile_controller.stop()
            self.workspace.shutdown()
            self.windows.close_all()
            self.runner.shutdown()
            with contextlib.suppress(tk.TclError):
                self.root.destroy()
        finally:
            self.runtime.close()
            self._closed = True
            self.logger.info("ui.stopped")

    def run(self) -> None:
        self.root.mainloop()
        if not self._closed:
            self.close(confirm=False)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``doctorcode-gui`` and ``python -m doctorcode.ui.tk_app``."""
    del argv
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print(Translator().t("gui.unavailable"), file=sys.stderr)
        return 1
    runtime: AppRuntime | None = None
    try:
        runtime = create_runtime()
    except Exception as exc:
        print(Translator().t("startup.core_failed", error=exc), file=sys.stderr)
        return 2
    try:
        app = DoctorCodeApp(runtime)
        app.run()
        return 0
    except Exception as exc:
        logger.exception("ui.startup_failed")
        print(Translator().t("startup.failed", error=exc), file=sys.stderr)
        return 1
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
