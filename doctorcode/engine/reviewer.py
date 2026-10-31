"""Read-only asynchronous reviewer.

The reviewer has no tool registry and no write path.  It can be pointed at a
different endpoint/model profile from the writer, which is the entire point of
the side-by-side review window.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from doctorcode.engine.events import ActionEvent, ActionStatus, ActionType
from doctorcode.engine.feed import ActionFeed
from doctorcode.engine.llm_client import ChatClientFactory, LLMError, create_client
from doctorcode.settings import ConfigError, SettingsStore


@dataclass(frozen=True)
class ReviewResult:
    status: ActionStatus
    text: str
    raw_payload: str
    model: str
    error_code: str | None = None


_SYSTEM = """\
You are DoctorCode Reviewer, an independent code reviewer.
You receive a code/event excerpt and optional instructions. Analyse only.
You cannot edit files and must not pretend to run commands. Be concrete:
identify risks, likely root causes, and verification steps. Never invent test
results. If the excerpt is insufficient, say exactly what is missing.
"""


class ReviewerService:
    """Execute one review in its own feed, without touching the workspace."""

    def __init__(
        self,
        *,
        settings_store: SettingsStore,
        feed: ActionFeed,
        client_factory: ChatClientFactory = create_client,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings_store = settings_store
        self.feed = feed
        self.client_factory = client_factory
        self.logger = logger or logging.getLogger(__name__)
        self.session_id = "reviewer"

    async def review(self, material: str, instructions: str = "") -> ReviewResult:
        profile = self.settings_store.settings.profile("reviewer")
        if not profile.base_url or not profile.model:
            error = ConfigError("REVIEWER_NOT_CONFIGURED")
            self._emit(
                ActionStatus.FAILURE,
                "Reviewer endpoint is not configured. Open Settings -> Reviewer.",
                error_code=str(error),
                summary_key="review.not_configured",
            )
            return ReviewResult(ActionStatus.FAILURE, "", "", "", str(error))
        prompt = (
            f"Review instructions:\n{instructions.strip() or 'Find correctness risks.'}\n\n"
            f"Material:\n{material}"
        )
        self._emit(
            ActionStatus.IN_PROGRESS,
            "Reviewer is analysing the material…",
            summary_key="review.running",
        )
        client = None
        try:
            client = self.client_factory(
                profile,
                timeout=self.settings_store.settings.agent.request_timeout_seconds,
            )
            turn = await client.complete(
                [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                [],
            )
        except LLMError as exc:
            self._emit(
                ActionStatus.FAILURE,
                f"Review failed: {exc}",
                raw=turn.raw_payload if "turn" in locals() else str(exc),
                error_code=exc.code,
                summary_key="review.failed",
                summary_params={"error": str(exc)},
            )
            return ReviewResult(ActionStatus.FAILURE, "", "", profile.model, exc.code)
        except Exception as exc:
            self.logger.exception("reviewer.failed")
            self._emit(
                ActionStatus.FAILURE,
                f"Reviewer error: {type(exc).__name__}",
                raw=repr(exc),
                error_code="REVIEWER_ERROR",
                summary_key="review.error",
                summary_params={"error_type": type(exc).__name__},
            )
            return ReviewResult(ActionStatus.FAILURE, "", "", profile.model, "REVIEWER_ERROR")
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    self.logger.exception("reviewer.close_failed")
        text = turn.content.strip()
        self._emit(
            ActionStatus.SUCCESS,
            f"Reviewer analysis ready ({len(text)} characters).",
            raw=turn.raw_payload,
            metadata={"model": profile.model},
            summary_key="review.ready",
            summary_params={"chars": len(text)},
        )
        return ReviewResult(ActionStatus.SUCCESS, text, turn.raw_payload, profile.model)

    def _emit(
        self,
        status: ActionStatus,
        summary: str,
        *,
        raw: str = "",
        metadata: dict[str, object] | None = None,
        error_code: str | None = None,
        summary_key: str = "",
        summary_params: dict[str, object] | None = None,
    ) -> None:
        details = dict(metadata or {})
        if error_code:
            details["error_code"] = error_code
        self.feed.append(
            ActionEvent(
                session_id=self.session_id,
                action_type=ActionType.REVIEW,
                status=status,
                human_summary=summary,
                raw_payload=raw,
                metadata=details,
                summary_key=summary_key,
                summary_params=summary_params or {},
            )
        )
