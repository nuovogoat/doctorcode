"""Model-client contract tests (no network calls)."""

from __future__ import annotations

import asyncio

import pytest

from doctorcode.engine.llm_client import CompatibleChatClient, LLMError
from doctorcode.settings import ConnectionProfile


def test_client_rejects_missing_base_url() -> None:
    with pytest.raises(LLMError, match="base URL"):
        CompatibleChatClient(ConnectionProfile(model="some-model"))


def test_client_can_be_constructed_before_a_model_is_chosen() -> None:
    client = CompatibleChatClient(
        ConnectionProfile(base_url="http://127.0.0.1:9/v1"),
        timeout=1.0,
    )

    async def scenario() -> None:
        with pytest.raises(LLMError, match="model is required"):
            await client.complete([], [])
        await client.close()

    asyncio.run(scenario())
