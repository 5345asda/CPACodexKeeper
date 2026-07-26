# Provider Fast Scan Refactor Implementation Plan

## Validation Baseline

- `python -m pytest -q` passed with 181 tests before this refactor.
- `python -B -m unittest discover -s tests` is the CI test command.
- `ruff check src/cpa_keeper tests`, `python -m compileall -q src/cpa_keeper tests`,
  `python -m build`, `docker compose config --quiet`, and Docker image build are
  required before completion.

## Execution Order

1. Add failing configuration tests for the supplied TOML, nested provider sections,
   duplicate rule IDs, invalid actions, invalid operators, and the executable
   example config. Run the focused tests and confirm they fail against the current
   loader.
2. Add failing matcher tests for nested and flat error payloads, both Codex rules,
   both xAI rules, case-insensitive contains, disabled rules, priority ordering,
   and raw-message log exclusion. Run them and confirm failure.
3. Replace the v1 configuration and static fast-scan policy path with compact
   `config.py` and `fast_scan.py` modules using pydantic. Run the configuration and
   matcher tests until green.
4. Add failing fast-scan service tests for one list request across Codex and xAI,
   provider switches, disable/delete calls, failure-code logging, and summary
   counts. Implement the generic fast-scan service using the existing CPA client.
   Run the focused service tests until green.
5. Add failing scheduler and CLI tests for one global fast-scan job, provider
   enablement boundaries, and command parsing. Adapt the existing scheduler without
   changing Codex lifecycle behavior. Run those tests until green.
6. Delete the static metadata policy scanners, metadata policy overrides, duplicate
   status parsing, obsolete rule-template documentation, and their tests. Keep the
   Codex inspection catalog, runtime, and lifecycle tests intact.
7. Replace the two conflicting configuration templates with one executable example.
   Rewrite the concise configuration, command, provider, and operations
   documentation around configured fast scanning.
9. Run the full quality gate. Review the diff for secrets, raw error text, unused
   modules, stale commands, and old configuration names.

## Follow-up Correctness Gate

1. Add a failing coordination regression: a successful fast-scan mutation after
   an inspection snapshot prevents that stale inspection from writing; an
   in-flight inspection write and a same-resource fast scan serialize in a
   deterministic order while a different fast scan can continue.
2. Add a failing CLI test for an unexpected exception: it must log a safe
   `event=internal_error` record and return exit code `4` without exposing the
   exception text.
3. Fix the CI installation command so the contract test dependency is explicit.
   Reproduce the CI install and test path in a clean Python 3.12 environment.
4. Implement the narrow coordinator and explicit composition-root injection.
   Keep provider rules, configuration grammar, and lifecycle policy ownership
   unchanged.
5. Run the full local quality gate, clean Python 3.12 reproduction, Docker checks
   when available, and a fresh scope/security/code review before staging.

## Rollback Boundary

The working tree is intentionally dirty and contains the baseline being replaced.
No reset, checkout, or broad rollback command is allowed. If the target needs to
be abandoned, preserve the current diff and restore only through a user-directed
git operation.

## Completed Verification

- The final suite contains 153 tests. Both `python -m pytest -q` and
  `python -B -m unittest discover -s tests` pass.
- `ruff check src/cpa_keeper tests`, `python -m compileall -q src/cpa_keeper
  tests`, `python -m build --no-isolation`, and `git diff --check` pass.
- A clean Python 3.12 virtual environment installs `setuptools>=68`, `build`,
  `ruff`, and the package; `pip check`, lint, tests, compilation, and package
  build pass. Its console script accepts `--version`, `config validate`, and
  `doctor` with `docs/reference/config.example.toml`.
- Docker Desktop is available through
  `C:\Program Files\Docker\Docker\resources\bin\docker.exe`; the current
  PowerShell `PATH` omits that directory. `docker compose config --quiet`, the
  isolated `cpacodexkeeper:verification-20260714` image build, and `--rm`
  container checks for `--version`, `config validate`, and `doctor` pass with
  a sample configuration and synthetic environment values. No live CPA request
  or credential mutation was performed for this refactor.
