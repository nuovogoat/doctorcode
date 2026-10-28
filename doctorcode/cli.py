"""Headless CLI.  The GUI never loads on this import path."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from doctorcode import __version__
from doctorcode.engine.events import ActionEvent, ActionStatus
from doctorcode.engine.llm_client import LLMError, load_models
from doctorcode.engine.reviewer import ReviewerService
from doctorcode.engine.workspace import WorkspaceManager
from doctorcode.i18n import Translator
from doctorcode.runtime import AppRuntime, create_runtime
from doctorcode.settings import SettingsStore
from doctorcode.tools.base import ApprovalRequest


def _pre_language(argv: Sequence[str]) -> str:
    for index, arg in enumerate(argv):
        if arg == "--lang" and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1]
    return ""


def build_parser(translator: Translator) -> argparse.ArgumentParser:
    t = translator.t
    parser = argparse.ArgumentParser(
        prog="doctorcode",
        description=t("cli.description"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project", type=Path, help=t("cli.project"))
    parser.add_argument("--prompt", help=t("cli.prompt"))
    parser.add_argument("--auto", action="store_true", help=t("cli.auto"))
    parser.add_argument("--json", action="store_true", help=t("cli.json"))
    parser.add_argument("--model", help=t("cli.model"))
    parser.add_argument("--lang", choices=("ru", "en", "de"), help=t("cli.lang"))
    parser.add_argument("--list-models", action="store_true", help=t("cli.list_models"))
    parser.add_argument("--server", action="store_true", help=t("cli.server"))
    parser.add_argument("--host", default="0.0.0.0", help=t("cli.host"))
    parser.add_argument("--port", type=int, default=None, help=t("cli.port"))
    parser.add_argument(
        "--reviewer-profile",
        action="store_true",
        help=t("cli.reviewer"),
    )
    parser.add_argument("--gui", action="store_true", help=t("cli.gui"))
    parser.add_argument("--qt", action="store_true", help=t("cli.qt"))
    parser.add_argument("--settings", action="store_true", help=t("cli.settings"))
    parser.add_argument("--review", type=Path, help=t("cli.review"))
    parser.add_argument("--instructions", default="", help=t("cli.instructions"))
    parser.add_argument("--data-dir", type=Path, help="Override the app data directory.")
    parser.add_argument("--version", action="version", version=f"DoctorCode {__version__}")
    return parser


def _print_event(translator: Translator, event: ActionEvent, *, json_mode: bool) -> None:
    if event.status is ActionStatus.IN_PROGRESS:
        return
    if json_mode:
        print(json.dumps(event.to_payload(), ensure_ascii=False), flush=True)
        return
    print(
        translator.t(
            "cli.event",
            status=event.status.value,
            summary=event.human_summary,
        ),
        flush=True,
    )


def _approval_callback(translator: Translator) -> object:
    def ask(request: ApprovalRequest) -> bool:
        print(
            translator.t("cli.approval_hint", details=json.dumps(request.details, ensure_ascii=False)),
            file=sys.stderr,
            flush=True,
        )
        answer = input(f"{translator.t('cli.approval')} [y/N] ").strip().lower()
        return answer in {"y", "yes", "д", "да", "j", "ja"}

    return ask


async def _list_models(
    translator: Translator, settings: SettingsStore, *, reviewer: bool
) -> int:
    role = "reviewer" if reviewer else "writer"
    profile = settings.settings.profile(role)
    if not profile.base_url:
        print(translator.t("cli.url_missing"), file=sys.stderr)
        return 2
    try:
        models = await load_models(profile, timeout=30.0)
    except LLMError as exc:
        print(translator.t("cli.models_failed", error=exc), file=sys.stderr)
        return 1
    print(translator.t("cli.models", profile=role))
    if not models:
        print(translator.t("cli.models_empty"), file=sys.stderr)
        return 1
    for model in models:
        print(model)
    print(translator.t("cli.models_fetched", count=len(models)), file=sys.stderr)
    return 0


async def _review_file(
    translator: Translator,
    settings: SettingsStore,
    path: Path,
    instructions: str,
) -> int:
    if not path.is_file():
        print(translator.t("cli.project_missing", path=path), file=sys.stderr)
        return 2
    try:
        if path.stat().st_size > settings.settings.agent.max_file_bytes:
            print(translator.t("cli.review_failed", error="file too large"), file=sys.stderr)
            return 1
        material = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(translator.t("cli.review_failed", error=exc), file=sys.stderr)
        return 1
    from doctorcode.engine.feed import ActionFeed

    feed = ActionFeed()
    service = ReviewerService(settings_store=settings, feed=feed)
    print(translator.t("cli.reviewing", file=path.name), file=sys.stderr)
    result = await service.review(material, instructions)
    if result.status is ActionStatus.SUCCESS:
        print(result.text)
        print(translator.t("cli.review_success"), file=sys.stderr)
        return 0
    print(translator.t("cli.review_failed", error=result.error_code), file=sys.stderr)
    return 1


async def _run_prompt(
    translator: Translator,
    runtime: AppRuntime,
    *,
    project: Path,
    prompt: str,
    auto: bool,
    json_mode: bool,
    model: str | None,
) -> int:
    manager = WorkspaceManager(
        app_data_dir=runtime.data_dir,
        settings_store=runtime.settings_store,
        executor=runtime.executor,
        logger=runtime.core.logger,
    )
    try:
        session = manager.open_project(project)
        if model:
            session.model = model
        if auto:
            session.set_auto_mode(True)
        else:
            session.set_approval_callback(_approval_callback(translator))
        session.subscribe(lambda event: _print_event(translator, event, json_mode=json_mode))
        print(
            translator.t("cli.running", project=session.project_name),
            file=sys.stderr,
            flush=True,
        )
        print(translator.t("cli.interrupt"), file=sys.stderr, flush=True)
        result = await session.run_prompt(prompt)
        if result.error_code == "CANCELLED":
            print(translator.t("cli.cancelled"), file=sys.stderr)
            return 130
        if result.status is ActionStatus.SUCCESS:
            if not json_mode:
                print(translator.t("cli.result_success", summary=result.final_text[:400]))
            return 0
        if not json_mode:
            last = session.feed.latest(1)
            summary = last[0].human_summary if last else (result.error_code or "")
            print(translator.t("cli.result_failure", summary=summary), file=sys.stderr)
        return 1
    finally:
        manager.shutdown()


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    translator = Translator(_pre_language(args_list))
    parser = build_parser(translator)
    args = parser.parse_args(args_list)
    if args.lang:
        translator.set_language(args.lang)

    if args.gui or args.qt:
        if args.qt:
            from doctorcode.ui.qt_app import main as qt_main

            return qt_main()
        from doctorcode.ui.tk_app import main as tk_main

        return tk_main()
    if args.data_dir:
        os.environ["DOCTORCODE_DATA_DIR"] = str(args.data_dir.resolve())

    runtime: AppRuntime
    try:
        runtime = create_runtime()
    except Exception as exc:
        print(translator.t("startup.core_failed", error=exc), file=sys.stderr)
        return 2
    try:
        settings = runtime.settings_store
        translator.set_language(settings.settings.ui.language or translator.language)
        if args.settings:
            print(json.dumps(settings.settings.sanitized(), ensure_ascii=False, indent=2))
            return 0
        if args.list_models:
            return asyncio.run(_list_models(translator, settings, reviewer=args.reviewer_profile))
        if args.review:
            return asyncio.run(_review_file(translator, settings, args.review, args.instructions))
        if args.server:
            from doctorcode.server.api import run_server

            project = (args.project or Path.cwd()).expanduser().resolve()
            if not project.is_dir():
                print(translator.t("cli.project_missing", path=project), file=sys.stderr)
                return 2
            return run_server(
                runtime=runtime,
                project=project,
                host=args.host,
                port=args.port,
            )
        if not args.prompt:
            print(translator.t("cli.no_prompt"), file=sys.stderr)
            parser.print_usage(sys.stderr)
            return 2
        project = (args.project or Path.cwd()).expanduser().resolve()
        if not project.is_dir():
            print(translator.t("cli.project_missing", path=project), file=sys.stderr)
            return 2
        try:
            return asyncio.run(
                _run_prompt(
                    translator,
                    runtime,
                    project=project,
                    prompt=args.prompt,
                    auto=args.auto,
                    json_mode=args.json,
                    model=args.model,
                )
            )
        except KeyboardInterrupt:
            print(translator.t("cli.stopped"), file=sys.stderr)
            return 130
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
