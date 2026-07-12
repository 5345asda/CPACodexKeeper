# xAI Permission-Denied Deletion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Delete only xAI auth files whose CPA list metadata matches the exact xAI chat-endpoint permission-denied signature.

**Architecture:** Keep full maintenance Codex-only. Extend the existing metadata-only error sweep with a fail-closed xAI policy that recognizes the flat `{code, error}` payload, and add a scoped one-shot CLI mode for safe xAI preflight and deletion. Existing generic Codex delete and disable settings remain unchanged.

**Tech Stack:** Python 3.11, curl-cffi, unittest, Docker Compose.

---

### Task 1: Add The Disabled-by-Default xAI Policy Setting

**Files:**
- Modify: `src/settings.py`
- Test: `tests/test_settings.py`
- Modify: `docker-compose.yml`
- Test: `tests/test_docker_compose.py`
- Modify: `.env.example`

**Step 1: Write failing settings and compose tests**

Add assertions that default settings expose
`xai_permission_denied_delete_enabled == False`, an environment value of
`CPA_XAI_PERMISSION_DENIED_DELETE_ENABLED=true` enables it, and Compose passes
the variable with a default of `false`.

**Step 2: Run focused tests to verify they fail**

Run:

```powershell
python -m unittest discover -s tests -p test_settings.py
python -m unittest discover -s tests -p test_docker_compose.py
```

Expected: failures for the missing setting and Compose variable.

**Step 3: Implement the smallest setting surface**

Add `DEFAULT_XAI_PERMISSION_DENIED_DELETE_ENABLED = False`, the Settings
dataclass field, and `_read_bool` loading for
`CPA_XAI_PERMISSION_DENIED_DELETE_ENABLED`. Add the variable to Compose and
the environment template. Do not add mutable provider/code/message lists; the
exact signature is fixed code policy.

**Step 4: Run focused tests to verify they pass**

Run the same command and expect success.

**Step 5: Commit the setting change**

```powershell
git add src/settings.py tests/test_settings.py docker-compose.yml tests/test_docker_compose.py .env.example
git commit -m "feat: add xai error deletion switch"
```

### Task 2: Parse And Match The xAI List Error Safely

**Files:**
- Modify: `src/maintainer.py`
- Test: `tests/test_maintainer.py`

**Step 1: Write failing matcher tests**

Add tests for these list rows:

```python
{
    "type": "xai",
    "status": "error",
    "status_message": (
        '{"code":"permission-denied",'
        '"error":"Access to the chat endpoint is denied."}'
    ),
}
```

The exact xAI row must match only when the new setting is enabled. Add negative
tests for a non-xAI type, a different code, a message without the exact
chat-endpoint phrase, an active status, malformed JSON, and a non-string
top-level `error`.

**Step 2: Run the focused matcher tests to verify they fail**

Run:

```powershell
python -m unittest discover -s tests -p test_maintainer.py
```

Expected: failure because the scalar `error` string is not exposed as a
message and no xAI rule exists.

**Step 3: Implement the fail-closed predicate**

Update `get_list_error_message()` to return a string `message` when present,
or the flat scalar `error` when present. Add an xAI-only predicate that requires
`type == "xai"`, `status == "error"`, exact `permission-denied`, the lowercased
chat-endpoint phrase, and the dedicated enabled setting. Call it before the
legacy Codex deletion predicate. Keep all existing Codex logic unchanged.

**Step 4: Run focused matcher tests to verify they pass**

Run the discovery command, which also covers the existing invalidated-auth
tests. Expected: all pass.

**Step 5: Commit the matcher change**

```powershell
git add src/maintainer.py tests/test_maintainer.py
git commit -m "feat: match xai chat permission errors"
```

### Task 3: Scope The Metadata Sweep And Dry-Run Result

**Files:**
- Modify: `src/maintainer.py`
- Test: `tests/test_maintainer.py`

**Step 1: Write failing sweep tests**

Add tests proving that a scoped xAI sweep:

- scans and deletes only an exact xAI match;
- ignores an exact Codex invalidated match when the supplied scope is xAI;
- records `would_delete=1` and `deleted=0` in dry-run mode without calling the
  CPA DELETE method;
- handles a disabled xAI exact match as a deletion candidate.

**Step 2: Run focused sweep tests to verify they fail**

Run:

```powershell
python -m unittest discover -s tests -p test_maintainer.py
```

Expected: failure because the sweep has no provider scope and skips xAI rows.

**Step 3: Implement the scoped sweep**

Allow `sweep_error_status_once()` to receive an explicit allowed-type set.
The default scope remains the supported metadata providers `codex` and `xai`.
Use the scope before counting or reserving a row. Add `would_delete` to the
result. In dry-run mode increment `would_delete`, do not call the CPA DELETE
client, and do not increment `deleted`. Include provider and error code in
audit logs but never write raw status-message text or credentials.

**Step 4: Run focused sweep tests to verify they pass**

Run the focused sweep tests and expect success.

**Step 5: Commit the scoped sweep change**

```powershell
git add src/maintainer.py tests/test_maintainer.py
git commit -m "feat: scope xai error sweep"
```

### Task 4: Add The xAI-Only One-Shot CLI Mode

**Files:**
- Modify: `src/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write failing CLI tests**

Add tests that `--xai-error-sweep-once` calls
`sweep_error_status_once(allowed_types={"xai"})`, does not call `run()` or
`run_forever()`, and can be combined with `--dry-run`.

**Step 2: Run focused CLI tests to verify they fail**

Run:

```powershell
python -m unittest discover -s tests -p test_cli.py
```

Expected: argparse rejects the new flag.

**Step 3: Implement the narrow CLI branch**

Add `--xai-error-sweep-once`. Handle it before daemon or full-run dispatch,
calling only the provider-scoped metadata sweep. Preserve current `--once`
behavior exactly.

**Step 4: Run focused CLI tests to verify they pass**

Run the same command and expect success.

**Step 5: Commit the CLI change**

```powershell
git add src/cli.py tests/test_cli.py
git commit -m "feat: add xai error sweep command"
```

### Task 5: Document, Verify, And Prepare The Live Rollout

**Files:**
- Modify: `README.md`
- Modify: `README.en.md`

**Step 1: Add documentation tests or assertions where appropriate**

Extend existing Compose tests for the new environment variable. Keep the
documentation changes explanatory; no secret values belong in documentation.

**Step 2: Update the operator documentation**

Document the strict xAI conditions, disabled-by-default switch, scoped dry-run
command, scoped actual deletion command, and the fact that full `--once`
remains Codex-only.

**Step 3: Run local verification**

Run:

```powershell
python -m unittest discover -s tests
docker compose config
docker build -t cpacodexkeeper:xai-permission-denied .
git diff --check
```

Expected: all commands exit `0` and no secret values appear in output.

**Step 4: Commit the documentation and verification-ready change**

```powershell
git add README.md README.en.md
git commit -m "docs: document xai permission cleanup"
```

### Task 6: Deploy And Execute The Scoped Cleanup

**Files:**
- Runtime only; do not persist credentials or generated artifacts in the repo.

**Step 1: Recheck live target and deployment baseline**

Confirm the correct remote compose directory, running image, current service
state, and that the persistent xAI setting is disabled. Do not reuse historical
container IDs or counts.

**Step 2: Deploy the tested code with the persistent setting disabled**

Build or transfer the verified application using the established service
deployment path, recreate only `cpacodexkeeper`, and verify it is running the
new code.

**Step 3: Run a process-scoped dry run**

Invoke the xAI-only one-shot mode with a process-local enabled setting and
`--dry-run`. Capture aggregate `scanned`, `delete_matched`, `would_delete`,
and failures without reporting tokens or credential contents.

**Step 4: Perform the xAI-only deletion**

If the dry-run candidates match the requested signature, invoke the same
scoped mode without `--dry-run`, still with a process-local enabled setting.
Report actual `deleted` rather than simulated candidates.

**Step 5: Verify completion**

Re-query the CPA list through the scoped dry-run or a sanitized list summary.
Verify that exact xAI `permission-denied` chat-endpoint matches are zero, the
daemon remains healthy, and no unrelated provider was processed.
