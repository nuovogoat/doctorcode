"""Mobile access window: managed LAN server, pairing code, and address."""

from __future__ import annotations

import tkinter as tk
from concurrent.futures import Future
from tkinter import ttk
from typing import Any

from doctorcode.server.controller import MobileServerController, ServerInfo


class MobileWindow(tk.Toplevel):
    def __init__(self, app: Any) -> None:
        super().__init__(app.root)
        self.app = app
        self.translator = app.translator
        self.controller: MobileServerController = app.mobile_controller
        self._texts: list[tuple[Any, str]] = []
        self._future: Future[Any] | None = None
        self._busy = False
        self.title(self.translator.t("mobile.title"))
        self.geometry("660x430")
        self.minsize(580, 380)
        self.transient(app.root)
        self._address_var = tk.StringVar(value="")
        self._build()
        self.apply_language()
        self._sync_from_controller()

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
        outer.pack(fill="both", expand=True, padx=16, pady=14)
        self.status_label = ttk.Label(outer, text="", style="Title.TLabel")
        self.status_label.pack(anchor="w")
        self._label(outer, "mobile.discovery", style="Muted.TLabel", wraplength=600).pack(
            anchor="w", pady=(6, 14)
        )

        code_frame = ttk.LabelFrame(outer, text="")
        self.code_frame = code_frame
        self._register(code_frame, "mobile.pairing_code")
        code_frame.pack(fill="x", pady=6)
        self.code_label = ttk.Label(code_frame, text="000000")
        self.code_label.configure(font=("Consolas", 30, "bold"))
        self.code_label.pack(anchor="w", padx=14, pady=(8, 0))
        self._label(code_frame, "mobile.pairing_hint", style="Muted.TLabel", wraplength=560).pack(
            anchor="w", padx=14, pady=(0, 10)
        )
        self.pairing_warning = self._label(
            code_frame, "mobile.no_pairing_warning", style="Danger.TLabel", wraplength=560
        )
        self.pairing_warning.pack(anchor="w", padx=14, pady=(0, 10))

        address_frame = ttk.LabelFrame(outer, text="")
        self._register(address_frame, "mobile.address")
        address_frame.pack(fill="x", pady=6)
        address_row = ttk.Frame(address_frame)
        address_row.pack(fill="x", padx=14, pady=10)
        self.address_entry = ttk.Entry(
            address_row, textvariable=self._address_var, state="readonly"
        )
        self.address_entry.pack(side="left", fill="x", expand=True)
        copy = ttk.Button(address_row, text="", command=self._copy_address)
        self._register(copy, "mobile.copy_address")
        copy.pack(side="left", padx=(8, 0))

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(16, 0))
        self.start_button = ttk.Button(
            controls, text="", style="Accent.TButton", command=self._start
        )
        self._register(self.start_button, "mobile.start")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            controls, text="", style="Danger.TButton", command=self._stop
        )
        self._register(self.stop_button, "mobile.stop")
        self.stop_button.pack(side="left", padx=8)
        self.status_detail = ttk.Label(controls, text="", style="Muted.TLabel")
        self.status_detail.pack(side="right")

    def apply_language(self) -> None:
        self.title(self.t("mobile.title"))
        for widget, key in self._texts:
            try:
                widget.configure(text=self.t(key))
            except tk.TclError:
                continue
        self._sync_from_controller()

    def _sync_from_controller(self) -> None:
        info = self.controller.info
        if info is None:
            self.status_label.configure(text=self.t("mobile.status_stopped"))
            self.code_label.configure(text="------")
            self._address_var.set("")
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.pairing_warning.pack_forget()
            return
        self._show_info(info)

    def _show_info(self, info: ServerInfo) -> None:
        address = info.urls[0] if info.urls else f"http://{info.host}:{info.port}"
        lan_urls = [url for url in info.urls if "127.0.0.1" not in url]
        if lan_urls:
            address = lan_urls[0]
        self.status_label.configure(text=self.t("mobile.status_running", address=address))
        self.code_label.configure(text=info.pairing_code)
        self._address_var.set(address)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        if info.pairing_required:
            self.pairing_warning.pack_forget()
        else:
            self.pairing_warning.pack(anchor="w", padx=14, pady=(0, 10))
        self.status_detail.configure(text=f":{info.port}")

    def _start(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.status_label.configure(text=self.t("mobile.status_starting"))
        self.start_button.configure(state="disabled")
        self.status_detail.configure(text="")
        self._future = self.app.runtime.executor.submit(self.controller.start)
        self.after(100, self._poll, "start")

    def _stop(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.stop_button.configure(state="disabled")
        self.status_detail.configure(text=self.t("mobile.status_stopped"))
        self._future = self.app.runtime.executor.submit(self.controller.stop)
        self.after(100, self._poll, "stop")

    def _poll(self, action: str) -> None:
        if not self.winfo_exists():
            return
        future = self._future
        if future is None or not future.done():
            self.after(100, self._poll, action)
            return
        self._busy = False
        try:
            future.result()
        except Exception as exc:
            self.status_label.configure(text=self.t("mobile.server_error", error=exc))
            self.status_detail.configure(text="")
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self._future = None
            return
        self._future = None
        if action == "start":
            info = self.controller.info
            if info is not None:
                self._show_info(info)
        else:
            self._sync_from_controller()

    def _copy_address(self) -> None:
        address = self._address_var.get().strip()
        if not address:
            return
        self.clipboard_clear()
        self.clipboard_append(address)
        self.status_detail.configure(text=self.t("dialog.copied"))
