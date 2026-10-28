"""Async OpenAI-compatible model client, deliberately model-agnostic.

Only the protocol is fixed.  Base URL and model come from the UI; the module
never contains a provider host, provider name, or model identifier.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAIError

from doctorcode.settings import ConnectionProfile


class LLMError(RuntimeError):
    """A network/model failure safe to show to the user."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ToolCall:
    """One function call requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""


@dataclass(frozen=True)
class ChatTurn:
    """Normalized model response plus the untouched raw payload."""

    content: str
    tool_calls: list[ToolCall]
    raw_payload: str
    finish_reason: str | None = None


class ChatClient(Protocol):
    """Minimal contract the ReAct loop needs; tests provide a fake."""

    async def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> ChatTurn: ...

    async def list_models(self) -> list[str]: ...

    async def close(self) -> None: ...


class CompatibleChatClient:
    """Thin, inspectable adapter over an OpenAI-compatible ``/v1`` endpoint."""

    def __init__(self, profile: ConnectionProfile, *, timeout: float = 120.0) -> None:
        if not profile.base_url:
            raise LLMError("SETTINGS_MISSING_BASE_URL", "A base URL is required.")
        self.profile = profile
        self._client = AsyncOpenAI(
            base_url=profile.base_url,
            api_key=profile.api_key or "not-required",
            timeout=timeout,
            max_retries=0,
        )

    async def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> ChatTurn:
        if not self.profile.model:
            raise LLMError("SETTINGS_MISSING_MODEL", "A model is required.")
        kwargs: dict[str, Any] = {
            "model": self.profile.model,
            "messages": list(messages),
            "temperature": self.profile.temperature,
        }
        if tools:
            kwargs["tools"] = list(tools)
            kwargs["tool_choice"] = "auto"
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except APITimeoutError as exc:
            raise LLMError("LLM_TIMEOUT", "The model endpoint timed out.", retryable=True) from exc
        except APIConnectionError as exc:
            raise LLMError(
                "LLM_CONNECTION",
                "Could not connect to the model endpoint.",
                retryable=True,
            ) from exc
        except APIStatusError as exc:
            status = getattr(exc, "status_code", "?")
            body = str(getattr(exc, "message", "") or "")[:1200]
            raise LLMError(
                "LLM_HTTP",
                f"The model endpoint returned HTTP {status}: {body}",
                retryable=status in (408, 409, 429) or status >= 500,
            ) from exc
        except OpenAIError as exc:
            raise LLMError("LLM_ERROR", str(exc)[:1200]) from exc

        choice = response.choices[0] if response.choices else None
        if choice is None:
            return ChatTurn(content="", tool_calls=[], raw_payload=response.model_dump_json())
        message = choice.message
        calls: list[ToolCall] = []
        for raw_call in message.tool_calls or []:
            function = raw_call.function
            raw_arguments = function.arguments or "{}"
            try:
                parsed = json.loads(raw_arguments)
                arguments = parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                arguments = {}
            calls.append(
                ToolCall(
                    id=raw_call.id,
                    name=function.name,
                    arguments=arguments,
                    raw_arguments=raw_arguments,
                )
            )
        return ChatTurn(
            content=message.content or "",
            tool_calls=calls,
            raw_payload=response.model_dump_json(),
            finish_reason=choice.finish_reason,
        )

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.models.list()
        except APITimeoutError as exc:
            raise LLMError("MODELS_TIMEOUT", "The model list request timed out.") from exc
        except APIConnectionError as exc:
            raise LLMError(
                "MODELS_CONNECTION", "Could not reach the endpoint for /models."
            ) from exc
        except APIStatusError as exc:
            raise LLMError(
                "MODELS_HTTP",
                f"The endpoint returned HTTP {exc.status_code} for /models.",
            ) from exc
        except OpenAIError as exc:
            raise LLMError("MODELS_ERROR", str(exc)[:1000]) from exc
        models = sorted({item.id for item in response.data if getattr(item, "id", None)})
        return models

    async def close(self) -> None:
        await self._client.close()


class ChatClientFactory(Protocol):
    """Dependency-injection seam for tests and future transports."""

    def __call__(self, profile: ConnectionProfile, *, timeout: float = 120.0) -> ChatClient: ...


def create_client(
    profile: ConnectionProfile, *, timeout: float = 120.0
) -> ChatClient:
    """Create the default protocol client (no provider baked in)."""
    return CompatibleChatClient(profile, timeout=timeout)
