"""Modules window: metadata status plus consent-gated pip install/update/remove."""

from __future__ import annotations

import tkinter as tk
from concurrent.futures import Future
from tkinter import messagebox, ttk
from typing import Any

from core_pack import CancellationToken, OperationCancelledError, run_process

from doctorcode.ui.modules_logic import (
    ModuleSpec,
    find_module,
    module_rows,
    pip_argv,
    pip_available,
)
from doctorcode.ui.theme import configure_text


class ModulesWindow(tk.Toplevel):
    def __init__(self, app: Any) -> None:
        super().__init__(app.root)
        self.app = app
        self.translator = app.translator
        self.colors = app.colors
        self._texts: list[tuple[Any, str]] = []
        self._token: CancellationToken | None = None
        self._future: Future[list[tuple[ModuleSpec, bool, str]]] | None = None
        self._last_action_name = "install"
        self.title(self.translator.t("modules.title"))
        self.geometry("840x620")
        self.minsize(700, 500)
        self.transient(app.root)
        self._build()
        self.apply_language()
        self.apply_colors()
        self._refresh()

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
        self._label(outer, "modules.intro", style="Muted.TLabel", wraplength=780).pack(
            anchor="w", pady=(0, 8)
        )
        tree_frame = ttk.Frame(outer)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("component", "package", "status", "version"),
            show="headings",
            selectmode="extended",
        )
        widths = {"component": 320, "package": 180, "status": 150, "version": 120}
        for column in ("component", "package", "status", "version"):
            self.tree.heading(column, text="")
            self.tree.column(column, width=widths[column], anchor="w", stretch=column == "component")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=8)
        for key, command in (
            ("modules.select_all", self._select_all),
            ("modules.clear_all", self._clear_all),
            ("modules.refresh", self._refresh),
        ):
            button = ttk.Button(buttons, text="", command=command)
            self._register(button, key)
            button.pack(side="left", padx=(0, 6))
        self.install_button = ttk.Button(
            buttons, text="", command=lambda: self._start("install")
        )
        self._register(self.install_button, "modules.install")
        self.install_button.pack(side="left", padx=(18, 6))
        self.update_button = ttk.Button(
            buttons, text="", command=lambda: self._start("update")
        )
        self._register(self.update_button, "modules.update")
        self.update_button.pack(side="left", padx=6)
        self.remove_button = ttk.Button(
            buttons,
            text="",
            style="Danger.TButton",
            command=lambda: self._start("remove"),
        )
        self._register(self.remove_button, "modules.remove")
        self.remove_button.pack(side="left", padx=6)

        result_frame = ttk.Frame(outer)
        result_frame.pack(fill="both", expand=True)
        self._label(result_frame, "modules.results").pack(anchor="w")
        self.result_text = tk.Text(result_frame, height=9, state="disabled")
        self.result_text.pack(fill="both", expand=True, pady=(4, 0))
        status_row = ttk.Frame(outer)
        status_row.pack(fill="x", pady=(8, 0))
        self.status_label = ttk.Label(status_row, text="", style="Muted.TLabel")
        self.status_label.pack(side="left")
        self.cancel_button = ttk.Button(
            status_row,
            text="",
            command=self._cancel,
            state="disabled",
        )
        self._register(self.cancel_button, "modules.cancel")
        self.cancel_button.pack(side="right")
        self._label(status_row, "modules.optional_pip", style="Muted.TLabel").pack(
            side="right", padx=12
        )

    def apply_language(self) -> None:
        self.title(self.t("modules.title"))
        for heading in ("component", "package", "status", "version"):
            key = {
                "component": "modules.name",
                "package": "modules.package_name",
                "status": "modules.status",
                "version": "modules.version",
            }[heading]
            self.tree.heading(heading, text=self.t(key))
        for widget, key in self._texts:
            try:
                widget.configure(text=self.t(key))
            except tk.TclError:
                continue
        self._refresh()

    def apply_colors(self) -> None:
        self.colors = self.app.colors
        configure_text(self.result_text, self.colors, mono=True)
        self.result_text.configure(state="disabled")

    def _refresh(self) -> None:
        if not self.winfo_exists():
            return
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for row in module_rows():
            self.tree.insert(
                "",
                "end",
                iid=row.spec.key,
                values=(
                    self.t(row.spec.label_key),
                    row.spec.distribution,
                    self.t(row.status_key),
                    row.version or self.t("common.not_available"),
                ),
            )
        for key in selected:
            if self.tree.exists(key):
                self.tree.selection_add(key)

    def _select_all(self) -> None:
        self.tree.selection_set(self.tree.get_children())

    def _clear_all(self) -> None:
        self.tree.selection_remove(self.tree.selection())

    def _selected_specs(self) -> list[ModuleSpec]:
        return [find_module(key) for key in self.tree.selection() if key]

    def _start(self, action: str) -> None:
        specs = self._selected_specs()
        if not specs:
            messagebox.showinfo(self.t("modules.title"), self.t("modules.no_selection"), parent=self)
            return
        if not pip_available():
            messagebox.showerror(self.t("dialog.error"), self.t("modules.missing_pip"), parent=self)
            return
        names = "\n".join(f"{spec.distribution} ({spec.extra})" for spec in specs)
        if action == "remove":
            if not messagebox.askyesno(
                self.t("dialog.confirm_remove_title"),
                self.t("dialog.confirm_remove_body", names=names),
                parent=self,
            ):
                return
        elif action == "install":
            if not messagebox.askyesno(
                self.t("modules.title"),
                self.t("modules.consent_install") + "\n\n" + names,
                parent=self,
            ):
                return
        elif action == "update" and not messagebox.askyesno(
            self.t("modules.title"),
            self.t("modules.consent_update") + "\n\n" + names,
            parent=self,
        ):
            return
        self._token = CancellationToken()
        self._last_action_name = action
        self._set_busy(True)
        self.status_label.configure(text=self.t("modules.working"))
        self._set_result("")
        self._future = self.app.runtime.executor.submit(
            self._worker, action, specs, self._token
        )
        self.after(100, self._poll)

    def _worker(
        self,
        action: str,
        specs: list[ModuleSpec],
        token: CancellationToken,
    ) -> list[tuple[ModuleSpec, bool, str]]:
        results: list[tuple[ModuleSpec, bool, str]] = []
        for spec in specs:
            if token.is_cancelled:
                break
            argv = pip_argv(action, spec.distribution, target=spec.target)  # type: ignore[arg-type]
            try:
                result = run_process(
                    argv,
                    timeout=900.0,
                    kill_timeout=5.0,
                    token=token,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    isolate_process_group=True,
                    logger=self.app.runtime.core.logger,
                )
            except OperationCancelledError:
                break
            except (OSError, ValueError) as exc:
                results.append((spec, False, str(exc)))
                continue
            if result.cancelled:
                break
            if result.returncode != 0:
                output = (str(result.stderr) or str(result.stdout)).strip()
                tail = "\n".join(output.splitlines()[-8:])
                results.append((spec, False, tail))
            else:
                results.append((spec, True, ""))
        return results

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        future = self._future
        if future is None or not future.done():
            self.after(100, self._poll)
            return
        try:
            results = future.result()
        except Exception as exc:
            results = [(spec, False, str(exc)) for spec in self._selected_specs()]
        self._set_busy(False)
        self._refresh()
        cancelled = self._token is not None and self._token.is_cancelled
        self.status_label.configure(
            text=self.t("modules.cancelled") if cancelled else self.t("modules.succeeded")
        )
        lines: list[str] = []
        for spec, ok, detail in results:
            label = self.t(spec.label_key)
            if ok:
                lines.append(self.t(f"modules.{self._last_action_name}_result", name=label))
            else:
                lines.append(self.t("modules.error_result", name=label, error=detail))
        if not lines and cancelled:
            lines.append(self.t("modules.cancelled"))
        self._set_result("\n".join(lines))
        self._token = None
        self._future = None

    def _set_result(self, text: str) -> None:
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", text)
        self.result_text.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in (self.install_button, self.update_button, self.remove_button):
            button.configure(state=state)
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self.tree.configure(selectmode="none" if busy else "extended")

    def _cancel(self) -> None:
        if self._token is not None:
            self._token.cancel()
            self.status_label.configure(text=self.t("modules.cancelled"))

    def destroy(self) -> None:
        if self._token is not None:
            self._token.cancel()
        super().destroy()
