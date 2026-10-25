"""Pairing and LAN token tests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from doctorcode.server.security import (
    LOCKOUT_SECONDS,
    MAX_FAILED_ATTEMPTS,
    PairingManager,
)


def wrong_code(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def test_pairing_issues_a_working_bearer_token(tmp_path: Path) -> None:
    pairing = PairingManager(require_pairing=True, token_file=tmp_path / "tokens.json")

    assert re.fullmatch(r"\d{6}", pairing.code)
    assert pairing.pair(wrong_code(pairing.code), "Phone").ok is False
    result = pairing.pair(pairing.code, "Pixel")

    assert result.ok is True
    assert result.token
    assert pairing.authorize(result.token) is True
    assert pairing.authorize("wrong-token") is False
    assert pairing.authorize(None) is False
    assert pairing.devices()[0]["device_name"] == "Pixel"
    assert "hash" not in pairing.devices()[0]


def test_pairing_locks_after_repeated_failures(tmp_path: Path) -> None:
    pairing = PairingManager(require_pairing=True, token_file=None)

    for _ in range(MAX_FAILED_ATTEMPTS):
        assert pairing.pair(wrong_code(pairing.code)).ok is False
    blocked = pairing.pair(pairing.code, "Pixel")

    assert blocked.ok is False
    assert blocked.failure == "locked"
    assert 0 < blocked.retry_after_seconds <= LOCKOUT_SECONDS
    assert pairing.blocked is True


def test_tokens_survive_restart_and_can_be_revoked(tmp_path: Path) -> None:
    token_file = tmp_path / "tokens.json"
    first = PairingManager(require_pairing=True, token_file=token_file)
    result = first.pair(first.code, "Pixel")
    assert result.token

    second = PairingManager(require_pairing=True, token_file=token_file)
    assert second.code != ""
    assert second.authorize(result.token) is True
    second.revoke_all()
    assert second.authorize(result.token) is False
    third = PairingManager(require_pairing=True, token_file=token_file)
    assert third.authorize(result.token) is False


def test_pairing_disabled_allows_unauthenticated_lan_use(tmp_path: Path) -> None:
    pairing = PairingManager(require_pairing=False, token_file=None)

    assert pairing.authorize(None) is True
    assert pairing.authorize("anything") is True
    assert pairing.status()["pairing_required"] is False


def test_rotating_the_code_rejects_the_old_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = iter([123456, 654321])
    monkeypatch.setattr(
        "doctorcode.server.security.secrets.randbelow",
        lambda _limit: next(values),
    )
    pairing = PairingManager(require_pairing=True, token_file=None)
    old_code = pairing.code

    new_code = pairing.rotate_code()

    assert (old_code, new_code) == ("123456", "654321")
    assert pairing.pair(old_code, "Phone").ok is False
    assert pairing.pair(new_code, "Phone").ok is True
