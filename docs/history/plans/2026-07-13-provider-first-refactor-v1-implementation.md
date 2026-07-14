# Provider-First Refactor v1.0 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the Codex-centered keeper with the `cpa-keeper` v1.0 provider-first runtime, where Codex and xAI are peer providers and future providers can add scan and inspection capabilities without modifying global orchestration.

**Architecture:** Build a new `src/cpa_keeper` package around a static provider registry, typed decisions, a deterministic decision resolver, and a single mutation executor. The v1 runtime uses `config.toml` for non-secret behavior and `.env` for CPA connection secrets. It directly migrates v0 configuration with a one-shot CLI tool and does not retain flat behavior-variable compatibility at runtime.

**Tech Stack:** Python 3.11+, standard-library `tomllib`, `unittest`, `curl-cffi`, Docker Compose, GitHub Actions, Ruff, package build tooling.

---

## Delivery Invariants

- Codex and xAI are registry peers. Provider IDs must not appear in CLI, scheduler, or mutation-executor conditionals.
- xAI v1 supports `metadata_scan` only. Any xAI inspection command must fail before CPA detail, external usage, or refresh calls.
- Plan mode emits decisions but sends zero CPA `DELETE`, `PATCH`, or `POST` requests.
- Apply mode requires both `--apply` and `execution.write_mode = "apply"`.
- All actions for one CPA auth-file name pass through one `MutationExecutor` lock key.
- Existing Codex and xAI decisions are characterized before moving implementation. A changed action requires an explicit approval test fixture. `f868918` is the source runtime SHA for fixtures marked `committed_v0`, not the commit SHA of the fixture artifact. `load_all_v0_fixtures()` validates the complete/unique inventory and pending set before `load_source_committed_fixtures()` excludes `pending_worktree` behavior. Migration, release, and rollout gates must consume that source selection and also require a future committed fixture-artifact SHA plus a recorded fixture-tree digest that matches both the commit tree and current worktree. The current worktree cannot satisfy a release gate.
- Runtime v1 accepts only `CPA_ENDPOINT`, `CPA_TOKEN`, `CPA_PROXY`, `--config`, and `--env-file`; legacy behavior variables exist only in the one-time migrator.
- The v0 and v1 daemons must never run with write permission at the same time.

## Task 0: Freeze the v0 Baseline

**Files:**
- Modify: only the already-approved xAI fix files that belong to the current v0 change set.
- Create: `tests/fixtures/v0_decisions/*.json`
- Create: `docs/history/releases/v0-baseline.md`
- Do not stage: existing unrelated untracked task records or historical user documents.

**Step 1: Add v0 characterization fixtures.**

Create fixtures covering:

- Codex metadata usage-limit disable.
- Codex metadata invalidated-auth delete.
- Codex `401` and `402` inspection delete.
- Codex expired-without-refresh delete, quota disable/enable, quota-without-
  refresh deletion, and refresh decision.
- xAI compact `Access denied.` delete candidate.
- xAI chat permission-denied delete candidate.
- xAI near-match, nested JSON, duplicate JSON key, and disabled-policy non-candidates.

Each schema-v2 fixture stores `baseline_status` (`committed_v0` or
`pending_worktree`), only redacted metadata, expected `action`,
`expected_policy_id`, and `expected_reason_code`. A policy ID identifies the
owning policy while the reason code identifies the triggering condition, so
HTTP `401` and `402` can share one policy ID while retaining distinct reason
codes. `committed_v0` identifies behavior attributed to `source_runtime_sha`,
not a committed fixture artifact; `pending_worktree` fixtures are working-tree
characterizations. Neither status alone satisfies a release gate until a
future committed artifact SHA is recorded. Metadata has a fixture-ID-specific,
exact typed contract: it cannot contain free opaque strings or nested
structures, and removed, added, or type-changed fields must fail tests.
Fixtures must not contain token bodies, email addresses, management tokens, or
upstream response bodies.

**Step 2: Write failing fixture-reader tests.**

Create `tests/contract/v0_baseline.py` as the single schema-v2 validator and
loader API. Create `tests/contract/test_v0_decision_fixtures.py` with tests
that reuse that API and verify every fixture has `baseline_status`, `provider`,
`phase`, `expected_action`, `expected_policy_id`, and `expected_reason_code`
fields. All loaders must reject duplicate JSON keys, extra fields, incomplete
or duplicate inventories, pending-set drift, unsafe metadata, and
credential-shaped fields or values.

Run:

```powershell
python -m unittest discover -s tests -p "test_v0_decision_fixtures.py"
```

Expected: failure until fixtures and the reader are added.

**Step 3: Record the baseline.**

Run the current suite and capture only aggregate test evidence in `docs/history/releases/v0-baseline.md`. Record `source_runtime_sha`, the unset or committed fixture-artifact SHA, the unset or committed fixture-tree digest, test command, fixture categories, baseline-status split, and that the fixture set has no secrets. State explicitly that `pending_worktree` fixtures cannot be used as released-v0 behavior, and that the current uncommitted artifact cannot satisfy a migration, release, or rollout gate.

**Step 4: Verify the frozen baseline.**

Run:

```powershell
python -m unittest discover -s tests
python -m compileall -q src tests
git diff --check
```

Expected: all existing tests pass and fixture tests pass. The source-selection
API must exclude `pending_worktree`; the release-gate API must remain blocked
until a future complete commit SHA and fixture-tree digest are recorded. Once
configured, it must read-only validate the commit object, the commit fixture
tree digest, and the current fixture-tree digest before it returns fixtures.

**Step 5: Commit only the v0 baseline scope.**

```powershell
git add -- tests/fixtures/v0_decisions tests/contract/test_v0_decision_fixtures.py docs/history/releases/v0-baseline.md
git commit -m "test: snapshot v0 provider decisions"
```

Commit the existing xAI fix separately if it is still unstaged. Do not label
its `pending_worktree` fixtures as behavior released by the source runtime;
after review, commit the fixture artifact separately, record its complete SHA
and fixture-tree digest, then require both the committed tree and current
tree to match before enabling the future release gate. Do not mix baseline
data with the provider-first refactor.

## Task 1: Create the v1 Package and Entrypoint

**Files:**
- Modify: `pyproject.toml`
- Create: `src/cpa_keeper/__init__.py`
- Create: `src/cpa_keeper/__main__.py`
- Create: `src/cpa_keeper/cli/__init__.py`
- Create: `src/cpa_keeper/cli/commands.py`
- Create: `tests/contract/test_v1_entrypoint.py`

**Step 1: Write failing entrypoint tests.**

Test that `python -m cpa_keeper --help` and the console script `cpa-keeper --help` load a v1 parser without reading `.env`, making network calls, or constructing a provider client.

**Step 2: Run the tests to verify they fail.**

```powershell
python -m unittest discover -s tests -p "test_v1_entrypoint.py"
```

Expected: import or console-script failure because `cpa_keeper` does not yet exist.

**Step 3: Add the package skeleton.**

Set the distribution entrypoint to `cpa_keeper.cli.commands:main`. The parser may initially expose only `--help` and a version value; it must not delegate to the old `src.cli` runtime.

**Step 4: Run focused tests.**

```powershell
python -m unittest discover -s tests -p "test_v1_entrypoint.py"
python -m build
```

Expected: the wheel includes `cpa_keeper`, and module help exits `0`.

**Step 5: Commit.**

```powershell
git add -- pyproject.toml src/cpa_keeper tests/contract/test_v1_entrypoint.py
git commit -m "feat(core): add v1 package entrypoint"
```

## Task 2: Define Provider-First Domain Contracts

**Files:**
- Create: `src/cpa_keeper/domain/__init__.py`
- Create: `src/cpa_keeper/domain/capabilities.py`
- Create: `src/cpa_keeper/domain/auth_files.py`
- Create: `src/cpa_keeper/domain/decisions.py`
- Create: `src/cpa_keeper/domain/reports.py`
- Create: `src/cpa_keeper/providers/base.py`
- Create: `tests/domain/test_capabilities.py`
- Create: `tests/domain/test_decisions.py`

**Step 1: Write failing domain tests.**

Cover immutable dataclasses, provider capability membership, policy-decision validation, and safe summaries that cannot carry raw credential payloads.

**Step 2: Run the tests to verify they fail.**

```powershell
python -m unittest discover -s tests -p "test_capabilities.py"
python -m unittest discover -s tests -p "test_decisions.py"
```

Expected: missing module failures.

**Step 3: Implement minimal contracts.**

Define:

```python
class ProviderCapability(StrEnum):
    METADATA_SCAN = "metadata_scan"
    INSPECTION = "inspection"
    REFRESH = "refresh"


class MutationAction(StrEnum):
    NONE = "none"
    DELETE = "delete"
    DISABLE = "disable"
    ENABLE = "enable"
    UPLOAD = "upload"
    REFRESH_THEN_UPLOAD = "refresh_then_upload"
```

`ProviderDefinition`, `AuthFileMetadata`, `PolicyDecision`, `OperationPlan`, `MutationResult`, and `ProviderRunReport` must be frozen dataclasses with stable identifiers.

**Step 4: Run focused tests.**

```powershell
python -m unittest discover -s tests -p "test_capabilities.py"
python -m unittest discover -s tests -p "test_decisions.py"
```

Expected: all domain contracts pass without HTTP dependencies.

**Step 5: Commit.**

```powershell
git add -- src/cpa_keeper/domain src/cpa_keeper/providers/base.py tests/domain
git commit -m "feat(domain): add provider capability contracts"
```

## Task 3: Add Shared Status Parsing, Policy Catalog, and Conflict Resolution

**Files:**
- Create: `src/cpa_keeper/domain/status_message.py`
- Create: `src/cpa_keeper/domain/policies.py`
- Create: `src/cpa_keeper/application/decision_resolver.py`
- Create: `tests/domain/test_status_message.py`
- Create: `tests/domain/test_decision_resolver.py`

**Step 1: Write failing parser and resolver tests.**

Test duplicate JSON keys fail closed, valid nested and flat list errors parse once, and raw status text never reaches a safe decision summary. Test resolver behavior:

- delete suppresses all lower actions;
- refresh-then-upload suppresses state reconciliation for that inspection pass;
- disable wins over enable;
- same-priority different mutations produce a failed conflict plan;
- equivalent actions use policy ID only as stable ordering.

**Step 2: Run tests to verify failure.**

```powershell
python -m unittest discover -s tests -p "test_status_message.py"
python -m unittest discover -s tests -p "test_decision_resolver.py"
```

Expected: missing parser and resolver failures.

**Step 3: Implement pure functions only.**

The parser accepts a CPA list row and returns a typed parsed status or an invalid result. The policy catalog defines static IDs, provider ownership, phase, action, priority, and whether a decision is terminal. `DecisionResolver` turns all decisions for one auth-file into one `OperationPlan`; it does not perform I/O.

**Step 4: Run focused tests and v0 fixture adapter tests.**

```powershell
python -m unittest discover -s tests -p "test_status_message.py"
python -m unittest discover -s tests -p "test_decision_resolver.py"
python -m unittest discover -s tests -p "test_v0_decision_fixtures.py"
```

Expected: resolver results match the fixture action, policy ID, and reason
code where fixture policy mapping is implemented.

**Step 5: Commit.**

```powershell
git add -- src/cpa_keeper/domain src/cpa_keeper/application/decision_resolver.py tests/domain
git commit -m "feat(policy): add status parsing and decision resolver"
```

## Task 4: Implement the Configuration Schema and Direct Migrator

**Files:**
- Create: `src/cpa_keeper/config/__init__.py`
- Create: `src/cpa_keeper/config/schema.py`
- Create: `src/cpa_keeper/config/loader.py`
- Create: `src/cpa_keeper/config/validation.py`
- Create: `src/cpa_keeper/config/migration.py`
- Create: `docs/reference/config.example.toml`
- Create: `tests/config/test_loader.py`
- Create: `tests/config/test_validation.py`
- Create: `tests/migration/test_legacy_env_migration.py`

**Step 1: Write failing configuration tests.**

Cover these exact rules:

- `--config` selects TOML; otherwise use current-working-directory `config.toml`.
- `--env-file` selects secrets; otherwise use current-working-directory `.env` if present.
- non-empty process `CPA_ENDPOINT`, `CPA_TOKEN`, and `CPA_PROXY` override selected `.env` values.
- shell variables cannot override TOML behavior or policy fields.
- v1 rejects legacy behavior variables at normal runtime.
- `config show --redacted` emits field source but no token, proxy password, URL query, or authorization value.
- unknown provider IDs, policy IDs, unsupported inspection configuration, invalid intervals, and invalid write modes fail validation.
- migration preserves supported legacy values exactly or fails with a report; it must not ignore unknown keys.

**Step 2: Run tests to verify failure.**

```powershell
python -m unittest discover -s tests -p "test_loader.py"
python -m unittest discover -s tests -p "test_validation.py"
python -m unittest discover -s tests -p "test_legacy_env_migration.py"
```

Expected: missing configuration modules and migration command failures.

**Step 3: Implement TOML, secrets, and migration.**

Use `tomllib`. Generate a v1 `config.toml`, split only endpoint/token/proxy into `.env`, and produce an atomic redacted migration report. Represent legacy Codex rules in typed TOML:

```toml
[policy_overrides.codex.metadata.invalidated-auth.delete]
error_types = ["authentication_error"]
error_codes = ["auth_unavailable"]
message_keywords = ["invalidated"]
match_any_message = false
```

Map legacy xAI booleans to enabled policy IDs. Map `CPA_ERROR_DELETE_MESSAGE_KEYWORDS=*` to `match_any_message = true` and require explicit acknowledgement before apply mode is valid.

**Step 4: Run configuration tests.**

```powershell
python -m unittest discover -s tests -p "test_loader.py"
python -m unittest discover -s tests -p "test_validation.py"
python -m unittest discover -s tests -p "test_legacy_env_migration.py"
```

Expected: a migrated default v0 `.env` produces valid v1 files with no runtime network calls.

**Step 5: Commit.**

```powershell
git add -- src/cpa_keeper/config docs/reference/config.example.toml tests/config tests/migration
git commit -m "feat(config): add v1 schema and legacy migration"
```

## Task 5: Build the Provider Registry and Scan-Only xAI Provider

**Files:**
- Create: `src/cpa_keeper/providers/registry.py`
- Create: `src/cpa_keeper/providers/xai/__init__.py`
- Create: `src/cpa_keeper/providers/xai/definition.py`
- Create: `src/cpa_keeper/providers/xai/metadata_policies.py`
- Create: `tests/providers/xai/test_definition.py`
- Create: `tests/providers/xai/test_metadata_policies.py`
- Create: `tests/contract/test_provider_registry.py`

**Step 1: Write failing provider-contract tests.**

Test that the registry contains peer `codex` and `xai` definitions. xAI must advertise only `metadata_scan`; it must not expose an inspector or refresher. Test the two xAI policies against the existing positive and fail-closed fixture set.

**Step 2: Run tests to verify failure.**

```powershell
python -m unittest discover -s tests -p "test_provider_registry.py"
python -m unittest discover -s tests -p "test_definition.py"
python -m unittest discover -s tests -p "test_metadata_policies.py"
```

Expected: registry does not yet contain xAI.

**Step 3: Implement xAI as a peer provider.**

Register xAI with `cpa_auth_file_type="xai"`, metadata scan capability, no inspection capability, and the two static deletion policies. The policies receive parsed metadata and emit `PolicyDecision`; they do not know about CLI, locks, HTTP, or raw JSON parsing.

**Step 4: Run focused and fixture tests.**

```powershell
python -m unittest discover -s tests -p "test_provider_registry.py"
python -m unittest discover -s tests -p "test_metadata_policies.py"
python -m unittest discover -s tests -p "test_v0_decision_fixtures.py"
```

Expected: exact xAI candidates match only when their policy ID is enabled.

**Step 5: Commit.**

```powershell
git add -- src/cpa_keeper/providers tests/providers/xai tests/contract/test_provider_registry.py
git commit -m "feat(xai): register scan-only provider policies"
```

## Task 6: Add the Shared CPA Gateway and Mutation Executor

**Files:**
- Create: `src/cpa_keeper/infrastructure/cpa_api.py`
- Create: `src/cpa_keeper/infrastructure/http.py`
- Create: `src/cpa_keeper/application/mutation_executor.py`
- Create: `tests/infrastructure/test_cpa_api.py`
- Create: `tests/application/test_mutation_executor.py`

**Step 1: Write failing contract tests.**

Use a fake CPA transport to verify list, detail, delete, status patch, and upload request contracts. Test plan mode sends no mutating calls. Test one lock key per auth-file name blocks concurrent scan/inspection mutation attempts. Test `RefreshThenUpload` holds the lock through refresh callback, validated upload, and disabled-state restoration.

**Step 2: Run tests to verify failure.**

```powershell
python -m unittest discover -s tests -p "test_cpa_api.py"
python -m unittest discover -s tests -p "test_mutation_executor.py"
```

Expected: no v1 gateway or executor exists.

**Step 3: Implement the gateway and executor.**

Move only provider-neutral CPA management calls into `cpa_api.py`. Make executor accept an `OperationPlan`, acquire the resource lock, enforce the two apply gates, perform the declared operation, and return a structured result. No provider module may import the CPA mutation client directly.

**Step 4: Run focused tests.**

```powershell
python -m unittest discover -s tests -p "test_cpa_api.py"
python -m unittest discover -s tests -p "test_mutation_executor.py"
```

Expected: fake transport proves no unplanned DELETE/PATCH/POST is sent.

**Step 5: Commit.**

```powershell
git add -- src/cpa_keeper/infrastructure src/cpa_keeper/application/mutation_executor.py tests/infrastructure tests/application/test_mutation_executor.py
git commit -m "feat(core): add cpa gateway and mutation executor"
```

## Task 7: Move Codex Inspection and Lifecycle Behavior into Its Provider

**Files:**
- Create: `src/cpa_keeper/providers/codex/__init__.py`
- Create: `src/cpa_keeper/providers/codex/definition.py`
- Create: `src/cpa_keeper/providers/codex/metadata_policies.py`
- Create: `src/cpa_keeper/providers/codex/inspector.py`
- Create: `src/cpa_keeper/providers/codex/openai_api.py`
- Create: `src/cpa_keeper/providers/codex/lifecycle_policies.py`
- Create: `src/cpa_keeper/providers/codex/refresher.py`
- Create: `tests/providers/codex/test_metadata_policies.py`
- Create: `tests/providers/codex/test_inspector.py`
- Create: `tests/providers/codex/test_lifecycle_policies.py`

**Step 1: Write failing golden-decision tests.**

For every Codex v0 fixture, assert the new provider produces the same
`PolicyDecision` action, policy ID, and reason code. Cover full inspection
401/402, missing refresh token, quota reconciliation, disabled-token enable,
and refresh planning.

**Step 2: Run tests to verify failure.**

```powershell
python -m unittest discover -s tests -p "test_inspector.py"
python -m unittest discover -s tests -p "test_lifecycle_policies.py"
```

Expected: missing Codex provider modules.

**Step 3: Implement provider-local behavior.**

Move OpenAI usage and OAuth refresh under `providers/codex`. The inspector returns facts and decisions; it never calls CPA mutations. The refresher returns a validated replacement payload to `MutationExecutor` through `RefreshThenUpload`.

**Step 4: Run Codex and v0 fixture tests.**

```powershell
python -m unittest discover -s tests -p "test_metadata_policies.py"
python -m unittest discover -s tests -p "test_inspector.py"
python -m unittest discover -s tests -p "test_lifecycle_policies.py"
python -m unittest discover -s tests -p "test_v0_decision_fixtures.py"
```

Expected: Codex behavior is equivalent to v0 at the decision layer.

**Step 5: Commit.**

```powershell
git add -- src/cpa_keeper/providers/codex tests/providers/codex tests/contract/test_v0_decision_fixtures.py
git commit -m "refactor(codex): move inspection and lifecycle policies"
```

## Task 8: Add Provider-Scoped Scan, Inspect, Run, and Scheduler Services

**Files:**
- Create: `src/cpa_keeper/application/metadata_scan_service.py`
- Create: `src/cpa_keeper/application/inspection_service.py`
- Create: `src/cpa_keeper/application/run_service.py`
- Create: `src/cpa_keeper/application/scheduler.py`
- Create: `tests/application/test_metadata_scan_service.py`
- Create: `tests/application/test_inspection_service.py`
- Create: `tests/application/test_run_service.py`
- Create: `tests/application/test_scheduler.py`

**Step 1: Write failing service tests.**

Cover:

- `scan --provider all` processes metadata for Codex and xAI.
- `inspect --provider xai` returns unsupported before any detail or external request.
- `run --provider all` reports xAI inspection as not applicable.
- a metadata mutation mark suppresses later inspection of the same file.
- metadata scan interval and per-provider inspection interval schedule independently.
- provider failures produce `partial_failure` without hiding successful providers.

**Step 2: Run tests to verify failure.**

```powershell
python -m unittest discover -s tests -p "test_metadata_scan_service.py"
python -m unittest discover -s tests -p "test_inspection_service.py"
python -m unittest discover -s tests -p "test_run_service.py"
python -m unittest discover -s tests -p "test_scheduler.py"
```

Expected: missing application services.

**Step 3: Implement capability-driven orchestration.**

Services query `ProviderRegistry`, filter CPA metadata by registered provider type, collect decisions, resolve conflicts, and invoke `MutationExecutor`. The scheduler reads provider capabilities and intervals; it does not know Codex or xAI names.

**Step 4: Run focused service tests.**

```powershell
python -m unittest discover -s tests -p "test_metadata_scan_service.py"
python -m unittest discover -s tests -p "test_inspection_service.py"
python -m unittest discover -s tests -p "test_run_service.py"
python -m unittest discover -s tests -p "test_scheduler.py"
```

Expected: xAI remains scan-only and all aggregate reports use provider/phase fields.

**Step 5: Commit.**

```powershell
git add -- src/cpa_keeper/application tests/application
git commit -m "feat(runtime): add provider-scoped scan and scheduling"
```

## Task 9: Implement the v1 CLI, Doctor, and Exit Codes

**Files:**
- Modify: `src/cpa_keeper/cli/commands.py`
- Create: `src/cpa_keeper/cli/config_commands.py`
- Create: `src/cpa_keeper/cli/provider_commands.py`
- Create: `src/cpa_keeper/cli/doctor.py`
- Create: `tests/cli/test_config_commands.py`
- Create: `tests/cli/test_provider_commands.py`
- Create: `tests/cli/test_doctor.py`

**Step 1: Write failing command tests.**

Test `providers list`, `config migrate`, `config validate`, `config show --redacted`, `policies list`, `doctor`, `scan`, `inspect`, `run --once`, and `daemon`. Assert stable exit codes `0` through `4`, explicit capability errors, and dual apply-gate failures.

**Step 2: Run tests to verify failure.**

```powershell
python -m unittest discover -s tests -p "test_config_commands.py"
python -m unittest discover -s tests -p "test_provider_commands.py"
python -m unittest discover -s tests -p "test_doctor.py"
```

Expected: parser lacks v1 subcommands.

**Step 3: Implement commands.**

`doctor` validates local files, provider registry, configuration, and CPA reachability only when explicitly requested. It must not mutate. `config show --redacted` reports source metadata for effective secrets and full non-secret policy configuration. `scan`, `inspect`, `run`, and `daemon` pass explicit provider scopes to application services.

**Step 4: Run command tests.**

```powershell
python -m unittest discover -s tests -p "test_config_commands.py"
python -m unittest discover -s tests -p "test_provider_commands.py"
python -m unittest discover -s tests -p "test_doctor.py"
python -m cpa_keeper --help
```

Expected: no legacy provider-specific flags remain in v1 help.

**Step 5: Commit.**

```powershell
git add -- src/cpa_keeper/cli tests/cli
git commit -m "feat(cli): add provider scoped v1 commands"
```

## Task 10: Remove v0 Runtime and Validate the Direct Migration Boundary

**Files:**
- Delete: `main.py`
- Delete: `src/__init__.py`
- Delete: `src/cli.py`
- Delete: `src/cpa_client.py`
- Delete: `src/openai_client.py`
- Delete: `src/maintainer.py`
- Delete: `src/models.py`
- Delete: `src/logging_utils.py`
- Delete: `src/settings.py`
- Delete: `src/utils.py`
- Delete: legacy v0 test modules after their scenarios are represented by v1 tests and fixtures.
- Create: `tests/migration/test_v1_rejects_legacy_runtime_env.py`

**Step 1: Write failing migration-boundary tests.**

Test that a v1 command fails when `CPA_INTERVAL`, `CPA_ERROR_*`, or `CPA_XAI_*` is present, unless the command is `config migrate`. Test that only `CPA_ENDPOINT`, `CPA_TOKEN`, and `CPA_PROXY` remain valid runtime environment inputs.

**Step 2: Run tests to verify failure.**

```powershell
python -m unittest discover -s tests -p "test_v1_rejects_legacy_runtime_env.py"
```

Expected: v0 files or loader aliases still accept legacy variables.

**Step 3: Delete v0 runtime after v1 parity passes.**

Remove old runtime modules and obsolete CLI files only after all v0 fixture assertions have a v1 equivalent. Delete tests by scenario replacement, never by reducing coverage.

**Step 4: Run the full suite.**

```powershell
python -m unittest discover -s tests
python -m compileall -q src tests
python -m build
```

Expected: no import references to the old `src` package remain.

**Step 5: Commit.**

```powershell
git add --all
git commit -m "refactor!: remove codex-first v0 runtime"
```

Before staging, inspect `git status --short` and remove unrelated user files from the staging set.

## Task 11: Build v1 Docker, Compose, CI, and Documentation

**Files:**
- Modify: `Dockerfile`
- Modify: `.dockerignore`
- Modify: `docker-compose.yml`
- Create: `docker-compose.apply.yml`
- Modify: `pyproject.toml`
- Create: `docs/architecture.md`
- Create: `docs/providers.md`
- Create: `docs/configuration.md`
- Create: `docs/policies.md`
- Create: `docs/commands.md`
- Create: `docs/migration/v0-to-v1.md`
- Create: `docs/operations/deploy.md`
- Create: `docs/operations/rollback.md`
- Create: `docs/development/add-provider.md`
- Move: current `docs/plans/*` and `docs/changes/*` into `docs/history/` with historical metadata.
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `.github/workflows/ci.yml`
- Create: `tests/contract/test_docs_registry.py`
- Create: `tests/contract/test_compose.py`

**Step 1: Write failing packaging, docs, and Compose tests.**

Test that:

- image source only contains runtime files;
- Compose base command lacks `--apply`;
- Compose apply override adds `--apply`;
- docs capability/policy matrices match the static registry;
- the migration manual includes plan, apply, rollback, and irreversible-delete boundaries;
- `docker compose config --quiet` is required by CI.

**Step 2: Run tests to verify failure.**

```powershell
python -m unittest discover -s tests -p "test_docs_registry.py"
python -m unittest discover -s tests -p "test_compose.py"
```

Expected: old Docker/Compose/docs layout fails the v1 contracts.

**Step 3: Implement delivery assets.**

Use a Docker allowlist and non-root runtime user. Keep the external `shared` network but document it as an explicit prerequisite. Use `env_file` and a read-only TOML mount. Add Python 3.11/3.12, Ruff, formatting, type checks, package build, Compose render, image help smoke test, fake CPA contracts, migration tests, and docs registry checks to CI.

**Step 4: Run local quality gates.**

```powershell
python -m unittest discover -s tests
ruff check src tests
ruff format --check src tests
python -m build
git diff --check
```

Run Docker-specific commands only where Docker is available:

```powershell
docker compose config --quiet
docker build -t cpa-provider-keeper:local .
docker run --rm cpa-provider-keeper:local --help
```

**Step 5: Commit.**

```powershell
git add -- Dockerfile .dockerignore docker-compose.yml docker-compose.apply.yml pyproject.toml docs README.md README.en.md .github tests
git commit -m "build: ship provider-first v1 delivery assets"
```

## Task 12: Release Candidate, Production Migration, and Rollback Drill

**Files:**
- Create: `docs/history/releases/v1.0.0-rc.1.md`
- Create: `docs/history/releases/v1.0.0.md`
- Create: protected production migration artifacts outside the repository.

**Step 1: Build and tag the release candidate.**

```powershell
git tag -a v1.0.0-rc.1 -m "CPA Provider Keeper v1.0.0 release candidate"
```

Build and publish the image with both the release-candidate tag and Git SHA tag.

**Step 2: Migrate configuration without contacting CPA.**

```powershell
cpa-keeper config migrate --legacy-env .env --output-dir .\migration
cpa-keeper config validate --config .\migration\config.toml --env-file .\migration\.env
cpa-keeper config show --config .\migration\config.toml --env-file .\migration\.env --redacted
```

Expected: no secrets printed and no network or mutation action occurs.

**Step 3: Compare plan-mode behavior.**

Run the v1 image in plan mode against a bounded, redacted CPA metadata comparison snapshot. Compare provider, phase, policy ID, action, reason code, and aggregate counts with the v0 baseline. Delete the snapshot after comparison.

Expected: all approved v0 actions are equivalent; xAI has list-only activity.

**Step 4: Deploy plan mode.**

Stop the old container without running `docker compose down`. Start v1 base Compose with `write_mode = "plan"` and no `--apply`. Verify one Codex inspection interval and two xAI scan intervals.

**Step 5: Perform the first controlled apply.**

After human review of the plan report, set `write_mode = "apply"`, start with the Compose apply override, and execute one provider-scoped cycle. Verify CPA audit logs and report counts before enabling the applied daemon.

**Step 6: Rollback drill and final release.**

Stop v1 before restoring v0 image/configuration. Do not run both writers simultaneously. Document that successful CPA deletes cannot be restored by code rollback. Promote `v1.0.0-rc.1` to `v1.0.0` only after the rollback procedure and first applied cycle are verified.

**Step 7: Commit release documentation and push tags.**

```powershell
git add -- docs/history/releases
git commit -m "docs: record provider-first v1 release"
git push origin HEAD
git push origin v1.0.0-rc.1
git push origin v1.0.0
```

Only push the final tag after the production acceptance gates pass.

## Final Verification Checklist

- Full unit and contract suite passes on Python 3.11 and 3.12.
- V1 package build and CLI help work from an installed wheel, not only from a checkout.
- Every registered provider has capability tests and documentation table entries.
- xAI scan-only tests prove no detail/usage/refresh calls.
- Decision resolver and refresh transaction tests prove deterministic mutation plans and lock coverage.
- V0 fixture comparisons prove Codex/xAI policy equivalence where behavior is intentionally retained.
- Legacy migration test fixtures prove exact mapping or explicit migration failure.
- Config validation, redaction, plan/apply gates, exit codes, Compose base/apply behavior, and docs registry consistency pass.
- Docker image build, Compose render, plan-mode rollout, first provider-scoped apply, and rollback drill are documented with evidence.
