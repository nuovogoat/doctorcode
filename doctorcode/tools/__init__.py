"""Tool registry exposed to the model."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from doctorcode.tools.base import ToolArgs, ToolContext, ToolError, ToolOutcome, ToolSpec
from doctorcode.tools.filesystem import filesystem_tools
from doctorcode.tools.process import process_tools


class ToolRegistry:
    """Strict name/argument dispatch.  Unknown tools are errors, not guesses."""

    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        listed = specs if specs is not None else [*filesystem_tools(), *process_tools()]
        self._specs = {spec.name: spec for spec in listed}
        if len(self._specs) != len(listed):
            raise ValueError("duplicate tool name")

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def openai_tools(self) -> list[dict[str, Any]]:
        return [spec.openai_schema() for spec in self._specs.values()]

    def spec(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise ToolError(f"Unknown tool: {name}") from exc

    def execute(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        spec = self.spec(name)
        try:
            validated: ToolArgs = spec.args_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolError(
                f"Invalid arguments for {name}: {exc.errors(include_url=False)}"
            ) from exc
        return spec.handler(validated, ctx)
