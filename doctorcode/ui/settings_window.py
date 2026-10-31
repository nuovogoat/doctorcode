"""Single Settings window: connections, agent, approvals, interface, mobile."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from pydantic import ValidationError

from doctorcode.engine.llm_client import LLMError, load_models
from doctorcode.i18n import LANGUAGES
from doctorcode.settings import (
    AgentSettings,
    AppSettings,
    ConfirmationSettings,
    ConnectionProfile,
    ServerSettings,
    UISettings,
)
from doctorcode.ui.theme import configure_text
from doctorcode.ui.widgets import ScrollableFrame

THEME_KEYS = {"dark": "settings.theme.dark", "light": "settings.theme.light"}
LANGUAGE_KEYS = {"": "language.auto", "ru": "language.ru", "en": "language.en", "de": "language.de"}
THEME_CODES = ("dark", "light")
LANGUAGE_CODES = ("", *LANGUAGES)


class SettingsWindow(tk.Toplevel):
    def __init__(self, app: Any) -> None:
        super().__init__(app.root)
        self.app = app
        self.settings_store = app.settings_store
        self.translator = app.translator
        self.colors = app.colors
        self._texts: list[tuple[Any, str]] = []
        self._settings = self.settings_store.settings
        self.title(self.translator.t("settings.title"))
        self.geometry("900x720")
        self.minsize(760, 580)
        self.transient(app.root)
        self._create_vars()
        self._build()
        self.apply_language()
        self.apply_colors()
        self._load()

    @property
    def t(self):
        return self.translator.t

    def _theme_label(self, code: str) -> str:
        return self.t(THEME_KEYS.get(code, "settings.theme.dark"))

    def _language_label(self, code: str) -> str:
        return self.t(LANGUAGE_KEYS.get(code, "language.auto"))

    def _on_theme_selected(self, _event: tk.Event[tk.Misc]) -> None:
        index = self.theme_combo.current()
        if 0 <= index < len(THEME_CODES):
            self._theme_code = THEME_CODES[index]

    def _on_language_selected(self, _event: tk.Event[tk.Misc]) -> None:
        index = self.language_combo.current()
        if 0 <= index < len(LANGUAGE_CODES):
            self._language_code = LANGUAGE_CODES[index]

    def _create_vars(self) -> None:
        s = self._settings
        self._theme_code = s.ui.theme
        self._language_code = s.ui.language
        self.vars: dict[str, tk.Variable] = {}
        for role, profile in (("writer", s.profiles["writer"]), ("reviewer", s.profiles["reviewer"])):
            self.vars[f"{role}_url"] = tk.StringVar(value=profile.base_url)
            self.vars[f"{role}_key"] = tk.StringVar(value=profile.api_key)
            self.vars[f"{role}_model"] = tk.StringVar(value=profile.model)
            self.vars[f"{role}_temp"] = tk.StringVar(value=str(profile.temperature))
        self.vars["max_iterations"] = tk.StringVar(value=str(s.agent.max_iterations))
        self.vars["command_timeout"] = tk.StringVar(value=str(s.agent.command_timeout_seconds))
        self.vars["max_file"] = tk.StringVar(value=str(s.agent.max_file_bytes))
        self.vars["request_timeout"] = tk.StringVar(value=str(s.agent.request_timeout_seconds))
        self.vars["confirm_commands"] = tk.BooleanVar(value=s.confirmations.commands)
        self.vars["confirm_tests"] = tk.BooleanVar(value=s.confirmations.tests)
        self.vars["confirm_writes"] = tk.BooleanVar(value=s.confirmations.writes)
        self.vars["confirm_package_actions"] = tk.BooleanVar(
            value=s.confirmations.package_actions
        )
        self.vars["hide_console"] = tk.BooleanVar(value=s.ui.hide_console)
        self.vars["theme"] = tk.StringVar(value=self._theme_label(s.ui.theme))
        self.vars["language"] = tk.StringVar(
            value=self._language_label(s.ui.language)
        )
        self.vars["port"] = tk.StringVar(value=str(s.server.port))
        self.vars["require_pairing"] = tk.BooleanVar(value=s.server.require_pairing)
        self.vars["advertise_mdns"] = tk.BooleanVar(value=s.server.advertise_mdns)
        self.vars["server_auto"] = tk.BooleanVar(value=s.server.auto_start)

    def _register(self, widget: Any, key: str) -> Any:
        self._texts.append((widget, key))
        return widget

    def _label(self, master: Any, key: str, **kwargs: Any) -> ttk.Label:
        label = ttk.Label(master, text="", **kwargs)
        self._register(label, key)
        return label

    def _build(self) -> None:
        outer = ttk.Frame(self, style="TFrame")
        outer.pack(fill="both", expand=True, padx=12, pady=10)
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        self._build_connections()
        self._build_agent()
        self._build_confirmations()
        self._build_interface()
        self._build_server()
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(10, 0))
        self.status_label = ttk.Label(bottom, text="", style="Muted.TLabel")
        self.status_label.pack(side="left")
        self.save_button = ttk.Button(bottom, text="", style="Accent.TButton", command=self._save)
        self._register(self.save_button, "settings.save")
        self.save_button.pack(side="right")
        close = ttk.Button(bottom, text="", command=self.destroy)
        self._register(close, "dialog.close")
        close.pack(side="right", padx=8)

    def _build_connections(self) -> None:
        self.connections_tab = ScrollableFrame(self.notebook)
        self.notebook.add(self.connections_tab, text="")
        body = self.connections_tab.body
        body.columnconfigure(0, weight=1)
        self._profile_frame(body, "writer", 0)
        self._profile_frame(body, "reviewer", 1)

    def _profile_frame(self, master: Any, role: str, row: int) -> None:
        frame = ttk.LabelFrame(master, text="")
        self._register(frame, "settings.writer" if role == "writer" else "settings.reviewer")
        frame.grid(row=row, column=0, sticky="ew", padx=8, pady=8)
        frame.columnconfigure(1, weight=1)
        hint = "settings.writer_hint" if role == "writer" else "settings.reviewer_hint"
        hint_label = self._label(frame, hint, style="Muted.TLabel")
        hint_label.grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 8))

        self._label(frame, "settings.base_url").grid(
            row=1, column=0, sticky="w", padx=8, pady=4
        )
        url_entry = ttk.Entry(frame, textvariable=self.vars[f"{role}_url"])
        url_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8, pady=4)
        self._label(frame, "settings.api_key").grid(
            row=2, column=0, sticky="w", padx=8, pady=4
        )
        key_entry = ttk.Entry(frame, textvariable=self.vars[f"{role}_key"], show="•")
        key_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=8, pady=4)
        api_hint = self._label(frame, "settings.api_key_hint", style="Muted.TLabel")
        api_hint.grid(row=3, column=1, columnspan=2, sticky="w", padx=8)

        self._label(frame, "settings.model").grid(
            row=4, column=0, sticky="w", padx=8, pady=4
        )
        model_box = ttk.Combobox(
            frame,
            textvariable=self.vars[f"{role}_model"],
            state="normal",
        )
        model_box.grid(row=4, column=1, sticky="ew", padx=8, pady=4)
        self.vars[f"{role}_models"] = model_box
        fetch = ttk.Button(
            frame,
            text="",
            command=lambda role=role: self._fetch_models(role),
        )
        self._register(fetch, "settings.fetch_models")
        fetch.grid(row=4, column=2, sticky="e", padx=8, pady=4)

        self._label(frame, "settings.temperature").grid(
            row=5, column=0, sticky="w", padx=8, pady=4
        )
        ttk.Spinbox(
            frame,
            from_=0.0,
            to=2.0,
            increment=0.1,
            textvariable=self.vars[f"{role}_temp"],
            width=8,
        ).grid(row=5, column=1, sticky="w", padx=8, pady=4)
        status = ttk.Label(frame, text="", style="Muted.TLabel")
        status.grid(row=6, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 8))
        self.vars[f"{role}_fetch_status"] = status

    def _build_agent(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="")
        tab.columnconfigure(1, weight=1)
        fields = [
            ("max_iterations", "settings.agent.max_iterations"),
            ("command_timeout", "settings.agent.command_timeout"),
            ("max_file", "settings.agent.max_file"),
            ("request_timeout", "settings.agent.request_timeout"),
        ]
        for row, (name, key) in enumerate(fields):
            self._label(tab, key).grid(row=row, column=0, sticky="w", padx=12, pady=8)
            ttk.Entry(tab, textvariable=self.vars[name], width=18).grid(
                row=row, column=1, sticky="w", padx=12, pady=8
            )
        self._label(tab, "settings.agent.extra").grid(
            row=len(fields), column=0, sticky="nw", padx=12, pady=8
        )
        self.extra_text = tk.Text(tab, height=8, wrap="word")
        self.extra_text.grid(
            row=len(fields),
            column=1,
            columnspan=2,
            sticky="nsew",
            padx=12,
            pady=8,
        )
        tab.rowconfigure(len(fields), weight=1)
        self.extra_text.insert("1.0", self._settings.agent.system_prompt_extra)

    def _build_confirmations(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="")
        rows = [
            ("confirm_commands", "confirm.commands"),
            ("confirm_tests", "confirm.tests"),
            ("confirm_writes", "confirm.writes"),
            ("confirm_package_actions", "confirm.package_actions"),
        ]
        for row, (name, key) in enumerate(rows):
            check = ttk.Checkbutton(tab, text="", variable=self.vars[name])
            check.grid(row=row, column=0, sticky="w", padx=12, pady=8)
            self._register(check, key)
        note = self._label(tab, "confirm.note", style="Muted.TLabel", wraplength=700)
        note.grid(row=len(rows), column=0, sticky="w", padx=12, pady=(18, 8))

    def _build_interface(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="")
        tab.columnconfigure(1, weight=1)
        hide = ttk.Checkbutton(tab, text="", variable=self.vars["hide_console"])
        hide.grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(14, 2))
        self._register(hide, "settings.hide_console")
        self._label(tab, "settings.hide_console_hint", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", padx=32, pady=(0, 14)
        )
        self._label(tab, "settings.theme").grid(row=2, column=0, sticky="w", padx=12, pady=8)
        self.theme_combo = ttk.Combobox(
            tab,
            textvariable=self.vars["theme"],
            state="readonly",
            values=[],
        )
        self.theme_combo.grid(row=2, column=1, sticky="w", padx=12, pady=8)
        self.theme_combo.bind("<<ComboboxSelected>>", self._on_theme_selected)
        self._label(tab, "settings.language").grid(row=3, column=0, sticky="w", padx=12, pady=8)
        self.language_combo = ttk.Combobox(
            tab,
            textvariable=self.vars["language"],
            state="readonly",
            values=[],
        )
        self.language_combo.grid(row=3, column=1, sticky="w", padx=12, pady=8)
        self.language_combo.bind("<<ComboboxSelected>>", self._on_language_selected)

    def _build_server(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="")
        entries = [
            ("port", "settings.server.port"),
            ("require_pairing", "settings.server.pairing"),
            ("advertise_mdns", "settings.server.mdns"),
            ("server_auto", "settings.server.auto"),
        ]
        for row, (name, key) in enumerate(entries):
            if name == "port":
                self._label(tab, key).grid(row=row, column=0, sticky="w", padx=12, pady=8)
                ttk.Entry(tab, textvariable=self.vars[name], width=10).grid(
                    row=row, column=1, sticky="w", padx=12, pady=8
                )
            else:
                check = ttk.Checkbutton(tab, text="", variable=self.vars[name])
                check.grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=8)
                self._register(check, key)
        self._label(tab, "settings.server.hint", style="Muted.TLabel", wraplength=700).grid(
            row=len(entries), column=0, columnspan=2, sticky="w", padx=12, pady=(18, 8)
        )

    def _load(self) -> None:
        for role in ("writer", "reviewer"):
            self.vars[f"{role}_models"].configure(values=[self.vars[f"{role}_model"].get()])

    def apply_language(self) -> None:
        self.title(self.t("settings.title"))
        tabs = [
            "settings.tab.connections",
            "settings.tab.agent",
            "settings.tab.confirmations",
            "settings.tab.interface",
            "settings.tab.server",
        ]
        for index, key in enumerate(tabs):
            self.notebook.tab(index, text=self.t(key))
        for widget, key in self._texts:
            try:
                widget.configure(text=self.t(key))
            except tk.TclError:
                continue
        self._update_theme_values()
        self._update_language_values()

    def _update_theme_values(self) -> None:
        self.theme_combo.configure(
            values=[self._theme_label(code) for code in THEME_CODES]
        )
        self.vars["theme"].set(self._theme_label(self._theme_code))

    def _update_language_values(self) -> None:
        self.language_combo.configure(
            values=[self._language_label(code) for code in LANGUAGE_CODES]
        )
        self.vars["language"].set(self._language_label(self._language_code))

    def apply_colors(self) -> None:
        self.colors = self.app.colors
        configure_text(self.extra_text, self.colors)
        self.configure(background=self.colors["bg"])

    def _fetch_models(self, role: str) -> None:
        status = self.vars[f"{role}_fetch_status"]
        status.configure(text=self.t("settings.fetching"))
        try:
            profile = self._profile(role)
        except (ValueError, ValidationError) as exc:
            status.configure(text=self.t("settings.models_failed", error=exc))
            return
        if not profile.base_url:
            status.configure(text=self.t("settings.not_configured"))
            return

        async def fetch() -> list[str]:
            return await load_models(profile, timeout=30.0)

        future = self.app.runner.submit(fetch())

        def poll() -> None:
            if not self.winfo_exists():
                return
            if not future.done():
                self.after(75, poll)
                return
            try:
                models = future.result()
            except LLMError as exc:
                if exc.code == "MODELS_MODULE_MISSING":
                    status.configure(text=self.t("models.module_missing"))
                else:
                    status.configure(text=self.t("settings.models_failed", error=exc))
                return
            except Exception as exc:
                status.configure(text=self.t("settings.models_failed", error=exc))
                return
            if not models:
                status.configure(text=self.t("settings.no_models"))
                return
            box = self.vars[f"{role}_models"]
            current = self.vars[f"{role}_model"].get().strip()
            values = list(dict.fromkeys([*models, *([current] if current else [])]))
            box.configure(values=values)
            if not current:
                self.vars[f"{role}_model"].set(models[0])
            status.configure(text=self.t("settings.models_fetched", count=len(models)))

        self.after(75, poll)

    def _profile(self, role: str) -> ConnectionProfile:
        temp = float(self.vars[f"{role}_temp"].get())
        return ConnectionProfile(
            base_url=self.vars[f"{role}_url"].get(),
            api_key=self.vars[f"{role}_key"].get(),
            model=self.vars[f"{role}_model"].get(),
            temperature=temp,
        )

    def _save(self) -> None:
        try:
            writer = self._profile("writer")
            reviewer = self._profile("reviewer")
            agent = AgentSettings(
                max_iterations=int(self.vars["max_iterations"].get()),
                command_timeout_seconds=int(self.vars["command_timeout"].get()),
                max_file_bytes=int(self.vars["max_file"].get()),
                request_timeout_seconds=float(self.vars["request_timeout"].get()),
                system_prompt_extra=self.extra_text.get("1.0", "end").strip(),
            )
            confirmations = ConfirmationSettings(
                commands=bool(self.vars["confirm_commands"].get()),
                tests=bool(self.vars["confirm_tests"].get()),
                writes=bool(self.vars["confirm_writes"].get()),
                package_actions=bool(self.vars["confirm_package_actions"].get()),
            )
            theme = self._theme_code
            language = self._language_code
            ui = UISettings(
                language=language,
                hide_console=bool(self.vars["hide_console"].get()),
                theme=theme,
                last_project=self._settings.ui.last_project,
            )
            server = ServerSettings(
                port=int(self.vars["port"].get()),
                require_pairing=bool(self.vars["require_pairing"].get()),
                advertise_mdns=bool(self.vars["advertise_mdns"].get()),
                auto_start=bool(self.vars["server_auto"].get()),
            )
        except (ValueError, ValidationError) as exc:
            self.status_label.configure(text=self.t("settings.invalid"))
            messagebox.showerror(self.t("dialog.error"), str(exc), parent=self)
            return

        def mutate(settings: AppSettings) -> None:
            settings.profiles = {"writer": writer, "reviewer": reviewer}
            settings.agent = agent
            settings.confirmations = confirmations
            settings.ui = ui
            settings.server = server

        try:
            self.settings_store.update(mutate)
        except Exception as exc:
            self.status_label.configure(text=self.t("settings.save_failed", error=exc))
            messagebox.showerror(self.t("dialog.error"), str(exc), parent=self)
            return
        self._settings = self.settings_store.settings
        self.status_label.configure(text=self.t("settings.saved"))
        self.app.preferences_changed()


class AboutWindow(tk.Toplevel):
    def __init__(self, app: Any) -> None:
        super().__init__(app.root)
        self.app = app
        self.title(app.t("gui.about"))
        self.geometry("480x260")
        self.transient(app.root)
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=24, pady=20)
        ttk.Label(frame, text=app.t("app.title"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text=app.t("app.subtitle"), style="Subtitle.TLabel").pack(
            anchor="w", pady=(2, 14)
        )
        ttk.Label(frame, text=app.t("app.version", version=app.version)).pack(anchor="w")
        ttk.Label(frame, text=app.data_dir, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Button(frame, text=app.t("dialog.close"), command=self.destroy).pack(
            side="right", pady=(18, 0)
        )
