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
