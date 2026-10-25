"""Application runtime: a single core_pack context and executor per process."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core_pack import (
    Core,
    TaskExecutor,
    create_core,
    ensure_private_dir,
    get_platform_data_dir,
)

from doctorcode.settings import SettingsStore

_APP_NAME = "DoctorCode"


def app_data_dir() -> Path:
    """Return the private per-user data root (env override for tests/portable)."""
    override = os.environ.get("DOCTORCODE_DATA_DIR")
    path = Path(override).expanduser() if override else Path(get_platform_data_dir(_APP_NAME))
    return path.resolve()


@dataclass
class AppRuntime:
    """Holds core, settings, and the only UI/background thread pool."""

    core: Core
    settings_store: SettingsStore
    executor: TaskExecutor
    data_dir: Path
    _closed: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self.executor.shutdown(wait=False, cancel_futures=True)
        finally:
            self.core.shutdown()

    def __enter__(self) -> AppRuntime:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def create_runtime(data_dir: Path | None = None) -> AppRuntime:
    """Initialize core_pack with private paths and human-readable logging."""
    base = (data_dir or app_data_dir()).resolve()
    ensure_private_dir(str(base))
    log_dir = base / "log"
    ensure_private_dir(str(log_dir))
    config: dict[str, Any] = {
        "base_dir": str(base),
        "logging": {
            "level": os.environ.get("DOCTORCODE_LOG_LEVEL", "INFO"),
            "log_dir": str(log_dir),
            "log_filename": "doctorcode.log",
            "max_size": 5_000_000,
            "backup_count": 3,
        },
        "paths": {
            "config_dir": str(base / "config"),
            "data_dir": str(base / "data"),
            "cache_dir": str(base / "cache"),
            "log_dir": str(log_dir),
        },
    }
    core = create_core(
        config=config,
        base_dir=str(base),
        install_signal_handlers=False,
        interactive=False,
    )
    settings_path = Path(core.paths.get_path("config")) / "settings.json"
    settings_store = SettingsStore(settings_path, logger=core.logger)
    executor = TaskExecutor(max_workers=6, name_prefix="doctorcode")
    core.logger.info(
        "app.started",
        context={"data_dir": str(base), "settings": str(settings_path)},
    )
    return AppRuntime(
        core=core,
        settings_store=settings_store,
        executor=executor,
        data_dir=base,
    )
