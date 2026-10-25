"""Shared fixtures for DoctorCode's engine and tool tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from doctorcode.engine.session import AgentSession
from doctorcode.settings import SettingsStore


def configure(store: SettingsStore, *, model: str = "test-model") -> None:
    """Point both profiles at a local dummy endpoint; tests never call it."""

    def mutate(settings):
        writer = settings.profiles["writer"]
        writer.base_url = "http://127.0.0.1:9/v1"
        writer.api_key = "test-key"
        writer.model = model
        reviewer = settings.profiles["reviewer"]
        reviewer.base_url = "http://127.0.0.1:9/v1"
        reviewer.model = "review-model"

    store.update(mutate)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


@pytest.fixture
def app_data(tmp_path: Path) -> Path:
    return tmp_path / "app-data"


@pytest.fixture
def settings_store(app_data: Path) -> SettingsStore:
    store = SettingsStore(app_data / "settings.json")
    configure(store)
    return store


@pytest.fixture
def make_session(settings_store: SettingsStore, app_data: Path):
    """Build an AgentSession with isolated persistence and a fake client."""

    from doctorcode.engine.context import HandoffStore, SessionStore

    def factory(
        project_path: Path,
        *,
        client_factory=None,
        restored=None,
    ) -> AgentSession:
        return AgentSession(
            project_path=project_path,
            settings_store=settings_store,
            session_store=SessionStore(app_data),
            handoff_store=HandoffStore(app_data),
            client_factory=client_factory or (lambda profile, timeout=120.0: None),
            restored=restored,
        )

    return factory


def git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def init_repo(path: Path) -> Path:
    git(path, "init", "-q")
    git(path, "config", "user.name", "DoctorCode Tests")
    git(path, "config", "user.email", "tests@example.invalid")
    (path / "tracked.txt").write_text("original\n", encoding="utf-8")
    git(path, "add", "tracked.txt")
    git(path, "commit", "-q", "-m", "initial")
    return path


@pytest.fixture
def git_project(project: Path) -> Path:
    return init_repo(project)
