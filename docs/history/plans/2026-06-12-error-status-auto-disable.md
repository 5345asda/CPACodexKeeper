# Error Status Auto Disable Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a lightweight one-minute CPA list metadata sweep that disables configured quota errors such as `usage_limit_reached` and deletes configured invalid-auth errors that match type, code, and message-keyword rules.

**Architecture:** Keep the existing full maintenance loop as the detailed authority for usage checks, refresh, and re-enable decisions. Add a narrow metadata-only sweep path that parses `status_message` from `/v0/management/auth-files` list results, deletes configured hard invalid-auth errors only when type, code, and message keyword all match, and disables currently enabled matching quota errors. Run the sweep in a daemon background thread, but serialize it with the full maintenance loop so both scheduled tasks do not mutate the same stale list at once.

**Tech Stack:** Python 3.11+, `curl_cffi`, `unittest`, existing CPACodexKeeper service classes.

---

### Task 1: Error Metadata Parsing

**Files:**
- Modify: `src/maintainer.py`
- Test: `tests/test_maintainer.py`

**Step 1: Write failing tests**

Add tests for extracting configured error types from list metadata:

```python
def test_parse_error_type_from_status_message_json(self):
    token = {
        "status": "error",
        "status_message": '{"error":{"type":"usage_limit_reached","message":"The usage limit has been reached"}}',
    }
    self.assertEqual(self.maintainer.get_list_error_type(token), "usage_limit_reached")

def test_parse_error_type_returns_none_for_bad_message(self):
    token = {"status": "error", "status_message": "not json"}
    self.assertIsNone(self.maintainer.get_list_error_type(token))
```

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_maintainer.MaintainerTests.test_parse_error_type_from_status_message_json tests.test_maintainer.MaintainerTests.test_parse_error_type_returns_none_for_bad_message
```

Expected: fails because `get_list_error_type` does not exist.

**Step 3: Implement minimal parsing**

Add a method that accepts a list token dict, parses `status_message` if it is JSON, handles nested `error.type`, and returns `None` on malformed data.

**Step 4: Run test to verify it passes**

Run the same focused unittest command.

### Task 2: Sweep Policy

**Files:**
- Modify: `src/settings.py`
- Modify: `src/maintainer.py`
- Test: `tests/test_settings.py`
- Test: `tests/test_maintainer.py`

**Step 1: Write failing tests**

Add tests that:

- `load_settings` reads default sweep settings.
- The sweep disables only `type=codex`, `status=error`, `disabled != true`, and configured error type matches.
- The sweep skips already disabled files and non-configured errors.

**Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_settings tests.test_maintainer.MaintainerTests.test_sweep_disables_usage_limit_error_tokens tests.test_maintainer.MaintainerTests.test_sweep_skips_disabled_or_unconfigured_error_tokens
```

Expected: fails because settings and sweep method do not exist.

**Step 3: Implement minimal settings and sweep**

Add settings:

- `error_sweep_enabled: bool`
- `error_sweep_interval_seconds: int`
- `error_disable_types: frozenset[str]`

Add `sweep_error_status_once()` on `CPACodexKeeper`. It should list auth files, filter candidates, call `set_disabled_status(name, disabled=True)`, and return counters.

**Step 4: Run test to verify it passes**

Run the same focused unittest command.

### Task 3: Daemon Integration Without Task Conflict

**Files:**
- Modify: `src/maintainer.py`
- Modify: `README.md`
- Modify: `README.en.md`
- Test: `tests/test_maintainer.py`

**Step 1: Write failing tests**

Add a test that `run_forever()` starts the sweep thread only when enabled, and does not start duplicate sweep threads across the same maintainer instance.

**Step 2: Run test to verify it fails**

Run the focused unittest for the daemon integration test.

**Step 3: Implement minimal daemon integration**

Add a daemon thread that runs:

```python
while not stop_event.wait(error_sweep_interval_seconds):
    sweep_error_status_once()
```

Guard startup with a lock and thread liveness check. Keep `run()` and `--once` unchanged.

**Step 4: Run test to verify it passes**

Run the focused unittest for daemon integration.

### Task 4: Full Verification

**Files:**
- All modified source, tests, and docs.

**Step 1: Run all tests**

Run:

```bash
python -m unittest discover -s tests
```

Expected: all tests pass.

**Step 2: Review diff**

Run:

```bash
git diff -- src tests README.md README.en.md docs/plans/2026-06-12-error-status-auto-disable-design.md docs/plans/2026-06-12-error-status-auto-disable.md
```

Expected: only intended files changed, no secrets printed or committed.
