"""Tkinter preview and import flow for Kimi Code sessions."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from doctorcode.engine.kimi_import import (
    KimiCandidate,
    KimiImportError,
    KimiImportResult,
)


class KimiImportWindow(tk.Toplevel):
    """Pick a Kimi source, preview every session, then import selected ones."""

    def __init__(self, app: Any) -> None:
        super().__init__(app.root)
        self.app = app
        self.service = app.kimi_import
        self.candidates: list[KimiCandidate] = []
        self._rows: dict[str, str] = {}
        self._busy = False
        self._target: Path | None = None
        self._texts: list[tuple[Any, str]] = []
        self.geometry("980x620")
        self.transient(app.root)
        self._build()
        self.apply_language()

    # ------------------------------------------------------------------ text
    def t(self, key: str, **params: object) -> str:
        return self.app.t(key, **params)

    def _label(self, master: Any, key: str, **kwargs: Any) -> ttk.Label:
        label = ttk.Label(master, text="", **kwargs)
        self._texts.append((label, key))
        return label

    def _button(self, master: Any, key: str, command: Any, **kwargs: Any) -> ttk.Button:
        button = ttk.Button(master, text="", command=command, **kwargs)
        self._texts.append((button, key))
        return button

    # ------------------------------------------------------------------- ui
    def _build(self) -> None:
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=12, pady=10)

        top = ttk.Frame(outer)
        top.pack(fill="x")
        self.hint = self._label(top, "import.hint", style="Muted.TLabel")
        self.hint.pack(side="left", fill="x", expand=True)
        self.choose_button = self._button(
            top, "import.choose_source", self.choose_source, style="Accent.TButton"
        )
        self.choose_button.pack(side="right", padx=(8, 0))

        self.source_var = tk.StringVar(value="")
        source = ttk.Entry(outer, textvariable=self.source_var, state="readonly")
        source.pack(fill="x", pady=(8, 6))

        # Target project: by default each session imports into the project
        # stored in its snapshot, but migration works into ANY project — the
        # explicit target below overrides every selected candidate.
        target_row = ttk.Frame(outer)
        target_row.pack(fill="x", pady=(0, 6))
        self._label(target_row, "import.target_label", style="Muted.TLabel").pack(
            side="left"
        )
        self.target_var = tk.StringVar(value="")
        target_entry = ttk.Entry(target_row, textvariable=self.target_var, state="readonly")
        target_entry.pack(side="left", fill="x", expand=True, padx=6)
        self._button(target_row, "import.choose_target", self.choose_target).pack(
            side="left", padx=(0, 6)
        )
        self._button(target_row, "import.target_reset", self.reset_target).pack(side="left")

        columns = ("title", "project", "messages", "warnings", "status")
        table = ttk.Frame(outer)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            table, columns=columns, show="headings", selectmode="extended", height=12
        )
        widths = {"title": 280, "project": 360, "messages": 90, "warnings": 90, "status": 150}
        for column in columns:
            self.tree.heading(column, text="")
            self.tree.column(column, width=widths[column], anchor="w", stretch=True)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._show_details)

        self.details = tk.Text(outer, height=6, wrap="word", state="disabled")
        self.details.pack(fill="x", pady=(8, 6))

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x")
        self.status_label = ttk.Label(bottom, text="", style="Muted.TLabel")
        self.status_label.pack(side="left", fill="x", expand=True)
        self.close_button = self._button(bottom, "dialog.close", self.destroy)
        self.close_button.pack(side="right")
        self.import_button = self._button(
            bottom, "import.import_selected", self.import_selected, style="Accent.TButton"
        )
        self.import_button.pack(side="right", padx=8)
        self.import_button.state(["disabled"])

    def apply_language(self) -> None:
        self.title(self.t("import.title"))
        for widget, key in self._texts:
            try:
                widget.configure(text=self.t(key))
            except tk.TclError:
                continue
        self.tree.heading("title", text=self.t("import.column_title"))
        self.tree.heading("project", text=self.t("import.column_project"))
        self.tree.heading("messages", text=self.t("import.column_messages"))
        self.tree.heading("warnings", text=self.t("import.column_warnings"))
        self.tree.heading("status", text=self.t("import.column_status"))
        self._render_target()

    # -------------------------------------------------------- target project
    def _render_target(self) -> None:
        self.target_var.set(
            str(self._target)
            if self._target is not None
            else self.t("import.target_default")
        )

    def choose_target(self) -> None:
        if self._busy:
            return
        initial = self.app.settings_store.settings.ui.last_project
        initial_dir = (
            str(self._target)
            if self._target is not None
            else (initial if initial and Path(initial).is_dir() else str(Path.home()))
        )
        folder = filedialog.askdirectory(
            parent=self,
            title=self.t("import.choose_target"),
            initialdir=initial_dir,
            mustexist=True,
        )
        if not folder:
            return
        path = Path(folder)
        if path.is_dir():
            self._target = path
            self._render_target()

    def reset_target(self) -> None:
        self._target = None
        self._render_target()

    # --------------------------------------------------------------- actions
    def choose_source(self) -> None:
        if self._busy:
            return
        initial = self.app.settings_store.settings.ui.last_project
        initial_dir = initial if initial and Path(initial).is_dir() else str(Path.home())
        folder = filedialog.askdirectory(
            parent=self,
            title=self.t("import.source_title"),
            initialdir=initial_dir,
            mustexist=True,
        )
        if not folder:
            return
        self.source_var.set(folder)
        if not self.service.can_handle(folder):
            messagebox.showerror(
                self.t("dialog.error"),
                self.t("import.source_missing", path=folder),
                parent=self,
            )
            return
        self._set_busy(True, self.t("import.scanning"))
        future = self.app.runtime.executor.submit(self.service.scan, folder)
        self.app._watch_future(future, on_done=self._scanned, on_error=self._scan_failed)

    def _scanned(self, candidates: list[KimiCandidate]) -> None:
        if not self.winfo_exists():
            return
        self.candidates = list(candidates)
        self._set_busy(False, "")
        self._rows.clear()
        self.tree.delete(*self.tree.get_children())
        if not candidates:
            self.status_label.configure(text=self.t("import.none_found"))
            return
        selected: list[str] = []
        for index, candidate in enumerate(candidates):
            row = str(index)
            self._rows[candidate.session_id] = row
            status = self._candidate_status(candidate)
            self.tree.insert(
                "",
                "end",
                iid=row,
                values=(
                    candidate.title,
                    candidate.project_path or self.t("import.no_project"),
                    candidate.message_count,
                    candidate.warning_count,
                    status,
                ),
            )
            if candidate.importable:
                selected.append(row)
        if selected:
            self.tree.selection_set(selected)
            self.tree.focus(selected[0])
            self.tree.see(selected[0])
        self.import_button.state(["!disabled"])
        self.status_label.configure(
            text=self.t(
                "import.scanned",
                count=len(candidates),
                importable=sum(1 for candidate in candidates if candidate.importable),
            )
        )
        self._show_details()

    def _scan_failed(self, exc: Exception) -> None:
        if not self.winfo_exists():
            return
        self._set_busy(False, "")
        messagebox.showerror(
            self.t("dialog.error"),
            self.t("import.scan_failed", error=exc),
            parent=self,
        )

    def _candidate_status(self, candidate: KimiCandidate) -> str:
        if not candidate.message_count:
            return self.t("import.no_messages")
        if not candidate.project_path:
            return self.t("import.no_project")
        if candidate.project_dir is None:
            return self.t("import.missing_project")
        return self.t("import.ready")

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        if status:
            self.status_label.configure(text=status)
        if busy:
            self.choose_button.state(["disabled"])
            self.import_button.state(["disabled"])
        else:
            self.choose_button.state(["!disabled"])
            if self.candidates:
                self.import_button.state(["!disabled"])

    def _selected_candidates(self) -> list[KimiCandidate]:
        selected: list[KimiCandidate] = []
        for row in self.tree.selection():
            try:
                selected.append(self.candidates[int(row)])
            except (ValueError, IndexError):
                continue
        return selected

    def _show_details(self, _event: tk.Event[Any] | None = None) -> None:
        selected = self._selected_candidates()
        if not selected:
            text = self.t("import.empty_details")
        elif len(selected) > 1:
            text = self.t("import.multi_details", count=len(selected))
        else:
            candidate = selected[0]
            codes = ", ".join(candidate.warning_codes) or self.t("import.no_warnings")
            text = self.t(
                "import.details",
                title=candidate.title,
                project=candidate.project_path or self.t("import.no_project"),
                messages=candidate.message_count,
                subagents=candidate.subagent_count,
                warnings=candidate.warning_count,
                codes=codes,
                updated=candidate.updated_at or "-",
            )
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def import_selected(self) -> None:
        if self._busy:
            return
        selected = self._selected_candidates()
        if not selected:
            self.status_label.configure(text=self.t("import.none_selected"))
            return
        work: list[tuple[KimiCandidate, Path | None]] = []
        for candidate in selected:
            if not candidate.message_count:
                continue
            # An explicit target wins for every candidate; without one, only
            # sessions whose stored project folder is gone ask for a folder.
            override: Path | None = self._target
            if override is None and candidate.project_dir is None:
                override = self._ask_project(candidate)
                if override is None:
                    continue
            work.append((candidate, override))
        if not work:
            self.status_label.configure(text=self.t("import.none_selected"))
            return
        self._set_busy(True, self.t("import.importing", count=len(work)))
        future = self.app.runtime.executor.submit(self._run_imports, work)
        self.app._watch_future(future, on_done=self._imported, on_error=self._import_failed)

    def _ask_project(self, candidate: KimiCandidate) -> Path | None:
        initial = self.app.settings_store.settings.ui.last_project
        initial_dir = initial if initial and Path(initial).is_dir() else str(Path.home())
        folder = filedialog.askdirectory(
            parent=self,
            title=self.t("import.select_project_title", title=candidate.title),
            initialdir=initial_dir,
            mustexist=True,
        )
        if not folder:
            return None
        path = Path(folder)
        return path if path.is_dir() else None

    def _run_imports(
        self,
        work: list[tuple[KimiCandidate, Path | None]],
    ) -> tuple[list[KimiImportResult], list[tuple[KimiCandidate, str]]]:
        results: list[KimiImportResult] = []
        failures: list[tuple[KimiCandidate, str]] = []
        for candidate, override in work:
            try:
                result = self.service.import_snapshot(
                    candidate.snapshot, project_path=override
                )
            except (KimiImportError, OSError, ValueError, RuntimeError) as exc:
                failures.append((candidate, str(exc)))
                continue
            results.append(result)
        return results, failures

    def _imported(
        self,
        outcome: tuple[list[KimiImportResult], list[tuple[KimiCandidate, str]]],
    ) -> None:
        if not self.winfo_exists():
            return
        results, failures = outcome
        self._set_busy(False, "")
        for result in results:
            if result.project_path is not None:
                self.app.open_project(result.project_path, announce=False)
            row = self._rows.get(result.session_id)
            if row is None:
                continue
            if result.already_imported:
                status = self.t("import.already_imported")
            elif result.imported_messages:
                status = self.t("import.imported", count=result.imported_messages)
            else:
                status = self.t("import.no_messages")
            values = list(self.tree.item(row, "values"))
            if len(values) >= 5:
                values[4] = status
                self.tree.item(row, values=values)
        imported = sum(result.imported_messages for result in results)
        already = sum(1 for result in results if result.already_imported)
        self.status_label.configure(
            text=self.t(
                "import.done",
                imported=imported,
                sessions=len(results),
                already=already,
                failed=len(failures),
            )
        )
        if failures:
            body = "\n".join(
                f"{candidate.title}: {error}" for candidate, error in failures[:12]
            )
            messagebox.showwarning(
                self.t("import.failed_title"),
                self.t("import.failed_body", count=len(failures), details=body),
                parent=self,
            )

    def _import_failed(self, exc: Exception) -> None:
        if not self.winfo_exists():
            return
        self._set_busy(False, "")
        messagebox.showerror(
            self.t("import.import_error"),
            str(exc),
            parent=self,
        )
