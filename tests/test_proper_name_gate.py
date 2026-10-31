"""Tests for the proper-name gate (v2.9 §0.10 / §8.2).

The gate itself lives in ``tools/check_gates.py::check_proper_name`` and runs
in CI as part of the gate suite. These unit tests verify that the gate
correctly accepts canonical spellings and rejects typos — so a future
refactor of the gate logic cannot silently stop catching misspellings.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from check_gates import check_proper_name  # noqa: E402


@pytest.fixture()
def scratch_repo(tmp_path: Path) -> Path:
    """Minimal repo skeleton so the gate has something to scan."""
    (tmp_path / "doctorcode").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    return tmp_path


def _write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_canonical_product_name_passes(scratch_repo: Path) -> None:
    _write(scratch_repo, "doctorcode/app.py", 'APP_NAME = "DoctorCode"')
    gate = check_proper_name(scratch_repo)
    assert gate.ok, gate.failures


def test_canonical_package_name_passes(scratch_repo: Path) -> None:
    _write(scratch_repo, "doctorcode/__init__.py", "# doctorcode package")
    gate = check_proper_name(scratch_repo)
    assert gate.ok, gate.failures


def test_dependency_name_is_not_flagged(scratch_repo: Path) -> None:
    _write(
        scratch_repo,
        "pyproject.toml",
        'deps = ["doctor-code-ai-connect @ git+...doctor_code_ai_connect..."]',
    )
    gate = check_proper_name(scratch_repo)
    assert gate.ok, gate.failures


def test_product_name_typo_is_caught(scratch_repo: Path) -> None:
    _write(scratch_repo, "doctorcode/app.py", 'APP_NAME = "DoctroCode"')
    gate = check_proper_name(scratch_repo)
    assert not gate.ok
    assert any("product-name variant" in f for f in gate.failures)
    assert any("DoctroCode" in f for f in gate.failures)


def test_package_name_with_separator_is_allowed(scratch_repo: Path) -> None:
    """doctor_code / doctor-code with correct letters are legitimate forms
    (distribution name, Android project path, dependency names) — not typos.
    """
    _write(scratch_repo, "doctorcode/cli.py", "# use doctor_code helper")
    _write(scratch_repo, "pyproject.toml", 'deps = ["doctor-code-ai-connect"]')
    gate = check_proper_name(scratch_repo)
    assert gate.ok, gate.failures


def test_real_repo_is_clean() -> None:
    """The actual doctor_code repository must pass the proper-name gate."""
    gate = check_proper_name(ROOT)
    assert gate.ok, (
        "Proper-name gate found typos in the real repo:\n"
        + "\n".join(f"  - {f}" for f in gate.failures)
    )
