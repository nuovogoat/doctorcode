"""Application settings and their fail-safe persisted store.

The settings file is the *only* place where connection credentials live.  It is
created with private-directory permissions and never leaves the desktop host.
Model names, base URLs, and API keys are user data: there are no defaults for
them anywhere in the code base.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from core_pack import atomic_write, ensure_private_dir
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

LANGUAGES = ("ru", "en", "de")
DEFAULT_LANGUAGE = "ru"
PROFILE_ROLES = ("writer", "reviewer")
ProfileRole = Literal["writer", "reviewer"]


class ConfigError(RuntimeError):
    """Raised when the user asks to start an agent without a connection."""


class ConnectionProfile(BaseModel):
    """An OpenAI-compatible endpoint plus the model selected by the user."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)

    @field_validator("base_url", "api_key", "model")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @property
    def is_configured(self) -> bool:
        """True when the endpoint and a concrete model are chosen."""
        return bool(self.base_url and self.model)

    def public_dict(self) -> dict[str, Any]:
        """Serializable data safe for the Android companion (no API key)."""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "configured": self.is_configured,
        }


class ConfirmationSettings(BaseModel):
    """Persistent per-action approval switches (default: always ask)."""

    model_config = ConfigDict(extra="forbid")

    commands: bool = True
    tests: bool = True
    writes: bool = True
    package_actions: bool = True


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_iterations: int = Field(default=20, ge=1, le=100)
    command_timeout_seconds: int = Field(default=180, ge=5, le=3600)
    max_file_bytes: int = Field(default=1_500_000, ge=1024, le=20_000_000)
    system_prompt_extra: str = ""
    request_timeout_seconds: float = Field(default=120.0, ge=5.0, le=900.0)

    @field_validator("system_prompt_extra")
    @classmethod
    def _strip_extra(cls, value: str) -> str:
        return value.strip()


class UISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str = ""
    hide_console: bool = False
    theme: Literal["dark", "light"] = "dark"
    last_project: str = ""

    @field_validator("language")
    @classmethod
    def _known_language(cls, value: str) -> str:
        return value if value in ("", *LANGUAGES) else ""


class ServerSettings(BaseModel):
    """LAN companion settings."""

    model_config = ConfigDict(extra="forbid")

    port: int = Field(default=8765, ge=1024, le=65535)
    require_pairing: bool = True
    advertise_mdns: bool = True
    auto_start: bool = False


class AppSettings(BaseModel):
    """Top-level persisted application settings."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    profiles: dict[str, ConnectionProfile] = Field(
        default_factory=lambda: {
            "writer": ConnectionProfile(),
            "reviewer": ConnectionProfile(),
        }
    )
    confirmations: ConfirmationSettings = Field(default_factory=ConfirmationSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    ui: UISettings = Field(default_factory=UISettings)
    server: ServerSettings = Field(default_factory=ServerSettings)

    @field_validator("profiles")
    @classmethod
    def _ensure_roles(
        cls, value: dict[str, ConnectionProfile]
    ) -> dict[str, ConnectionProfile]:
        for role in PROFILE_ROLES:
            value.setdefault(role, ConnectionProfile())
        return value

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported settings schema version")
        return value

    def profile(self, role: str) -> ConnectionProfile:
        """Return a profile, defaulting unknown roles to the writer slot."""
        return self.profiles.get(role, self.profiles["writer"])

    def sanitized(self) -> dict[str, Any]:
        """Dump settings without API keys (for logs and LAN clients)."""
        data = self.model_dump(mode="json")
        for profile in data.get("profiles", {}).values():
            profile.pop("api_key", None)
        return data


class SettingsStore:
    """Thread-safe JSON settings store with corrupt-file quarantine."""

    def __init__(self, path: Path, *, logger: logging.Logger | None = None) -> None:
        self.path = Path(path)
        self._logger = logger or logging.getLogger(__name__)
        self._lock = threading.RLock()
        self._settings = AppSettings()
        ensure_private_dir(str(self.path.parent))
        self.load()

    @property
    def settings(self) -> AppSettings:
        """Return a deep copy so callers cannot mutate persistence by accident."""
        with self._lock:
            return self._settings.model_copy(deep=True)

    def load(self) -> AppSettings:
        """Load settings; quarantine a broken file and fall back to defaults."""
        with self._lock:
            if not self.path.exists():
                self._settings = AppSettings()
                self.save(self._settings)
                return self.settings
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._settings = AppSettings.model_validate(raw)
            except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                broken = self.path.with_suffix(f".broken-{stamp}.json")
                try:
                    shutil.copy2(self.path, broken)
                except OSError:
                    broken = self.path
                self._logger.error(
                    "settings.load_failed",
                    extra={
                        "context": {
                            "error": str(exc),
                            "quarantined": str(broken),
                        }
                    },
                )
                self._settings = AppSettings()
                self.save(self._settings)
            return self.settings

    def save(self, settings: AppSettings | None = None) -> None:
        """Atomically persist settings and restrict file permissions."""
        with self._lock:
            if settings is not None:
                self._settings = settings.model_copy(deep=True)
            payload = json.dumps(
                self._settings.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            )
            atomic_write(str(self.path), payload)
            # Windows ACLs are managed by the per-user LOCALAPPDATA tree.
            with contextlib.suppress(OSError):
                os.chmod(self.path, 0o600)

    def update(self, mutator: Any) -> AppSettings:
        """Apply a callable to a copy, validate, and save atomically."""
        with self._lock:
            candidate = self._settings.model_copy(deep=True)
            result = mutator(candidate)
            if isinstance(result, AppSettings):
                candidate = result
            self._settings = candidate.model_validate(candidate.model_dump())
            self.save()
            return self.settings

    def set_language(self, language: str) -> AppSettings:
        def mutate(settings: AppSettings) -> None:
            settings.ui.language = language

        return self.update(mutate)

    def set_hide_console(self, hidden: bool) -> AppSettings:
        def mutate(settings: AppSettings) -> None:
            settings.ui.hide_console = hidden

        return self.update(mutate)

    def remember_project(self, path: str) -> AppSettings:
        def mutate(settings: AppSettings) -> None:
            settings.ui.last_project = str(path)

        return self.update(mutate)

    def set_profile(self, role: str, profile: ConnectionProfile) -> AppSettings:
        if role not in PROFILE_ROLES:
            raise ValueError(f"unknown profile role: {role}")

        def mutate(settings: AppSettings) -> None:
            settings.profiles[role] = profile

        return self.update(mutate)

    def ensure_model_configured(self, role: str = "writer") -> ConnectionProfile:
        """Validate a profile before any network request is attempted."""
        profile = self.settings.profile(role)
        if not profile.base_url:
            raise ConfigError("SETTINGS_MISSING_BASE_URL")
        if not profile.model:
            raise ConfigError("SETTINGS_MISSING_MODEL")
        return profile
