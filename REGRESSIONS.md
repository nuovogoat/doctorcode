# Regressions

Every frozen-version bug must be listed here with its incident and a test.
The regression gate (`python tools/check_gates.py`) verifies that each entry has
a linked test file/marker.

| ID | Incident | Test |
|---|---|---|
| R-001 | A successful-looking `human_summary` must never mask a `FAILURE` status. | `tests/test_events.py::test_failure_with_positive_summary_is_flagged` |
| R-002 | Sending STOP twice must not crash and must leave the session runnable. | `tests/test_session.py::test_stop_is_idempotent` |
| R-003 | A second tab must not inherit the first project's history. | `tests/test_workspace.py::test_project_sessions_are_isolated` |
| R-004 | A model switch must preserve history in a handoff snapshot. | `tests/test_session.py::test_model_switch_writes_handoff` |
| R-005 | Imported Kimi snapshots must never contain local settings or API keys. | `tests/test_kimi_import.py::test_snapshot_never_contains_settings_or_api_keys` |
| R-006 | Re-importing the same Kimi session into the same project must not duplicate history. | `tests/test_kimi_import.py::test_import_is_idempotent_for_the_same_project` |
| R-007 | Existing project session files without an `imports` field must still load. | `tests/test_kimi_import.py::test_old_session_files_without_imports_still_load` |
| R-008 | A failed model-catalogue request must stay a typed `LLMError` and must not leak the key. | `tests/test_llm_client.py::test_http_error_maps_status_and_never_leaks_the_key` |
| R-009 | With Russian selected the feed showed raw English statuses, action kinds and summaries, and a missing `feed.action` header key. | `tests/test_ui.py::test_feed_rows_localize_for_selected_language` |
| R-010 | An unconfigured writer failed silently: Settings were only reachable from the menu and no onboarding explained how to connect a model or pick a theme. | `tests/test_ui.py::test_onboarding_offers_settings_when_writer_unconfigured` |
