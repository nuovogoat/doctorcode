"""Pairing codes, bearer tokens, and fail-closed LAN authentication.

The mobile server is loopback-adjacent, not public: pairing is enabled by
default, codes are six digits, failed attempts are rate limited, and only token
hashes are persisted.  API keys from model profiles never enter this module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from core_pack import atomic_write, ensure_private_dir

PairFailure = Literal["bad_code", "locked"]
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60.0
ATTEMPT_WINDOW_SECONDS = 300.0


@dataclass(frozen=True)
class PairedDevice:
    """Public metadata about a trusted phone (never the raw token)."""

    device_name: str
    created_at: str

    def payload(self) -> dict[str, str]:
        return {"device_name": self.device_name, "created_at": self.created_at}


@dataclass
class PairResult:
    token: str | None = None
    failure: PairFailure | None = None
    retry_after_seconds: float = 0.0
    new_code: str = ""

    @property
    def ok(self) -> bool:
        return self.token is not None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PairingManager:
    """Six-digit pairing with persisted bearer-token hashes."""

    def __init__(
        self,
        *,
        require_pairing: bool = True,
        token_file: Path | None = None,
        max_devices: int = 8,
        logger: logging.Logger | None = None,
    ) -> None:
        self.require_pairing = bool(require_pairing)
        self.token_file = Path(token_file) if token_file else None
        self.max_devices = max_devices
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.RLock()
        self._code = self._new_code()
        self._hashes: dict[str, PairedDevice] = {}
        self._failures: deque[float] = deque()
        self._blocked_until = 0.0
        self._load()

    @property
    def code(self) -> str:
        with self._lock:
            return self._code

    @property
    def blocked(self) -> bool:
        with self._lock:
            return time.monotonic() < self._blocked_until

    def rotate_code(self) -> str:
        """Show a fresh code without invalidating already paired devices."""
        with self._lock:
            self._code = self._new_code()
            return self._code

    def pair(self, code: str, device_name: str = "Android") -> PairResult:
        """Validate the code and issue one bearer token.

        Codes are compared in constant time.  Repeated failures lock pairing for
        a minute; a successful pairing clears the failure window.
        """
        now = time.monotonic()
        device_name = " ".join(device_name.split())[:80] or "Android"
        with self._lock:
            if now < self._blocked_until:
                return PairResult(
                    failure="locked",
                    retry_after_seconds=round(self._blocked_until - now, 1),
                )
            while self._failures and now - self._failures[0] > ATTEMPT_WINDOW_SECONDS:
                self._failures.popleft()
            if not hmac.compare_digest(code.strip(), self._code):
                self._failures.append(now)
                if len(self._failures) >= MAX_FAILED_ATTEMPTS:
                    self._blocked_until = now + LOCKOUT_SECONDS
                return PairResult(failure="bad_code")
            self._failures.clear()
            if len(self._hashes) >= self.max_devices:
                self._hashes.pop(next(iter(self._hashes)))
            token = secrets.token_urlsafe(32)
            self._hashes[_hash_token(token)] = PairedDevice(
                device_name=device_name,
                created_at=datetime.now(UTC).isoformat(),
            )
            self._save_locked()
            return PairResult(token=token)

    def authorize(self, token: str | None) -> bool:
        """Fail closed when pairing is required; open only when disabled."""
        if not self.require_pairing:
            return True
        if not token:
            return False
        with self._lock:
            return _hash_token(token) in self._hashes

    def devices(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                device.payload()
                for device in sorted(self._hashes.values(), key=lambda item: item.created_at)
            ]

    def revoke_all(self) -> None:
        with self._lock:
            self._hashes.clear()
            self._save_locked()

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "pairing_required": self.require_pairing,
                "paired_devices": len(self._hashes),
                "locked": time.monotonic() < self._blocked_until,
            }

    def _new_code(self) -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    def _load(self) -> None:
        if self.token_file is None or not self.token_file.exists():
            return
        try:
            raw = json.loads(self.token_file.read_text(encoding="utf-8"))
            for item in raw.get("tokens", []):
                token_hash = str(item.get("hash", ""))
                if len(token_hash) != 64:
                    continue
                self._hashes[token_hash] = PairedDevice(
                    device_name=str(item.get("device_name", "Android"))[:80],
                    created_at=str(item.get("created_at", "")),
                )
        except (OSError, ValueError, TypeError) as exc:
            self.logger.warning("server.pairing_store_unreadable: %s", exc)
            self._hashes.clear()

    def _save_locked(self) -> None:
        if self.token_file is None:
            return
        ensure_private_dir(str(self.token_file.parent))
        payload = json.dumps(
            {
                "schema_version": 1,
                "tokens": [
                    {
                        "hash": token_hash,
                        "device_name": device.device_name,
                        "created_at": device.created_at,
                    }
                    for token_hash, device in self._hashes.items()
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        try:
            atomic_write(str(self.token_file), payload)
        except OSError as exc:
            self.logger.error("server.pairing_store_write_failed: %s", exc)
