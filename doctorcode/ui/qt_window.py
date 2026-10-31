"""Optional PySide6 shell.

This module is imported only after :mod:`doctorcode.ui.qt_app` confirms that
PySide6 is installed.  It offers the same engine, project tabs, structured
feed, handoff, reviewer, mobile controls, and settings as the Tkinter shell;
Tkinter remains the default because it has no optional dependency.
"""

from __future__ import annotations

import contextlib
import queue
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from core_pack import CancellationToken, OperationCancelledError, run_process
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from doctorcode import __version__
from doctorcode.engine.events import ActionEvent, ActionStatus, ActionType
from doctorcode.engine.feed import ActionFeed
from doctorcode.engine.kimi_import import ConnectModuleMissing, KimiImportService
from doctorcode.engine.llm_client import LLMError, load_models
from doctorcode.engine.reviewer import ReviewerService
from doctorcode.engine.session import AgentSession
from doctorcode.engine.workspace import WorkspaceManager
from doctorcode.i18n import LANGUAGES, Translator, resolve_language
from doctorcode.runtime import AppRuntime, create_runtime
from doctorcode.server.controller import MobileServerController, ServerInfo
from doctorcode.settings import (
    AgentSettings,
    AppSettings,
    ConfirmationSettings,
    ConnectionProfile,
    ServerSettings,
    UISettings,
)
from doctorcode.ui.approval import ApprovalBroker
from doctorcode.ui.feed_model import (
    event_action_text,
    event_human_row,
    event_status_text,
    event_summary,
    key_value_rows,
    raw_text,
    status_style,
    warning_text,
)
from doctorcode.ui.modules_logic import (
    MODULES,
    ModuleSpec,
    find_module,
    module_rows,
    pip_argv,
    pip_available,
)
from doctorcode.ui.palette import palette
from doctorcode.ui.qt_future import FutureWatcher
from doctorcode.ui.qt_import import KimiImportDialog
from doctorcode.ui.runner import AsyncRunner

DARK = palette("dark")
LIGHT = palette("light")
STATUS_COLORS = {
    "success": DARK["success"],
    "failure": DARK["failure"],
    "progress": DARK["progress"],
    "conflict": DARK["failure"],
}


def _stylesheet(theme: str) -> str:
    colors = LIGHT if theme == "light" else DARK
    return f"""
    QWidget {{ background: {colors['bg']}; color: {colors['text']}; }}
    QToolBar {{ background: {colors['panel']}; border: 0; spacing: 6px; padding: 4px; }}
    QTabWidget::pane {{ border: 1px solid {colors['border']}; }}
    QTabBar::tab {{ background: {colors['panel_alt']}; padding: 6px 12px; }}
    QTabBar::tab:selected {{ background: {colors['panel']}; color: {colors['accent']}; }}
    QPlainTextEdit, QLineEdit, QComboBox, QTableWidget {{
        background: {colors['panel']}; color: {colors['text']};
        border: 1px solid {colors['border']};
    }}
    QPushButton {{ background: {colors['panel_alt']}; border: 1px solid {colors['border']};
        padding: 5px 10px; border-radius: 3px; }}
    QPushButton:hover {{ border-color: {colors['accent']}; }}
    QPushButton:disabled {{ color: {colors['muted']}; }}
    QHeaderView::section {{ background: {colors['panel_alt']}; color: {colors['text']};
        border: 0; padding: 5px; }}
    QLabel#muted {{ color: {colors['muted']}; }}
    """


def ask_yes_no(
    parent: QWidget,
    translator: Translator,
    title: str,
    text: str,
    *,
    yes_key: str = "dialog.yes",
    no_key: str = "dialog.no",
) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    yes = box.addButton(translator.t(yes_key), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(translator.t(no_key), QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is yes


class QtProjectTab(QWidget):
    def __init__(
        self,
        parent: QWidget,
        *,
        session: AgentSession,
        translator: Translator,
        on_send: Callable[[QtProjectTab, str], None],
        on_close: Callable[[QtProjectTab], None],
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.translator = translator
        self.on_send = on_send
        self.on_close = on_close
        self._events: dict[str, ActionEvent] = {}
        self._row_by_id: dict[str, int] = {}
        self._build()
        self.set_events(session.feed.events())
        self.apply_language()

    @property
    def t(self) -> Callable[..., str]:
        return self.translator.t

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        header = QHBoxLayout()
        self.title_label = QLabel("", self)
        font = self.title_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        self.title_label.setFont(font)
        header.addWidget(self.title_label)
        self.path_label = QLabel("", self)
        self.path_label.setObjectName("muted")
        header.addWidget(self.path_label)
        header.addStretch(1)
        self.count_label = QLabel("", self)
        self.count_label.setObjectName("muted")
        header.addWidget(self.count_label)
        self.state_label = QLabel("", self)
        header.addWidget(self.state_label)
        self.close_button = QPushButton("✕", self)
        self.close_button.setFixedWidth(34)
        self.close_button.clicked.connect(lambda: self.on_close(self))
        header.addWidget(self.close_button)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.notebook = QTabWidget(splitter)
        human_page = QWidget(self.notebook)
        human_layout = QVBoxLayout(human_page)
        self.warning_label = QLabel("", human_page)
        self.warning_label.setStyleSheet("color: #e05a5a;")
        human_layout.addWidget(self.warning_label)
        human_split = QSplitter(Qt.Orientation.Vertical, human_page)
        self.table = QTableWidget(0, 4, human_split)
        self.table.setHorizontalHeaderLabels(["", "", "", ""])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._render_selected)
        human_split.addWidget(self.table)
        self.details = QPlainTextEdit(human_split)
        self.details.setReadOnly(True)
        human_split.addWidget(self.details)
        human_split.setStretchFactor(0, 4)
        human_split.setStretchFactor(1, 2)
        human_layout.addWidget(human_split)
        self.notebook.addTab(human_page, "")

        kv_page = QWidget(self.notebook)
        kv_layout = QVBoxLayout(kv_page)
        self.kv_table = QTableWidget(0, 2, kv_page)
        self.kv_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.kv_table.verticalHeader().setVisible(False)
        self.kv_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.kv_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        kv_layout.addWidget(self.kv_table)
        self.notebook.addTab(kv_page, "")

        raw_page = QWidget(self.notebook)
        raw_layout = QVBoxLayout(raw_page)
        self.raw = QPlainTextEdit(raw_page)
        self.raw.setReadOnly(True)
        self.raw.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        raw_layout.addWidget(self.raw)
        self.notebook.addTab(raw_page, "")
        splitter.addWidget(self.notebook)

        prompt_page = QWidget(splitter)
        prompt_layout = QVBoxLayout(prompt_page)
        self.prompt_label = QLabel("", prompt_page)
        prompt_layout.addWidget(self.prompt_label)
        prompt_row = QHBoxLayout()
        self.prompt_text = QPlainTextEdit(prompt_page)
        self.prompt_text.setPlaceholderText("")
        self.prompt_text.setMaximumHeight(110)
        prompt_row.addWidget(self.prompt_text, 1)
        self.send_button = QPushButton("", prompt_page)
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self.submit)
        prompt_row.addWidget(self.send_button)
        prompt_layout.addLayout(prompt_row)
        splitter.addWidget(prompt_page)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        self._shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.prompt_text)
        self._shortcut.activated.connect(self.submit)

    def apply_language(self) -> None:
        t = self.t
        self.title_label.setText(self.session.project_name)
        self.path_label.setText(self.session.project_path.as_posix())
        self.prompt_label.setText(t("prompt.label"))
        self.prompt_text.setPlaceholderText(t("prompt.placeholder"))
        self.send_button.setText(t("prompt.send"))
        self.notebook.setTabText(0, t("feed.human"))
        self.notebook.setTabText(1, t("feed.key_value"))
        self.notebook.setTabText(2, t("feed.raw"))
        self.table.setHorizontalHeaderLabels(
            [t("feed.time"), t("feed.status"), t("feed.action"), t("feed.summary")]
        )
        self.kv_table.setHorizontalHeaderLabels([t("feed.key_value"), t("feed.details")])
        selected = self._selected_id()
        self.set_events(list(self._events.values()))
        if selected and selected in self._row_by_id:
            self.table.selectRow(self._row_by_id[selected])
        self.refresh_state()
        self._render_selected()

    def set_events(self, events: Sequence[ActionEvent]) -> None:
        self.table.setRowCount(0)
        self._events.clear()
        self._row_by_id.clear()
        for event in events:
            self._insert(event)
        self.refresh_state()

    def upsert(self, event: ActionEvent) -> None:
        if event.event_id in self._row_by_id:
            row = self._row_by_id[event.event_id]
            self._events[event.event_id] = event
            for column, value in enumerate(event_human_row(event, self.translator)):
                self.table.setItem(row, column, self._item(value))
            self._paint_row(row, event)
            if self._selected_id() == event.event_id:
                self._render_selected()
        else:
            self._insert(event)
        self.refresh_state()

    def _item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _insert(self, event: ActionEvent) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._events[event.event_id] = event
        self._row_by_id[event.event_id] = row
        for column, value in enumerate(event_human_row(event, self.translator)):
            item = self._item(value)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, event.event_id)
            self.table.setItem(row, column, item)
        self._paint_row(row, event)
        self.table.scrollToBottom()

    def _paint_row(self, row: int, event: ActionEvent) -> None:
        key = status_style(event)
        color = STATUS_COLORS.get(key, DARK["text"])
        # QColor is imported lazily to keep module import cheap.
        from PySide6.QtGui import QColor

        for column in range(4):
            item = self.table.item(row, column)
            if item is not None:
                item.setForeground(QColor(color))

    def refresh_state(self) -> None:
        busy = self.session.busy
        self.count_label.setText(
            self.t("gui.status_events", count=len(self.session.feed))
        )
        self.state_label.setText(
            self.t("toolbar.running") if busy else self.t("toolbar.idle")
        )
        self.state_label.setStyleSheet(
            f"color: {DARK['failure'] if busy else DARK['success']};"
        )
        self.send_button.setEnabled(not busy)
        event = self.selected_event()
        self.warning_label.setText(warning_text(event, self.translator) if event else "")

    def _selected_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return str(value) if value else None

    def selected_event(self) -> ActionEvent | None:
        event_id = self._selected_id()
        return self._events.get(event_id) if event_id else None

    def _render_selected(self) -> None:
        event = self.selected_event()
        if event is None:
            self.details.setPlainText(self.t("feed.no_selection"))
            self.raw.setPlainText(self.t("feed.no_selection"))
            self.kv_table.setRowCount(0)
            self.warning_label.setText("")
            return
        self.warning_label.setText(warning_text(event, self.translator))
        duration = f"\n\n{event.duration_ms:.0f} ms" if event.duration_ms is not None else ""
        self.details.setPlainText(
            f"{event_status_text(event, self.translator)} | "
            f"{event_action_text(event, self.translator)}\n\n"
            f"{event_summary(event, self.translator)}{duration}"
        )
        self.raw.setPlainText(raw_text(event, self.translator))
        rows = key_value_rows(event)
        self.kv_table.setRowCount(len(rows))
        for row, (field, value) in enumerate(rows):
            self.kv_table.setItem(row, 0, self._item(field))
            self.kv_table.setItem(row, 1, self._item(value))

    def prompt_value(self) -> str:
        return self.prompt_text.toPlainText().strip()

    def clear_prompt(self) -> None:
        self.prompt_text.clear()

    def submit(self) -> None:
        prompt = self.prompt_value()
        if prompt:
            self.on_send(self, prompt)


class SettingsDialog(QDialog):
    def __init__(self, main: QtMainWindow) -> None:
        super().__init__(main)
        self.main = main
        self.translator = main.translator
        self.settings_store = main.settings_store
        self.setWindowTitle(self.t("settings.title"))
        self.resize(860, 680)
        self._texts: list[tuple[Any, str]] = []
        self._build()
        self.apply_language()
        self._load()

    @property
    def t(self) -> Callable[..., str]:
        return self.translator.t

    def _label(self, key: str) -> QLabel:
        label = QLabel("")
        self._texts.append((label, key))
        return label

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        outer.addWidget(self.tabs, 1)
        self._build_connections()
        self._build_agent()
        self._build_confirmations()
        self._build_interface()
        self._build_mobile()
        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        outer.addWidget(self.status_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)

    def _profile_group(self, role: str) -> tuple[QGroupBox, dict[str, Any]]:
        group = QGroupBox("")
        self._texts.append(
            (group, "settings.writer" if role == "writer" else "settings.reviewer")
        )
        form = QFormLayout(group)
        hint = self._label(
            "settings.writer_hint" if role == "writer" else "settings.reviewer_hint"
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        url = QLineEdit()
        key = QLineEdit()
        key.setEchoMode(QLineEdit.EchoMode.Password)
        model = QComboBox()
        model.setEditable(True)
        fetch = QPushButton("")
        self._texts.append((fetch, "settings.fetch_models"))
        fetch_status = QLabel("")
        fetch_status.setObjectName("muted")
        fetch_row = QHBoxLayout()
        fetch_row.addWidget(model, 1)
        fetch_row.addWidget(fetch)
        temp = QDoubleSpinBox()
        temp.setRange(0.0, 2.0)
        temp.setSingleStep(0.1)
        temp.setDecimals(2)
        form.addRow(self._label("settings.base_url"), url)
        form.addRow(self._label("settings.api_key"), key)
        form.addRow(self._label("settings.model"), fetch_row)
        form.addRow("", fetch_status)
        form.addRow(self._label("settings.temperature"), temp)
        fetch.clicked.connect(lambda: self._fetch_models(url, key, model, temp, fetch_status))
        return group, {
            "url": url,
            "key": key,
            "model": model,
            "temp": temp,
            "status": fetch_status,
        }

    def _build_connections(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        writer, self.writer = self._profile_group("writer")
        reviewer, self.reviewer = self._profile_group("reviewer")
        layout.addWidget(writer)
        layout.addWidget(reviewer)
        layout.addStretch(1)
        self.tabs.addTab(page, "")

    def _build_agent(self) -> None:
        page = QWidget(self)
        form = QFormLayout(page)
        self.agent_iterations = QSpinBox()
        self.agent_iterations.setRange(1, 100)
        self.agent_command = QSpinBox()
        self.agent_command.setRange(5, 3600)
        self.agent_file = QSpinBox()
        self.agent_file.setRange(1024, 20_000_000)
        self.agent_file.setSingleStep(100_000)
        self.agent_request = QDoubleSpinBox()
        self.agent_request.setRange(5.0, 900.0)
        self.agent_request.setDecimals(1)
        self.agent_extra = QPlainTextEdit()
        self.agent_extra.setMaximumHeight(150)
        form.addRow(self._label("settings.agent.max_iterations"), self.agent_iterations)
        form.addRow(self._label("settings.agent.command_timeout"), self.agent_command)
        form.addRow(self._label("settings.agent.max_file"), self.agent_file)
        form.addRow(self._label("settings.agent.request_timeout"), self.agent_request)
        form.addRow(self._label("settings.agent.extra"), self.agent_extra)
        self.tabs.addTab(page, "")

    def _build_confirmations(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self.confirm_commands = QCheckBox("")
        self.confirm_tests = QCheckBox("")
        self.confirm_writes = QCheckBox("")
        self.confirm_packages = QCheckBox("")
        for key, check in (
            ("confirm.commands", self.confirm_commands),
            ("confirm.tests", self.confirm_tests),
            ("confirm.writes", self.confirm_writes),
            ("confirm.package_actions", self.confirm_packages),
        ):
            self._texts.append((check, key))
            layout.addWidget(check)
        note = self._label("confirm.note")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        self.tabs.addTab(page, "")

    def _build_interface(self) -> None:
        page = QWidget(self)
        form = QFormLayout(page)
        self.hide_console = QCheckBox("")
        self._texts.append((self.hide_console, "settings.hide_console"))
        form.addRow(self.hide_console)
        hint = self._label("settings.hide_console_hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Light", "light")
        self.language_combo = QComboBox()
        self.language_combo.addItem("Auto", "")
        for code in LANGUAGES:
            self.language_combo.addItem(code.upper(), code)
        form.addRow(self._label("settings.theme"), self.theme_combo)
        form.addRow(self._label("settings.language"), self.language_combo)
        self.tabs.addTab(page, "")

    def _build_mobile(self) -> None:
        page = QWidget(self)
        form = QFormLayout(page)
        self.server_port = QSpinBox()
        self.server_port.setRange(1024, 65535)
        self.server_pairing = QCheckBox("")
        self.server_mdns = QCheckBox("")
        self.server_auto = QCheckBox("")
        for key, widget in (
            ("settings.server.pairing", self.server_pairing),
            ("settings.server.mdns", self.server_mdns),
            ("settings.server.auto", self.server_auto),
        ):
            self._texts.append((widget, key))
        form.addRow(self._label("settings.server.port"), self.server_port)
        form.addRow(self.server_pairing)
        form.addRow(self.server_mdns)
        form.addRow(self.server_auto)
        hint = self._label("settings.server.hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        self.tabs.addTab(page, "")

    def _load(self) -> None:
        settings = self.settings_store.settings
        for role, widgets in (("writer", self.writer), ("reviewer", self.reviewer)):
            profile = settings.profile(role)
            widgets["url"].setText(profile.base_url)
            widgets["key"].setText(profile.api_key)
            widgets["model"].setCurrentText(profile.model)
            widgets["temp"].setValue(profile.temperature)
        self.agent_iterations.setValue(settings.agent.max_iterations)
        self.agent_command.setValue(settings.agent.command_timeout_seconds)
        self.agent_file.setValue(settings.agent.max_file_bytes)
        self.agent_request.setValue(settings.agent.request_timeout_seconds)
        self.agent_extra.setPlainText(settings.agent.system_prompt_extra)
        self.confirm_commands.setChecked(settings.confirmations.commands)
        self.confirm_tests.setChecked(settings.confirmations.tests)
        self.confirm_writes.setChecked(settings.confirmations.writes)
        self.confirm_packages.setChecked(settings.confirmations.package_actions)
        self.hide_console.setChecked(settings.ui.hide_console)
        self.theme_combo.setCurrentIndex(
            0 if settings.ui.theme == "dark" else 1
        )
        index = self.language_combo.findData(settings.ui.language)
        self.language_combo.setCurrentIndex(max(0, index))
        self.server_port.setValue(settings.server.port)
        self.server_pairing.setChecked(settings.server.require_pairing)
        self.server_mdns.setChecked(settings.server.advertise_mdns)
        self.server_auto.setChecked(settings.server.auto_start)

    def apply_language(self) -> None:
        self.setWindowTitle(self.t("settings.title"))
        for index, key in enumerate(
            (
                "settings.tab.connections",
                "settings.tab.agent",
                "settings.tab.confirmations",
                "settings.tab.interface",
                "settings.tab.server",
            )
        ):
            self.tabs.setTabText(index, self.t(key))
        for widget, key in self._texts:
            with contextlib.suppress(Exception):
                widget.setText(self.t(key))
        self.save_button.setText(self.t("settings.save"))
        self.cancel_button.setText(self.t("dialog.cancel"))
        self.theme_combo.setItemText(0, self.t("settings.theme.dark"))
        self.theme_combo.setItemText(1, self.t("settings.theme.light"))
        self.language_combo.setItemText(0, self.t("language.auto"))
        for index, code in enumerate(LANGUAGES, start=1):
            self.language_combo.setItemText(index, self.t(f"language.{code}"))

    def _profile(self, widgets: dict[str, Any]) -> ConnectionProfile:
        return ConnectionProfile(
            base_url=widgets["url"].text(),
            api_key=widgets["key"].text(),
            model=widgets["model"].currentText(),
            temperature=float(widgets["temp"].value()),
        )

    def _fetch_models(
        self,
        url: QLineEdit,
        key: QLineEdit,
        model: QComboBox,
        temp: QDoubleSpinBox,
        status: QLabel,
    ) -> None:
        profile = ConnectionProfile(
            base_url=url.text(),
            api_key=key.text(),
            model=model.currentText(),
            temperature=float(temp.value()),
        )
        if not profile.base_url:
            status.setText(self.t("settings.not_configured"))
            return

        async def load() -> list[str]:
            return await load_models(profile, timeout=30.0)

        status.setText(self.t("settings.fetching"))
        future = self.main.runner.submit_tracked(load())

        def done(models: list[str]) -> None:
            current = model.currentText()
            model.clear()
            model.addItems(models)
            if current:
                model.setCurrentText(current)
            status.setText(
                self.t("settings.models_fetched", count=len(models))
                if models
                else self.t("settings.no_models")
            )

        def failed(exc: Exception) -> None:
            if isinstance(exc, LLMError) and exc.code == "MODELS_MODULE_MISSING":
                status.setText(self.t("models.module_missing"))
            else:
                status.setText(self.t("settings.models_failed", error=exc))

        FutureWatcher(self, future, done, failed)

    def _save(self) -> None:
        try:
            settings = self.settings_store.settings
            writer = self._profile(self.writer)
            reviewer = self._profile(self.reviewer)
            agent = AgentSettings(
                max_iterations=self.agent_iterations.value(),
                command_timeout_seconds=self.agent_command.value(),
                max_file_bytes=self.agent_file.value(),
                request_timeout_seconds=float(self.agent_request.value()),
                system_prompt_extra=self.agent_extra.toPlainText(),
            )
            confirmations = ConfirmationSettings(
                commands=self.confirm_commands.isChecked(),
                tests=self.confirm_tests.isChecked(),
                writes=self.confirm_writes.isChecked(),
                package_actions=self.confirm_packages.isChecked(),
            )
            ui = UISettings(
                language=str(self.language_combo.currentData() or ""),
                hide_console=self.hide_console.isChecked(),
                theme=str(self.theme_combo.currentData() or "dark"),
                last_project=settings.ui.last_project,
            )
            server = ServerSettings(
                port=self.server_port.value(),
                require_pairing=self.server_pairing.isChecked(),
                advertise_mdns=self.server_mdns.isChecked(),
                auto_start=self.server_auto.isChecked(),
            )

            def mutate(candidate: AppSettings) -> None:
                candidate.profiles = {"writer": writer, "reviewer": reviewer}
                candidate.agent = agent
                candidate.confirmations = confirmations
                candidate.ui = ui
                candidate.server = server

            self.settings_store.update(mutate)
        except Exception as exc:
            QMessageBox.critical(self, self.t("dialog.error"), str(exc))
            return
        self.main.preferences_changed()
        self.accept()


class ReviewerDialog(QDialog):
    def __init__(self, main: QtMainWindow) -> None:
        super().__init__(main)
        self.main = main
        self.translator = main.translator
        self.setWindowTitle(self.t("reviewer.title"))
        self.resize(900, 720)
        layout = QVBoxLayout(self)
        self.model_label = QLabel("")
        self.model_label.setObjectName("muted")
        layout.addWidget(self.model_label)
        layout.addWidget(QLabel(self.t("reviewer.instructions")))
        self.instructions = QPlainTextEdit()
        self.instructions.setMaximumHeight(90)
        layout.addWidget(self.instructions)
        layout.addWidget(QLabel(self.t("reviewer.material")))
        self.material = QPlainTextEdit()
        layout.addWidget(self.material, 1)
        actions = QHBoxLayout()
        attach = QPushButton(self.t("reviewer.attach"))
        attach.clicked.connect(self._attach)
        take = QPushButton(self.t("reviewer.take_event"))
        take.clicked.connect(lambda: self._take(True))
        raw = QPushButton(self.t("reviewer.attach_raw"))
        raw.clicked.connect(lambda: self._take(False))
        context = QPushButton(self.t("reviewer.session_context"))
        context.clicked.connect(self._context)
        self.run_button = QPushButton(self.t("reviewer.run"))
        self.run_button.clicked.connect(self._run)
        for widget in (attach, take, raw, context):
            actions.addWidget(widget)
        actions.addStretch(1)
        actions.addWidget(self.run_button)
        layout.addLayout(actions)
        layout.addWidget(QLabel(self.t("reviewer.result")))
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        layout.addWidget(self.result, 1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        layout.addWidget(self.status_label)
        profile = main.settings_store.settings.profile("reviewer")
        self.model_label.setText(
            self.t("reviewer.model", model=profile.model or self.t("gui.model_none"))
        )
        if not profile.is_configured:
            self.status_label.setText(self.t("reviewer.no_profile"))

    @property
    def t(self) -> Callable[..., str]:
        return self.translator.t

    def _attach(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self)
        if not filename:
            return
        path = Path(filename)
        try:
            limit = self.main.settings_store.settings.agent.max_file_bytes
            if path.stat().st_size > limit:
                raise OSError(f"file is larger than {limit} bytes")
            self.material.setPlainText(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            self.status_label.setText(self.t("reviewer.file_failed", error=exc))
            return
        self.status_label.setText(self.t("reviewer.file_loaded", name=path.name))

    def _take(self, prefer_raw: bool) -> None:
        tab = self.main.selected_tab()
        event = tab.selected_event() if tab is not None else None
        if event is None:
            self.status_label.setText(self.t("gui.no_event_selected"))
            return
        from doctorcode.ui.feed_model import selected_action_material

        self.material.setPlainText(
            selected_action_material(event, prefer_raw=prefer_raw)
        )
        self.status_label.setText(f"{event.action_type.value}: {event.human_summary}")

    def _context(self) -> None:
        session = self.main.selected_session()
        if session is None:
            self.status_label.setText(self.t("gui.no_selection"))
            return
        self.material.setPlainText(session.review_material())
        self.status_label.setText(session.project_name)

    def _run(self) -> None:
        session = self.main.selected_session()
        if session is None:
            self.status_label.setText(self.t("gui.no_selection"))
            return
        profile = self.main.settings_store.settings.profile("reviewer")
        if not profile.is_configured:
            self.status_label.setText(self.t("reviewer.no_profile"))
            return
        material = self.material.toPlainText().strip()
        if not material:
            self.status_label.setText(self.t("reviewer.empty"))
            return
        service = ReviewerService(
            settings_store=self.main.settings_store,
            feed=ActionFeed(),
            logger=self.main.logger,
        )
        self.run_button.setEnabled(False)
        self.status_label.setText(self.t("reviewer.running"))
        future = self.main.runner.submit_tracked(
            service.review(material, self.instructions.toPlainText().strip())
        )

        def done(review: Any) -> None:
            self.run_button.setEnabled(True)
            if review.status is ActionStatus.SUCCESS:
                self.result.setPlainText(review.text)
                self.status_label.setText(self.t("reviewer.done"))
            else:
                message = self.t("reviewer.failed", error=review.error_code or "")
                self.result.setPlainText(message)
                self.status_label.setText(message)
            try:
                current = self.main.workspace.get(session.session_id)
            except KeyError:
                return
            current.feed.append(
                ActionEvent(
                    session_id=current.session_id,
                    action_type=ActionType.REVIEW,
                    status=review.status,
                    human_summary=(
                        f"Reviewer analysed the material with {review.model or 'the reviewer model'}."
                        if review.status is ActionStatus.SUCCESS
                        else f"Review failed: {review.error_code or 'unknown error'}."
                    ),
                    summary_key=(
                        "review.done"
                        if review.status is ActionStatus.SUCCESS
                        else "review.failed"
                    ),
                    summary_params=(
                        {"model": review.model or ""}
                        if review.status is ActionStatus.SUCCESS
                        else {"error": review.error_code or ""}
                    ),
                    raw_payload=review.text or review.raw_payload,
                    metadata={"model": review.model, "read_only": True},
                )
            )

        def failed(exc: Exception) -> None:
            self.run_button.setEnabled(True)
            text = self.t("reviewer.failed", error=exc)
            self.status_label.setText(text)
            self.result.setPlainText(text)

        FutureWatcher(self, future, done, failed)


class MobileDialog(QDialog):
    def __init__(self, main: QtMainWindow) -> None:
        super().__init__(main)
        self.main = main
        self.translator = main.translator
        self.setWindowTitle(self.t("mobile.title"))
        self.resize(620, 400)
        layout = QVBoxLayout(self)
        self.status_label = QLabel("")
        font = self.status_label.font()
        font.setPointSize(font.pointSize() + 2)
        self.status_label.setFont(font)
        layout.addWidget(self.status_label)
        hint = QLabel(self.t("mobile.discovery"))
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)
        layout.addWidget(QLabel(self.t("mobile.pairing_code")))
        self.code_label = QLabel("------")
        code_font = self.code_label.font()
        code_font.setFamily("Consolas")
        code_font.setPointSize(28)
        code_font.setBold(True)
        self.code_label.setFont(code_font)
        layout.addWidget(self.code_label)
        pairing_hint = QLabel(self.t("mobile.pairing_hint"))
        pairing_hint.setWordWrap(True)
        pairing_hint.setObjectName("muted")
        layout.addWidget(pairing_hint)
        layout.addWidget(QLabel(self.t("mobile.address")))
        address_row = QHBoxLayout()
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        address_row.addWidget(self.address, 1)
        copy = QPushButton(self.t("mobile.copy_address"))
        copy.clicked.connect(self._copy)
        address_row.addWidget(copy)
        layout.addLayout(address_row)
        controls = QHBoxLayout()
        self.start_button = QPushButton(self.t("mobile.start"))
        self.start_button.clicked.connect(self._start)
        self.stop_button = QPushButton(self.t("mobile.stop"))
        self.stop_button.clicked.connect(self._stop)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.status_detail = QLabel("")
        self.status_detail.setObjectName("muted")
        layout.addWidget(self.status_detail)
        self._busy = False
        self._sync()

    @property
    def t(self) -> Callable[..., str]:
        return self.translator.t

    def _sync(self) -> None:
        info = self.main.mobile_controller.info
        if info is None:
            self.status_label.setText(self.t("mobile.status_stopped"))
            self.code_label.setText("------")
            self.address.setText("")
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            return
        address = next(
            (url for url in info.urls if "127.0.0.1" not in url),
            info.urls[0] if info.urls else f"http://{info.host}:{info.port}",
        )
        self.status_label.setText(self.t("mobile.status_running", address=address))
        self.code_label.setText(info.pairing_code)
        self.address.setText(address)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.address.text())
        self.status_detail.setText(self.t("dialog.copied"))

    def _start(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.status_label.setText(self.t("mobile.status_starting"))
        self.start_button.setEnabled(False)
        future = self.main.runtime.executor.submit(self.main.mobile_controller.start)
        FutureWatcher(self, future, lambda _info: self._finished(), self._failed)

    def _stop(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.stop_button.setEnabled(False)
        future = self.main.runtime.executor.submit(self.main.mobile_controller.stop)
        FutureWatcher(self, future, lambda _value: self._finished(), self._failed)

    def _finished(self) -> None:
        self._busy = False
        self._sync()

    def _failed(self, exc: Exception) -> None:
        self._busy = False
        self.status_label.setText(self.t("mobile.server_error", error=exc))
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)


class ModulesDialog(QDialog):
    def __init__(self, main: QtMainWindow) -> None:
        super().__init__(main)
        self.main = main
        self.translator = main.translator
        self.setWindowTitle(self.t("modules.title"))
        self.resize(820, 620)
        self._token: CancellationToken | None = None
        self._future: Future[Any] | None = None
        self._last_action = "install"
        layout = QVBoxLayout(self)
        intro = QLabel(self.t("modules.intro"))
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)
        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(
            [
                self.t("modules.name"),
                self.t("modules.package_name"),
                self.t("modules.status"),
                self.t("modules.version"),
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        layout.addWidget(self.table, 1)
        controls = QHBoxLayout()
        for key, action in (
            ("modules.install", "install"),
            ("modules.update", "update"),
            ("modules.remove", "remove"),
        ):
            button = QPushButton(self.t(key))
            button.clicked.connect(lambda _checked=False, action=action: self._start(action))
            controls.addWidget(button)
        refresh = QPushButton(self.t("modules.refresh"))
        refresh.clicked.connect(self._refresh)
        controls.addWidget(refresh)
        controls.addStretch(1)
        self.cancel_button = QPushButton(self.t("modules.cancel"))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(150)
        layout.addWidget(self.output)
        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        layout.addWidget(self.status_label)
        self._refresh()

    @property
    def t(self) -> Callable[..., str]:
        return self.translator.t

    def _refresh(self) -> None:
        selected = {
            self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
            for index in self.table.selectedIndexes()
            if self.table.item(index.row(), 0) is not None
        }
        self.table.setRowCount(0)
        for row in module_rows():
            index = self.table.rowCount()
            self.table.insertRow(index)
            values = (
                self.t(row.spec.label_key),
                row.spec.distribution,
                self.t(row.status_key),
                row.version or self.t("common.not_available"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row.spec.key)
                self.table.setItem(index, column, item)
            if row.spec.key in selected:
                self.table.selectRow(index)

    def _selected_specs(self) -> list[ModuleSpec]:
        keys: list[str] = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item is not None:
                keys.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return [find_module(key) for key in keys if key in {m.key for m in MODULES}]

    def _start(self, action: str) -> None:
        specs = self._selected_specs()
        if not specs:
            QMessageBox.information(self, self.t("modules.title"), self.t("modules.no_selection"))
            return
        if not pip_available():
            QMessageBox.critical(self, self.t("dialog.error"), self.t("modules.missing_pip"))
            return
        names = "\n".join(f"{spec.distribution} ({spec.extra})" for spec in specs)
        if action == "remove":
            if not ask_yes_no(
                self,
                self.translator,
                self.t("dialog.confirm_remove_title"),
                self.t("dialog.confirm_remove_body", names=names),
            ):
                return
        elif action in ("install", "update"):
            consent = (
                "modules.consent_install"
                if action == "install"
                else "modules.consent_update"
            )
            if not ask_yes_no(
                self,
                self.translator,
                self.t("modules.title"),
                self.t(consent) + "\n\n" + names,
            ):
                return
        self._token = CancellationToken()
        self._last_action = action
        self.cancel_button.setEnabled(True)
        self.status_label.setText(self.t("modules.working"))
        self.output.setPlainText("")
        self._future = self.main.runtime.executor.submit(
            self._worker, action, specs, self._token
        )
        FutureWatcher(self, self._future, self._finished, self._failed)

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
                    logger=self.main.logger,
                )
            except OperationCancelledError:
                break
            except (OSError, ValueError) as exc:
                results.append((spec, False, str(exc)))
                continue
            if result.cancelled:
                break
            if result.returncode == 0:
                results.append((spec, True, ""))
            else:
                output = (str(result.stderr) or str(result.stdout)).strip()
                results.append((spec, False, "\n".join(output.splitlines()[-8:])))
        return results

    def _finished(self, results: list[tuple[ModuleSpec, bool, str]]) -> None:
        cancelled = self._token is not None and self._token.is_cancelled
        self.cancel_button.setEnabled(False)
        self._token = None
        self._refresh()
        self.status_label.setText(
            self.t("modules.cancelled") if cancelled else self.t("modules.succeeded")
        )
        lines: list[str] = []
        for spec, ok, detail in results:
            label = self.t(spec.label_key)
            if ok:
                lines.append(self.t(f"modules.{self._last_action}_result", name=label))
            else:
                lines.append(self.t("modules.error_result", name=label, error=detail))
        self.output.setPlainText("\n".join(lines))

    def _failed(self, exc: Exception) -> None:
        self.cancel_button.setEnabled(False)
        self._token = None
        self.status_label.setText(self.t("modules.error_result", name="", error=exc))

    def _cancel(self) -> None:
        if self._token is not None:
            self._token.cancel()
            self.status_label.setText(self.t("modules.cancelled"))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._token is not None:
            self._token.cancel()
        super().closeEvent(event)


class QtMainWindow(QMainWindow):
    def __init__(self, runtime: AppRuntime) -> None:
        super().__init__()
        self.runtime = runtime
        self.settings_store = runtime.settings_store
        self.translator = Translator(self.settings_store.settings.ui.language)
        self.logger = runtime.core.logger
        self.runner = AsyncRunner(self.logger)
        self._event_queue: queue.Queue[ActionEvent] = queue.Queue()
        self.workspace = WorkspaceManager(
            app_data_dir=runtime.data_dir,
            settings_store=self.settings_store,
            executor=runtime.executor,
            logger=self.logger,
        )
        self.workspace.subscribe_all(self._event_queue.put)
        try:
            self.kimi_import: KimiImportService | None = KimiImportService(
                workspace=self.workspace,
                data_dir=runtime.data_dir,
            )
        except ConnectModuleMissing:
            # Optional pluggable module: the menu entry offers to connect it.
            self.logger.warning("kimi_import.module_missing")
            self.kimi_import = None
        self.approvals = ApprovalBroker()
        self.mobile_controller = MobileServerController(
            manager=self.workspace,
            settings_store=self.settings_store,
            translator=self.translator,
            logger=self.logger,
        )
        self._tabs: dict[str, QtProjectTab] = {}
        self._switching: set[str] = set()
        self._models: list[str] = []
        self._closing = False
        self._closed = False
        self._syncing = False
        self._text_refs: list[tuple[Any, str]] = []
        self._toolbar_actions: dict[str, QAction] = {}
        self._build_ui()
        self._retranslate()
        self._restore_last_project()
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._poll_events)
        self._timer.start()
        self._approval_timer = QTimer(self)
        self._approval_timer.setInterval(120)
        self._approval_timer.timeout.connect(self._poll_approvals)
        self._approval_timer.start()
        QTimer.singleShot(400, self._maybe_auto_start_server)

    @property
    def t(self) -> Callable[..., str]:
        return self.translator.t

    def _build_ui(self) -> None:
        self.setWindowTitle(self.t("app.title"))
        self.resize(1280, 840)
        self.setMinimumSize(1000, 640)
        self.toolbar = QToolBar(self)
        self.toolbar.setMovable(False)
        self.addToolBar(self.toolbar)
        self._build_toolbar()
        self._build_menus()
        self.status = QStatusBar(self)
        self.setStatusBar(self.status)
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("muted")
        self.status.addPermanentWidget(self.detail_label)
        self.stack = QStackedWidget(self)
        self.setCentralWidget(self.stack)
        self.welcome = QWidget(self.stack)
        welcome_layout = QVBoxLayout(self.welcome)
        welcome_layout.addStretch(1)
        self.welcome_title = QLabel("", self.welcome)
        title_font = self.welcome_title.font()
        title_font.setPointSize(title_font.pointSize() + 10)
        title_font.setBold(True)
        self.welcome_title.setFont(title_font)
        self.welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(self.welcome_title)
        self.welcome_hint = QLabel("", self.welcome)
        self.welcome_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.welcome_hint.setObjectName("muted")
        welcome_layout.addWidget(self.welcome_hint)
        open_button = QPushButton("", self.welcome)
        open_button.clicked.connect(self.open_project_dialog)
        self.welcome_button = open_button
        welcome_layout.addWidget(open_button, 0, Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addStretch(1)
        self.stack.addWidget(self.welcome)
        self.tabs = QTabWidget(self.stack)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab_index)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.stack.addWidget(self.tabs)
        self._sync_empty()
        self.apply_theme()

    def _build_toolbar(self) -> None:
        def action(key: str, callback: Callable[[], None]) -> QAction:
            item = QAction("", self)
            item.triggered.connect(lambda _checked=False: callback())
            self.toolbar.addAction(item)
            self._toolbar_actions[key] = item
            return item

        action("toolbar.open", self.open_project_dialog)
        action("toolbar.add", self.open_project_dialog)
        self.run_action = action("toolbar.run", self.run_selected)
        self.stop_action = action("toolbar.stop", self.stop_selected)
        self.auto_action = QAction("", self)
        self.auto_action.setCheckable(True)
        self.auto_action.toggled.connect(self._toggle_auto)
        self.toolbar.addAction(self.auto_action)
        self.toolbar.addSeparator()
        self.model_label = QLabel("", self)
        self.toolbar.addWidget(self.model_label)
        self.model_combo = QComboBox(self)
        self.model_combo.setEditable(False)
        self.model_combo.setMinimumWidth(240)
        self.model_combo.currentTextChanged.connect(self._on_model_chosen)
        self.toolbar.addWidget(self.model_combo)
        self.refresh_action = action("toolbar.refresh_models", self.refresh_models)
        self.toolbar.addSeparator()
        self.language_buttons: dict[str, QPushButton] = {}
        for code in LANGUAGES:
            button = QPushButton(self.t(f"language.flag.{code}"), self)
            button.setFixedWidth(36)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, code=code: self.set_language(code))
            self.toolbar.addWidget(button)
            self.language_buttons[code] = button

    def _build_menus(self) -> None:
        old = [
            getattr(self, name, None)
            for name in ("_file_menu", "_tools_menu", "_language_menu", "_help_menu")
        ]
        self.menuBar().clear()
        for menu in old:
            if menu is not None:
                menu.deleteLater()
        file_menu = self.menuBar().addMenu("")
        self._file_menu = file_menu
        file_menu.addAction(self.t("toolbar.open"), self.open_project_dialog)
        file_menu.addAction(self.t("toolbar.add"), self.open_project_dialog)
        file_menu.addAction(self.t("gui.close_tab"), self.close_selected_tab)
        file_menu.addSeparator()
        file_menu.addAction(self.t("gui.create_handoff"), self.create_handoff)
        file_menu.addAction(self.t("menu.import_kimi"), self.open_kimi_import)
        file_menu.addSeparator()
        file_menu.addAction(self.t("menu.quit"), self.close)
        tools_menu = self.menuBar().addMenu("")
        self._tools_menu = tools_menu
        tools_menu.addAction(self.t("menu.settings"), self.open_settings)
        tools_menu.addAction(self.t("menu.modules"), self.open_modules)
        tools_menu.addAction(self.t("menu.reviewer"), self.open_reviewer)
        tools_menu.addAction(self.t("menu.mobile"), self.open_mobile)
        language_menu = self.menuBar().addMenu("")
        self._language_menu = language_menu
        group = QActionGroup(self)
        group.setExclusive(True)
        for code in LANGUAGES:
            item = QAction(
                f"{self.t(f'language.flag.{code}')}  {self.t(f'language.{code}')}",
                self,
            )
            item.setCheckable(True)
            item.setChecked(code == self.translator.language)
            item.triggered.connect(lambda _checked=False, code=code: self.set_language(code))
            group.addAction(item)
            language_menu.addAction(item)
        help_menu = self.menuBar().addMenu("")
        self._help_menu = help_menu
        help_menu.addAction(self.t("gui.about"), self.open_about)

    def apply_theme(self) -> None:
        theme = self.settings_store.settings.ui.theme
        self.setStyleSheet(_stylesheet(theme))

    def _retranslate(self) -> None:
        self.setWindowTitle(self.t("app.title"))
        for key, action in self._toolbar_actions.items():
            action.setText(self.t(key))
        self.auto_action.setText(self.t("toolbar.no_watch"))
        self.model_label.setText(self.t("toolbar.model"))
        self.welcome_title.setText(self.t("gui.center_text"))
        self.welcome_hint.setText(self.t("gui.open_project_hint"))
        self.welcome_button.setText(self.t("toolbar.open"))
        self._build_menus()
        self.menuBar().actions()[0].setText(self.t("menu.file"))
        self.menuBar().actions()[1].setText(self.t("menu.tools"))
        self.menuBar().actions()[2].setText(self.t("menu.language"))
        self.menuBar().actions()[3].setText(self.t("menu.help"))
        for code, button in self.language_buttons.items():
            button.setChecked(code == self.translator.language)
        for tab in self._tabs.values():
            tab.apply_language()
        self._refresh_toolbar()
        self._sync_title()

    def preferences_changed(self) -> None:
        self.translator.set_language(resolve_language(self.settings_store.settings.ui.language))
        self.apply_theme()
        self._retranslate()
        self.status.showMessage(self.t("gui.settings_saved"), 5000)

    def set_language(self, code: str) -> None:
        if code not in LANGUAGES:
            return
        try:
            self.settings_store.set_language(code)
        except Exception as exc:
            QMessageBox.critical(
                self, self.t("dialog.error"), self.t("language.switch_failed", error=exc)
            )
            return
        self.translator.set_language(code)
        self._retranslate()
        self.status.showMessage(self.t("gui.language_changed"), 4000)

    def selected_tab(self) -> QtProjectTab | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, QtProjectTab) else None

    def selected_session(self) -> AgentSession | None:
        tab = self.selected_tab()
        return tab.session if tab is not None else None

    def _sync_empty(self) -> None:
        self.stack.setCurrentWidget(self.welcome if not self._tabs else self.tabs)

    def _sync_title(self) -> None:
        tab = self.selected_tab()
        self.setWindowTitle(
            self.t("app.title") if tab is None else f"{self.t('app.title')} — {tab.session.project_name}"
        )

    def _on_tab_changed(self, _index: int) -> None:
        self._sync_title()
        self._refresh_toolbar()

    def _make_tab(self, session: AgentSession, *, select: bool = True) -> QtProjectTab:
        existing = self._tabs.get(session.session_id)
        if existing is not None:
            if select:
                self.tabs.setCurrentWidget(existing)
            return existing
        tab = QtProjectTab(
            self.tabs,
            session=session,
            translator=self.translator,
            on_send=self._send_prompt,
            on_close=self._close_tab_widget,
        )
        self._tabs[session.session_id] = tab
        index = self.tabs.addTab(tab, session.project_name)
        session.set_approval_callback(self.approvals.request)
        if select:
            self.tabs.setCurrentIndex(index)
            self._sync_title()
        self._sync_empty()
        return tab

    def open_project_dialog(self) -> None:
        initial = self.settings_store.settings.ui.last_project or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, self.t("project.open_title"), initial)
        if folder:
            self.open_project(folder)

    def open_project(self, folder: str | Path, *, announce: bool = True) -> None:
        try:
            session = self.workspace.open_project(folder)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, self.t("dialog.error"), self.t("project.open_failed", error=exc)
            )
            return
        self._make_tab(session)
        if announce:
            self.status.showMessage(self.t("gui.project_open", name=session.project_name), 5000)
        self._refresh_toolbar()

    def _restore_last_project(self) -> None:
        last = self.settings_store.settings.ui.last_project
        if last and Path(last).is_dir():
            with contextlib.suppress(Exception):
                self.open_project(last, announce=False)

    def _close_tab_widget(self, tab: QtProjectTab) -> None:
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self._close_tab_index(index)

    def _close_tab_index(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if not isinstance(tab, QtProjectTab):
            return
        session = tab.session
        if session.busy and not ask_yes_no(
            self,
            self.translator,
            self.t("dialog.warning"),
            self.t("gui.close_tab_confirm", name=session.project_name),
        ):
            return
        self.workspace.close_project(session.session_id)
        self._tabs.pop(session.session_id, None)
        self.tabs.removeTab(index)
        tab.deleteLater()
        self._sync_empty()
        self._refresh_toolbar()
        self._sync_title()

    def close_selected_tab(self) -> None:
        index = self.tabs.currentIndex()
        if index >= 0:
            self._close_tab_index(index)
        else:
            self.status.showMessage(self.t("gui.no_selection"), 4000)

    def _send_prompt(self, tab: QtProjectTab, prompt: str) -> None:
        session = tab.session
        if session.busy:
            self.status.showMessage(self.t("gui.busy"), 5000)
            return
        future = self.runner.submit_tracked(session.run_prompt(prompt))
        tab.clear_prompt()
        tab.refresh_state()
        self.status.showMessage(self.t("toolbar.running"))
        self._refresh_toolbar()
        FutureWatcher(
            self,
            future,
            lambda result: self._prompt_done(tab, result),
            lambda exc: self._prompt_failed(tab, exc),
        )

    def _prompt_done(self, tab: QtProjectTab, result: Any) -> None:
        tab.refresh_state()
        self._refresh_toolbar()
        if getattr(result, "status", None) is ActionStatus.SUCCESS:
            self.status.showMessage(self.t("toolbar.idle"), 4000)
        elif getattr(result, "error_code", "") == "CANCELLED":
            self.status.showMessage(self.t("gui.stop_sent"), 4000)
        else:
            self.status.showMessage(
                self.t("gui.unknown_error", error=getattr(result, "error_code", "")),
                6000,
            )

    def _prompt_failed(self, tab: QtProjectTab, exc: Exception) -> None:
        tab.refresh_state()
        self._refresh_toolbar()
        self.status.showMessage(self.t("gui.unknown_error", error=exc), 8000)

    def run_selected(self) -> None:
        tab = self.selected_tab()
        if tab is None:
            self.status.showMessage(self.t("gui.no_selection"), 4000)
            return
        tab.submit()

    def stop_selected(self) -> None:
        session = self.selected_session()
        if session is None:
            self.status.showMessage(self.t("gui.no_selection"), 4000)
            return
        stopped = session.stop()
        self.status.showMessage(
            self.t("gui.stop_sent") if stopped else self.t("gui.stop_idle"), 4000
        )
        self._refresh_toolbar()

    def _toggle_auto(self, enabled: bool) -> None:
        if self._syncing:
            return
        session = self.selected_session()
        if session is None:
            self._set_auto(False)
            return
        if enabled and not session.auto_mode and not ask_yes_no(
            self,
            self.translator,
            self.t("toolbar.no_watch"),
            self.t("gui.auto_confirm"),
        ):
            self._set_auto(False)
            return
        session.set_auto_mode(enabled)
        self._refresh_toolbar()

    def _set_auto(self, value: bool) -> None:
        self._syncing = True
        try:
            self.auto_action.setChecked(value)
        finally:
            self._syncing = False

    def _refresh_toolbar(self) -> None:
        tab = self.selected_tab()
        session = tab.session if tab is not None else None
        profile = self.settings_store.settings.profile("writer")
        self._syncing = True
        try:
            if session is not None:
                self.model_combo.setEnabled(True)
                self.auto_action.setEnabled(True)
                self.auto_action.setChecked(session.auto_mode)
                self.model_combo.setCurrentText(session.model)
                self.run_action.setEnabled(not session.busy)
                self.stop_action.setEnabled(session.busy)
            else:
                self.model_combo.setEnabled(False)
                self.auto_action.setEnabled(False)
                self.auto_action.setChecked(False)
                self.model_combo.setCurrentText(profile.model)
                self.run_action.setEnabled(False)
                self.stop_action.setEnabled(False)
        finally:
            self._syncing = False
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
        self.model_combo.blockSignals(True)
        try:
            current = self.model_combo.currentText()
            self.model_combo.clear()
            self.model_combo.addItems(values)
            if current:
                self.model_combo.setCurrentText(current)
        finally:
            self.model_combo.blockSignals(False)
        self.detail_label.setText(
            self.t("gui.status_events", count=len(session.feed))
            if session is not None
            else self.t("gui.no_projects_open")
        )

    def _on_model_chosen(self, model: str) -> None:
        if self._syncing or not model:
            return
        session = self.selected_session()
        if session is None or model == session.model:
            return
        if session.busy:
            QMessageBox.warning(self, self.t("dialog.warning"), self.t("gui.busy"))
            self._refresh_toolbar()
            return
        if session.session_id in self._switching:
            return
        self._switching.add(session.session_id)
        self.status.showMessage(self.t("gui.model_switch_running"))
        future = self.runtime.executor.submit(session.switch_model, model)

        def done(_snapshot: Any) -> None:
            self._switching.discard(session.session_id)
            tab = self._tabs.get(session.session_id)
            if tab is not None:
                tab.refresh_state()
            self._refresh_toolbar()
            self.status.showMessage(self.t("gui.handoff_done", model=model), 5000)

        def failed(exc: Exception) -> None:
            self._switching.discard(session.session_id)
            self._refresh_toolbar()
            QMessageBox.critical(
                self,
                self.t("dialog.error"),
                self.t("gui.model_switch_failed", error=exc),
            )

        FutureWatcher(self, future, done, failed)

    def refresh_models(self) -> None:
        profile = self.settings_store.settings.profile("writer")
        if not profile.base_url:
            self.status.showMessage(self.t("gui.settings_required"), 5000)
            return

        async def load() -> list[str]:
            return await load_models(profile, timeout=30.0)

        self.status.showMessage(self.t("settings.fetching"))
        future = self.runner.submit_tracked(load())

        def done(models: list[str]) -> None:
            self._models = list(dict.fromkeys(models))
            self._refresh_toolbar()
            self.status.showMessage(
                self.t("settings.models_fetched", count=len(self._models))
                if self._models
                else self.t("settings.no_models"),
                5000,
            )

        def failed(exc: Exception) -> None:
            if isinstance(exc, LLMError) and exc.code == "MODELS_MODULE_MISSING":
                self.status.showMessage(self.t("models.module_missing"), 8000)
            else:
                self.status.showMessage(self.t("gui.model_refresh_failed", error=exc), 8000)

        FutureWatcher(self, future, done, failed)

    def create_handoff(self) -> None:
        session = self.selected_session()
        if session is None:
            self.status.showMessage(self.t("gui.no_selection"), 4000)
            return
        if session.busy:
            self.status.showMessage(self.t("gui.busy"), 5000)
            return
        self.status.showMessage(self.t("gui.handoff_running"))
        future = self.runtime.executor.submit(session.create_handoff, "manual")

        def done(path: Path) -> None:
            self.status.showMessage(self.t("gui.handoff_saved", path=path), 6000)

        def failed(exc: Exception) -> None:
            QMessageBox.critical(
                self, self.t("dialog.error"), self.t("gui.handoff_failed", error=exc)
            )

        FutureWatcher(self, future, done, failed)

    def open_settings(self) -> None:
        SettingsDialog(self).exec()

    def open_modules(self) -> None:
        ModulesDialog(self).exec()

    def open_reviewer(self) -> None:
        ReviewerDialog(self).exec()

    def open_mobile(self) -> None:
        MobileDialog(self).exec()

    def open_kimi_import(self) -> None:
        if self.kimi_import is None:
            answer = QMessageBox.question(
                self,
                self.t("import.module_missing_title"),
                self.t("import.module_missing_body"),
            )
            if answer is QMessageBox.StandardButton.Yes:
                self.open_modules()
            return
        KimiImportDialog(self).exec()

    def open_about(self) -> None:
        QMessageBox.about(
            self,
            self.t("gui.about"),
            f"{self.t('app.title')}\n{self.t('app.subtitle')}\n\n"
            f"{self.t('app.version', version=__version__)}\n{self.runtime.data_dir}",
        )

    def _poll_events(self) -> None:
        if self._closing:
            return
        try:
            for _ in range(250):
                try:
                    event = self._event_queue.get_nowait()
                except queue.Empty:
                    break
                self._apply_event(event)
        except Exception:
            self.logger.exception("ui.qt_event_poll_failed")

    def _apply_event(self, event: ActionEvent) -> None:
        try:
            session = self.workspace.get(event.session_id)
        except KeyError:
            return
        tab = self._tabs.get(event.session_id)
        if tab is None:
            tab = self._make_tab(session, select=False)
        tab.upsert(event)
        if self.selected_session() is session:
            self._refresh_toolbar()

    def _poll_approvals(self) -> None:
        if self._closing:
            return
        pending = self.approvals.next_pending()
        if pending is None:
            return
        details = "\n".join(
            f"{key}: {value}" for key, value in pending.request.details.items()
        ) or self.t("common.not_available")
        approved = ask_yes_no(
            self,
            self.translator,
            self.t("dialog.approval_title"),
            self.t("dialog.approval_body", summary=pending.request.title, details=details),
            yes_key="dialog.approve_once",
            no_key="dialog.deny",
        )
        pending.resolve(approved)

    def _maybe_auto_start_server(self) -> None:
        if self._closing:
            return
        if not self.settings_store.settings.server.auto_start:
            return
        if self.mobile_controller.running:
            return
        self.status.showMessage(self.t("mobile.status_starting"))
        future = self.runtime.executor.submit(self.mobile_controller.start)

        def done(info: ServerInfo) -> None:
            urls = [url for url in info.urls if "127.0.0.1" not in url]
            address = urls[0] if urls else f"http://{info.host}:{info.port}"
            self.status.showMessage(self.t("mobile.status_running", address=address))

        def failed(exc: Exception) -> None:
            self.status.showMessage(self.t("mobile.server_error", error=exc), 8000)

        FutureWatcher(self, future, done, failed)

    def closeEvent(self, event: QCloseEvent) -> None:
        sessions = self.workspace.list()
        if any(session.busy for session in sessions) and not ask_yes_no(
            self,
            self.translator,
            self.t("dialog.quit_title"),
            self.t("dialog.quit_body"),
        ):
            event.ignore()
            return
        self.shutdown()
        event.accept()

    def shutdown(self) -> None:
        if self._closed or self._closing:
            return
        self._closing = True
        try:
            self.approvals.close()
            for session in self.workspace.list():
                session.stop()
            with contextlib.suppress(Exception):
                self.mobile_controller.stop()
            self.workspace.shutdown()
            self.runner.shutdown()
        finally:
            self._closed = True


def run(argv: Sequence[str] | None = None) -> int:
    runtime = create_runtime()
    window: QtMainWindow | None = None
    try:
        app = QApplication(list(argv if argv is not None else sys.argv))
        app.setApplicationName("DoctorCode")
        app.setApplicationVersion(__version__)
        window = QtMainWindow(runtime)
        window.show()
        return app.exec()
    finally:
        if window is not None and not window._closed:
            window.shutdown()
        runtime.close()
