"""Optional-module metadata and pip command construction (GUI-independent)."""

from __future__ import annotations

import importlib.metadata
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

PackageAction = Literal["install", "update", "remove"]


@dataclass(frozen=True)
class ModuleSpec:
    key: str
    label_key: str
    distribution: str
    extra: str
    # Explicit pip requirement for distributions that are not on PyPI (the
    # private git dependency); empty means "install by distribution name".
    target: str = ""


@dataclass(frozen=True)
class ModuleRow:
    spec: ModuleSpec
    installed: bool
    version: str

    @property
    def status_key(self) -> str:
        return "modules.installed" if self.installed else "modules.not_installed"


MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec(
        key="qt",
        label_key="modules.pyside",
        distribution="PySide6",
        extra="qt",
    ),
    ModuleSpec(
        key="dnd",
        label_key="modules.dnd",
        distribution="tkinterdnd2",
        extra="dnd",
    ),
    ModuleSpec(
        key="connect",
        label_key="modules.connect",
        distribution="open-code-ai-connect",
        extra="connect",
        target=(
            "open-code-ai-connect @ "
            "git+https://github.com/nuovogoat/open_code_ai_connect.git@v2.0.0"
        ),
    ),
)

VersionLookup = Callable[[str], str]


def installed_version(distribution: str, lookup: VersionLookup | None = None) -> str | None:
    """Read status from installed distribution metadata, never by importing Qt."""
    lookup = lookup or importlib.metadata.version
    try:
        return str(lookup(distribution))
    except importlib.metadata.PackageNotFoundError:
        return None


def module_rows(lookup: VersionLookup | None = None) -> list[ModuleRow]:
    rows: list[ModuleRow] = []
    for spec in MODULES:
        version = installed_version(spec.distribution, lookup)
        rows.append(
            ModuleRow(
                spec=spec,
                installed=version is not None,
                version=version or "",
            )
        )
    return rows


def find_module(key: str) -> ModuleSpec:
    for spec in MODULES:
        if spec.key == key or spec.distribution.casefold() == key.casefold():
            return spec
    raise KeyError(key)


def pip_argv(
    action: PackageAction,
    distribution: str,
    *,
    python: str | None = None,
    target: str = "",
) -> list[str]:
    python = python or sys.executable
    base = [python, "-m", "pip", "--disable-pip-version-check"]
    if action == "remove":
        return [*base, "uninstall", "--yes", distribution]
    requirement = target or distribution
    if action == "update":
        return [*base, "install", "--upgrade", requirement]
    return [*base, "install", requirement]


def pip_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("pip") is not None
