# DoctorCode — GAP Tracking (TOOLING_STANDARDS v2.9 §12 checklist)

Generated: 2026-10-31 22:10 +0700
Branch/PR: main (no PR — direct commits to main)
Reference: C:\Projects\Kimi_code\TOOLING_STANDARDS_ver2.9.md §12 (check-list приёмки)

**Rule:** Every row must have status, owner, and scope. "To be triaged" is forbidden — either fill the row or park the item in Backlog (parked) with a reason.

## Implemented — closed this session

| ID | Standard ref | Item | Priority | Status | Owner | Scope |
|----|--------------|------|----------|--------|-------|-------|
| GAP-01 | §0.10 / §8.2 | Proper-name gate: identifiers verified by test vs canonical spelling; rename ID → version bump + CHANGELOG | P1 | Done | Agent | `tools/check_gates.py::check_proper_name`, `tests/test_proper_name_gate.py` |
| GAP-02 | §5 | Language switcher = combobox in top-right corner; flags drawn by code (pixel grid, no emoji font); runtime switch without restart | P1 | Done | Agent | `doctorcode/ui/flags.py`, `tk_app.py`, `qt_window.py`, i18n ru/en/de |
| GAP-03 | §5.5 | STANDARDS.md pointer → v2.9 canon | P2 | Done | Agent (standards_sync) | `STANDARDS.md` |

## Open / to implement (from v2.9 §12)

| ID | Standard ref | Item | Priority | Status | Owner | Scope |
|----|--------------|------|----------|--------|-------|-------|
| GAP-04 | §10 | About dialog must show: tool name **by nuovogoat**, tool version, core name **by nuovogoat**, core version; CLI `--version` same attribution | P1 | Open | Agent (next session) | `doctorcode/ui/settings_window.py::AboutWindow`, `qt_window.py` |
| GAP-05 | §10 | Single source of version = `__version__` package attr; `pyproject.toml` via `dynamic = ["version"]` + `attr`; no duplicate version in pyproject | P1 | Open | Agent (next session) | `pyproject.toml`, `doctorcode/__init__.py` |
| GAP-06 | §10 | Version bump accompanies each release phase (feat→minor, fix→patch, breaking→major) | P3 | Open | Agent (later) | `CHANGELOG.md`, release process |
| GAP-07 | §8.3 | Network code covered by tests: failures, garbage, oversized, abort, cleanup in-flight; memory delta via `tracemalloc` snapshots before/after + `gc.collect()` + object count comparison | P2 | Open | Agent (later) | `tests/test_network.py` (or similar) |
| GAP-08 | §13 | README lists allowed network endpoint domains line-by-line | P2 | Open | Agent (later) | `README.md` "Allowed network endpoints" |
| GAP-09 | §12 | Documentation: installation, launch, typical errors | P3 | Open | Agent (later) | `docs/INSTALL.md`, `docs/TROUBLESHOOTING.md` |
| GAP-10 | §8 | Behavioral GUI tests; CLI↔GUI parity | P2 | Open | Agent (later) | `tests/test_ui.py`, `tests/test_qt_ui.py` |
| GAP-11 | §7 | Destructive actions: audit → add confirmation where missing; default "ask" | P2 | Open | Agent (next session) | Audit all destructive actions in `tk_app.py` / `qt_window.py` |

## Verified in-place (no change needed)

| ID | Standard ref | Item | Status | Evidence |
|----|--------------|------|--------|----------|
| V-01 | §1 | CLI with `--help` and validation | Verified | `doctorcode/cli.py` |
| V-02 | §1 | `<tool>_start.bat` default entry to main window | Verified | `doctorcode_start.bat` |
| V-03 | §1 | CLI `.bat`: `%~dp0`, `%*`, `chcp 65001`, `pause` | Verified | All root `.bat` launchers |
| V-04 | §1 | Headless work without GUI | Verified | `doctorcode/__init__.py` keeps GUI off import path |
| V-05 | §2 | GUI Tkinter default; optional `*_qt.py` | Verified | `tk_app.py` default, `qt_window.py` optional |
| V-06 | §6.7 | Modules menu first level; one window; statuses from metadata; verbs | Verified | `modules_window.py` |
| V-07 | §5 | ru/en/de packs; key parity test; flag switcher | Verified | i18n packs + `tests/test_i18n.py` |
| V-08 | §5 | Language detection failure → ru | Verified | `i18n.resolve_language()` |
| V-09 | §3 | Unkillable poll loop; worker via queue; cancellation idempotent | Verified | `engine/workspace.py`, `runner.py` |
| V-10 | §3 | Operations >0.5s in thread with progress + cancellation | Verified | Worker pattern |
| V-11 | §4 | Source of truth = metadata; refresh after op; named results | Verified | Events/actions model |
| V-12 | §6.1 | Lists: "Select all" + bulk actions | Verified | `project_tab.py`, `EventFeedView` |
| V-13 | §6.2 | Menu first level = only window openers; no leaf items | Verified | Menu structure |
| V-14 | §6 | Settings = single window; "Hide console"; cleanup deletes folders; dialogs separated | Verified | `settings_window.py` |
| V-15 | §8 | Tests on real code; gates green: i18n, BOM, manifest, commit-msg, clean, regressions | Verified | CI green run 41211123381 |
| V-16 | §10 | `REGRESSIONS.md` maintained; items covered by tests | Verified | `check_regressions()` gate passes |
| V-17 | §8 | Narrow `git add` by list; `git status` before commit | Verified | Process rule |
| V-18 | §5.4 | Checkpoint commits: completed phases pushed, upstream exists | Verified | All sessions pushed |
| V-19 | §0.8 | Tool depends on core_pack (pinned by git tag); run from any session | Verified | `pyproject.toml`, CI |
| V-20 | §0.9 | External data: unrecognized → empty; invalid input rejected not truncated; values not invented | Verified | Engine validation |
| V-21 | §4.6 | Cache and backoff reset on input change | Verified | `engine/backoff.py` |
| V-22 | §13 | Secrets to hardcoded origins only; user-address requests unauthenticated + timeout + size limit + no redirect | Verified | `NetworkPolicy`, `MobileServerController` |
| V-23 | §3.1 | All .bat launchers in root folder | Verified | `*.bat` at repo root |

## Backlog (parked)

| ID | Reason | Standard ref |
|----|--------|--------------|
| — | (none parked yet) | — |

## Self-check (pre-close)

- [ ] Report date present (with time + offset)
- [ ] Branch/PR link present (if applicable)
- [ ] Commit history explained (why multiple fix commits after green CI)
- [ ] All GAP rows have status/owner/scope (no "to be triaged")
- [ ] CI run ID referenced for latest green run
- [ ] All Verified rows have V-XX IDs
- [ ] GAP-07 scope uses Python-native tooling (`tracemalloc`, `gc.collect`)
