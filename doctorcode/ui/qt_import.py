"""PySide6 preview and import flow for Kimi Code sessions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from doctorcode.engine.kimi_import import (
    KimiCandidate,
    KimiImportError,
    KimiImportResult,
)
from doctorcode.ui.qt_future import FutureWatcher

COLUMNS = 6


class KimiImportDialog(QDialog):
    """Pick a Kimi source, preview every session, then import selected ones."""

    def __init__(self, main: Any) -> None:
        super().__init__(main)
        self.main = main
        self.service = main.kimi_import
        self.candidates: list[KimiCandidate] = []
        self._busy = False
        self.resize(1000, 660)
        self._build()
        self.apply_language()

    # ------------------------------------------------------------------- ui
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        source_row = QHBoxLayout()
        self.source = QLineEdit("")
        self.source.setReadOnly(True)
        source_row.addWidget(self.source, 1)
        self.browse = QPushButton("")
        self.browse.clicked.connect(self.choose_source)
        source_row.addWidget(self.browse)
        layout.addLayout(source_row)

        self.table = QTableWidget(0, COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column in (3, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.currentCellChanged.connect(self._show_details)
        layout.addWidget(self.table, 1)

        self.details = QPlainTextEdit("")
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(145)
        layout.addWidget(self.details)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("muted")
        bottom.addWidget(self.status, 1)
        self.close_button = QPushButton("")
        self.close_button.clicked.connect(self.reject)
        bottom.addWidget(self.close_button)
        self.import_button = QPushButton("")
        self.import_button.clicked.connect(self.import_selected)
        bottom.addWidget(self.import_button)
        layout.addLayout(bottom)
        self._set_busy(False)

    def apply_language(self) -> None:
        self.setWindowTitle(self.t("import.title"))
        self.hint.setText(self.t("import.hint"))
        self.browse.setText(self.t("import.choose_source"))
        self.import_button.setText(self.t("import.import_selected"))
        self.close_button.setText(self.t("dialog.close"))
        headers = [
            "",
            self.t("import.column_title"),
            self.t("import.column_project"),
            self.t("import.column_messages"),
            self.t("import.column_warnings"),
            self.t("import.column_status"),
        ]
        self.table.setHorizontalHeaderLabels(headers)

    def t(self, key: str, **params: object) -> str:
        return self.main.translator.t(key, **params)

    # --------------------------------------------------------------- actions
    def choose_source(self) -> None:
        if self._busy:
            return
        initial = self.main.settings_store.settings.ui.last_project
        initial_dir = initial if initial and Path(initial).is_dir() else str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self, self.t("import.source_title"), initial_dir
        )
        if not folder:
            return
        self.source.setText(folder)
        if not self.service.can_handle(folder):
            QMessageBox.critical(
                self,
                self.t("dialog.error"),
                self.t("import.source_missing", path=folder),
            )
            return
        self._set_busy(True)
        self.status.setText(self.t("import.scanning"))
        future = self.main.runtime.executor.submit(self.service.scan, folder)
        FutureWatcher(self, future, self._scanned, self._scan_failed)

    def _scanned(self, candidates: list[KimiCandidate]) -> None:
        self.candidates = list(candidates)
        self._set_busy(False)
        self.table.setRowCount(0)
        if not candidates:
            self.status.setText(self.t("import.none_found"))
            return
        self.table.setRowCount(len(candidates))
        for row, candidate in enumerate(candidates):
            check = QTableWidgetItem("")
            check.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            check.setCheckState(
                Qt.CheckState.Checked if candidate.importable else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, check)
            values = [
                candidate.title,
                candidate.project_path or self.t("import.no_project"),
                str(candidate.message_count),
                str(candidate.warning_count),
                self._candidate_status(candidate),
            ]
            for column, value in enumerate(values, start=1):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.status.setText(
            self.t(
                "import.scanned",
                count=len(candidates),
                importable=sum(1 for candidate in candidates if candidate.importable),
            )
        )
        self._select_first_importable()
        self._show_details()

    def _select_first_importable(self) -> None:
        for row, candidate in enumerate(self.candidates):
            if candidate.importable:
                self.table.setCurrentCell(row, 1)
                return

    def _scan_failed(self, exc: Exception) -> None:
        self._set_busy(False)
        QMessageBox.critical(
            self,
            self.t("dialog.error"),
            self.t("import.scan_failed", error=exc),
        )

    def _candidate_status(self, candidate: KimiCandidate) -> str:
        if not candidate.message_count:
            return self.t("import.no_messages")
        if not candidate.project_path:
            return self.t("import.no_project")
        if candidate.project_dir is None:
            return self.t("import.missing_project")
        return self.t("import.ready")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.browse.setEnabled(not busy)
        self.import_button.setEnabled(not busy and bool(self.candidates))

    def _selected_candidates(self) -> list[KimiCandidate]:
        selected: list[KimiCandidate] = []
        for row, candidate in enumerate(self.candidates):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                selected.append(candidate)
        return selected

    def _show_details(self, *_args: object) -> None:
        selected = self._selected_candidates()
        current = self.table.currentRow()
        if 0 <= current < len(self.candidates):
            selected = [self.candidates[current]]
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
        self.details.setPlainText(text)

    def import_selected(self) -> None:
        if self._busy:
            return
        selected = self._selected_candidates()
        if not selected:
            self.status.setText(self.t("import.none_selected"))
            return
        work: list[tuple[KimiCandidate, Path | None]] = []
        for candidate in selected:
            if not candidate.message_count:
                continue
            override: Path | None = None
            if candidate.project_dir is None:
                override = self._ask_project(candidate)
                if override is None:
                    continue
            work.append((candidate, override))
        if not work:
            self.status.setText(self.t("import.none_selected"))
            return
        self._set_busy(True)
        self.status.setText(self.t("import.importing", count=len(work)))
        future = self.main.runtime.executor.submit(self._run_imports, work)
        FutureWatcher(self, future, self._imported, self._import_failed)

    def _ask_project(self, candidate: KimiCandidate) -> Path | None:
        initial = self.main.settings_store.settings.ui.last_project
        initial_dir = initial if initial and Path(initial).is_dir() else str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self,
            self.t("import.select_project_title", title=candidate.title),
            initial_dir,
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
        results, failures = outcome
        self._set_busy(False)
        for result in results:
            if result.project_path is not None:
                self.main.open_project(result.project_path, announce=False)
            row = next(
                (
                    index
                    for index, candidate in enumerate(self.candidates)
                    if candidate.session_id == result.session_id
                ),
                None,
            )
            if row is None:
                continue
            if result.already_imported:
                status = self.t("import.already_imported")
            elif result.imported_messages:
                status = self.t("import.imported", count=result.imported_messages)
            else:
                status = self.t("import.no_messages")
            item = self.table.item(row, 5)
            if item is not None:
                item.setText(status)
        imported = sum(result.imported_messages for result in results)
        already = sum(1 for result in results if result.already_imported)
        summary = self.t(
            "import.done",
            imported=imported,
            sessions=len(results),
            already=already,
            failed=len(failures),
        )
        self.status.setText(summary)
        self.main.status.showMessage(summary, 7000)
        if failures:
            body = "\n".join(
                f"{candidate.title}: {error}" for candidate, error in failures[:12]
            )
            QMessageBox.warning(
                self,
                self.t("import.failed_title"),
                self.t("import.failed_body", count=len(failures), details=body),
            )

    def _import_failed(self, exc: Exception) -> None:
        self._set_busy(False)
        QMessageBox.critical(
            self,
            self.t("import.import_error"),
            str(exc),
        )
