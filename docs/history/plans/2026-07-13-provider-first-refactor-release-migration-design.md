# Provider-First Refactor, Release, and Migration Design

> Status: approved
>
> Proposed release: `CPA Provider Keeper v1.0.0`
>
> Scope: direct breaking refactor and production migration. Codex and xAI are peer providers. Future providers must use the same extension contract.

## Goal

Replace the Codex-centered maintenance application with a provider-first CPA credential maintenance platform.

The new architecture must make these facts explicit:

- `codex` and `xai` are first-class peer providers.
- A provider declares capabilities instead of inheriting a Codex workflow.
- xAI currently supports only metadata fast scanning. It does not download auth-file details, call an external usage API, refresh credentials, or run a deep inspection.
- A future xAI inspection, or a new provider inspection, is added by implementing that provider capability. The scheduler, CLI, mutation layer, and other providers must not gain new provider-specific branches.
- Provider policies remain fail-closed. A configuration can enable a reviewed policy but cannot inject arbitrary destructive error matching logic.

## Non-Goals

- Do not create a dynamically loaded plugin system that imports arbitrary classes from configuration.
- Do not implement a fake xAI inspection just to make provider capability tables symmetric.
- Do not run v0 and v1 daemons in write mode at the same time against one CPA endpoint.
- Do not migrate, download, or persist CPA credential bodies as part of configuration migration.
- Do not retain the current flat non-secret `CPA_*` runtime configuration as a long-lived compatibility path.

## Product and Release Boundary

The v1 product name is **CPA Provider Keeper**. The proposed Python package is `cpa_keeper` and the CLI is `cpa-keeper`.

For the first production release, the existing repository path, Docker service name, and container name may remain `cpacodexkeeper` to avoid unrelated deployment and tunnel changes. Runtime semantics, package naming, commands, configuration, and documentation are nevertheless a breaking v1 migration.

The current `CPACodexKeeper` class, `main.py --once`, daemon default mode, and `--xai-error-sweep-once` are removed from the v1 runtime. A one-time migration tool reads the legacy `.env` and generates v1 files; it is not a second runtime configuration path.

## Provider Model

The central model is a static, tested `ProviderRegistry`. Built-in providers are registered in code, not dynamically imported from TOML.

```python
class ProviderCapability(StrEnum):
    METADATA_SCAN = "metadata_scan"
    INSPECTION = "inspection"
    REFRESH = "refresh"


@dataclass(frozen=True)
class ProviderDefinition:
    provider_id: str
    cpa_auth_file_type: str
    display_name: str
    capabilities: frozenset[ProviderCapability]
    metadata_scanner: MetadataScanner | None
    inspector: Inspector | None
    refresher: Refresher | None
    policy_ids: frozenset[str]
```

Initial capability matrix:

| Provider | Metadata fast scan | Deep inspection | External usage check | Refresh | Current write actions |
|---|---:|---:|---:|---:|---|
| `codex` | yes | yes | OpenAI usage | yes | delete, disable, enable, upload |
| `xai` | yes | no | no | no | fixed metadata-policy delete only |
| future provider | declared by provider | optional | optional | optional | declared by policy |

`xai` must be registered as follows:

```text
provider_id = "xai"
cpa_auth_file_type = "xai"
capabilities = {metadata_scan}
inspector = None
refresher = None
```

The result is intentional and observable:

- `cpa-keeper scan --provider xai` is valid.
- `cpa-keeper inspect --provider xai` exits with configuration/capability code `2` and states that xAI has no inspection capability.
- `cpa-keeper run --provider all` scans both providers, inspects Codex, and reports `xai inspection=not-applicable` rather than silently pretending xAI was inspected.
- When a real xAI inspection API becomes available, implementation adds `providers/xai/inspector.py`, xAI inspection policies, fixtures, and the `inspection` capability. It does not modify the scheduler, mutation executor, or Codex provider.

## Decisions, Policies, and Mutations

Providers do not call CPA mutation APIs directly. They produce typed decisions from typed metadata or inspection facts.

```text
AuthFileMetadata
  name
  provider_id
  disabled
  status
  parsed_status_message

PolicyDecision
  provider_id
  resource_name
  policy_id
  phase                 # metadata_scan or inspection
  action                # none, delete, disable, enable, upload, refresh_then_upload
  priority
  reason_code
  safe_summary
  mutating
  terminal

MutationResult
  decision
  applied
  dry_run
  error_code

ProviderRunReport
  provider_id
  phase
  scanned
  matched
  planned
  applied
  skipped
  failed
  unsupported
```

`DecisionResolver` runs before `MutationExecutor`. It groups decisions by CPA auth-file name and produces exactly one `OperationPlan` for a resource. Its deterministic rules are:

- `delete` has the highest priority and is terminal; all lower-priority decisions for that file are reported as suppressed.
- `refresh_then_upload` is terminal for the current inspection pass and has higher priority than state reconciliation.
- `disable` has higher priority than `enable` when both are proposed for the same resource.
- two distinct mutating actions with the same priority are a policy conflict; the resource is not mutated and the run reports a failure rather than relying on evaluation order.
- policy IDs are the deterministic final tie-breaker only for equivalent actions; they never resolve conflicting actions.

`MutationExecutor` is the only component allowed to call CPA `DELETE`, status `PATCH`, or auth-file `POST`. It owns:

- plan mode and explicit apply mode;
- one lock key per CPA auth-file name;
- same-round mutation marks so inspection cannot reprocess an item mutated by a scan;
- serialized mutation writes;
- safe audit logging without token data or raw error payloads;
- aggregation into a provider and process run report.

Codex refresh is represented as a single typed `RefreshThenUpload` operation, not as independent policy writes. The operation owns the auth-file lock for its complete sequence: call the provider refresher, validate the replacement payload without logging it, upload it to CPA, reapply the required disabled state when applicable, and emit one result. A refresh or upload failure does not trigger an implicit delete or enable fallback.

This preserves the existing reservation, refresh, and dry-run protections while making them provider-neutral.

## Fixed Policy Catalog

Policies are static code definitions with stable IDs. TOML enables or disables a policy; it cannot supply arbitrary xAI text matching.

Initial policy IDs:

```text
codex.metadata.usage-limit.disable
codex.metadata.invalidated-auth.delete
codex.inspection.http-401-402.delete
codex.inspection.expired-without-refresh.delete
codex.inspection.quota-without-refresh.delete
codex.inspection.quota-state.reconcile
codex.inspection.disabled-expiring.refresh

xai.metadata.access-denied.delete
xai.metadata.chat-permission-denied.delete
```

The current strict xAI matchers remain fixed and fail closed:

```text
xai.metadata.access-denied.delete
  provider=xai
  status=error
  top-level JSON object contains only "error"
  normalized error exactly equals "access denied."

xai.metadata.chat-permission-denied.delete
  provider=xai
  status=error
  top-level code exactly equals "permission-denied"
  top-level scalar error contains the fixed chat-endpoint denial marker
```

Both xAI deletion policies are disabled by default. Invalid JSON, duplicate JSON keys, nested error objects, unexpected fields on the compact signature, and near-match text remain non-mutating outcomes.

The existing Codex CSV rules migrate into schema-validated Codex policy overrides. The migrator must preserve an existing non-default rule exactly or fail with an actionable report; it must not silently broaden or discard it. `*` is represented explicitly as `match_any_message = true` and requires a destructive-override acknowledgement before apply mode is allowed.

## Target Module Layout

```text
src/
  cpa_keeper/
    __init__.py
    __main__.py
    cli/
      commands.py
      config_commands.py
      provider_commands.py
    config/
      schema.py
      loader.py
      validation.py
      migration.py
    domain/
      auth_files.py
      capabilities.py
      decisions.py
      policies.py
      reports.py
      status_message.py
    application/
      metadata_scan_service.py
      inspection_service.py
      run_service.py
      scheduler.py
      mutation_executor.py
      reporting.py
    infrastructure/
      cpa_api.py
      http.py
      logging.py
      clock.py
    providers/
      base.py
      registry.py
      codex/
        definition.py
        metadata_policies.py
        inspector.py
        lifecycle_policies.py
        refresher.py
        openai_api.py
      xai/
        definition.py
        metadata_policies.py
```

Placement rules:

- CPA management API code remains provider-neutral infrastructure.
- OpenAI usage and OAuth refresh code move under the Codex provider because they are not global dependencies.
- xAI has no `inspector.py` until it has a real inspection implementation.
- Provider identifiers appear only in the provider registry, provider package, and policy definitions. The CLI, scheduler, and mutation executor must not grow `if provider == ...` branches.
- A future provider is added by registering a definition, declaring capabilities, adding policies, adding only the capability implementations it supports, and supplying provider contract tests.

## Command Model

The current provider-specific command surface becomes provider-scoped subcommands.

```text
cpa-keeper providers list
cpa-keeper config migrate --legacy-env .env --output-dir ./migration
cpa-keeper config validate --config ./config.toml --env-file ./.env
cpa-keeper config show --redacted
cpa-keeper policies list
cpa-keeper doctor

cpa-keeper scan --provider codex
cpa-keeper scan --provider xai
cpa-keeper scan --provider all

cpa-keeper inspect --provider codex
cpa-keeper run --once --provider all
cpa-keeper daemon --provider all
```

All commands default to plan mode. CPA writes require two independent gates: the command must include `--apply`, and the effective configuration must set `write_mode = "apply"`. If only one gate is present, the command exits with code `4`; it does not silently write or silently downgrade to plan mode.

| Command | Read scope | Codex behavior | xAI behavior | Writes by default |
|---|---|---|---|---:|
| `scan` | CPA list metadata | metadata policies | metadata policies | no |
| `inspect` | detail plus provider external checks | supported | capability error | no |
| `run --once` | scan then supported inspection | scan and inspect | scan only | no |
| `daemon` | scheduled provider stages | scan and inspect | scan only | no |

Stable process exit codes:

```text
0  All requested, supported provider stages completed successfully.
1  Upstream request or mutation failed.
2  CLI, configuration, provider, or capability error.
3  Partial provider failure; another requested provider completed.
4  A requested apply was blocked by the safety gate.
```

## Configuration Design

V1 uses two configuration artifacts.

`.env` contains only deployment-specific endpoint/secrets:

```env
CPA_ENDPOINT=https://your-cpa-endpoint
CPA_TOKEN=replace-with-management-token
CPA_PROXY=
```

`config.toml` contains all non-secret runtime behavior:

```toml
[control_plane]
timeout_seconds = 30
max_retries = 2

[execution]
write_mode = "plan"

[scheduler]
metadata_scan_interval_seconds = 60

[providers.codex]
enabled = true
inspection_interval_seconds = 1800
workers = 8
usage_timeout_seconds = 15
quota_threshold_percent = 100
refresh_enabled = true
refresh_before_expiry_days = 3
enabled_policies = [
  "codex.metadata.usage-limit.disable",
  "codex.metadata.invalidated-auth.delete",
  "codex.inspection.http-401-402.delete",
  "codex.inspection.expired-without-refresh.delete",
  "codex.inspection.quota-without-refresh.delete",
  "codex.inspection.quota-state.reconcile",
  "codex.inspection.disabled-expiring.refresh",
]

[policy_overrides.codex.metadata.usage-limit.disable]
error_types = ["usage_limit_reached"]

[policy_overrides.codex.metadata.invalidated-auth.delete]
error_types = ["authentication_error"]
error_codes = ["auth_unavailable"]
message_keywords = ["invalidated"]
match_any_message = false

[providers.xai]
enabled = true
enabled_policies = []
```

After an approved xAI dry-run, an operator enables only the needed fixed policy:

```toml
[providers.xai]
enabled = true
enabled_policies = [
  "xai.metadata.access-denied.delete",
]
```

The normal v1 runtime accepts only `CPA_ENDPOINT`, `CPA_TOKEN`, `CPA_PROXY`, `--config`, and `--env-file` as external configuration sources. Legacy behavior variables such as `CPA_INTERVAL`, `CPA_ERROR_*`, and `CPA_XAI_*` are read only by `config migrate`; if present during a normal v1 run, validation fails with a migration instruction.

Configuration discovery and precedence are fixed:

| Concern | Resolution order | Behavior |
|---|---|---|
| TOML path | `--config` then current-working-directory `config.toml` | Required. No package-directory discovery. |
| env-file path | `--env-file` then current-working-directory `.env` | Optional when all required secrets are in the process environment. |
| `CPA_ENDPOINT`, `CPA_TOKEN`, `CPA_PROXY` | non-empty process environment then selected env file | Process environment wins. `config show --redacted` reports the winning source and any overridden file value. |
| non-secret behavior | selected `config.toml` only | Shell environment cannot silently override policy, interval, or write mode. |
| write permission | `--apply` and `execution.write_mode` | Both are required; neither overrides the other. |

## Direct Migration

This is a direct v0-to-v1 migration rather than a multi-release runtime compatibility layer.

1. Create a release baseline from the current xAI cleanup fix. Do not merge current refactor work with uncommitted user plans or task records.
2. Create branch `codex/provider-first-v1` from that baseline. `main` remains the current production-safe version until v1 acceptance gates pass.
3. Build `config migrate` first. It reads a legacy `.env` without contacting CPA and writes atomically:

```text
migration/
  .env                  # endpoint, token, proxy only
  config.toml           # non-secret behavior and policy IDs
  migration-report.json # mappings, unknown keys, risk notes; never secrets
```

4. The migration fails on unknown legacy keys or custom destructive behavior it cannot reproduce exactly. It never silently drops a value.
5. Validate the generated files with `config validate`; validation checks provider IDs, capabilities, policy ownership, intervals, policy configuration, and secret references without contacting CPA.
6. Run v1 plan mode against the same CPA list metadata used for a short-lived, permission-restricted comparison snapshot. Compare only provider, phase, policy ID, action, reason code, and aggregate counts. Delete the snapshot after comparison.
7. Do not run old and new containers with CPA write capability at the same time. Cross-container locks do not exist.

## Release and Production Rollout

Proposed versioning:

```text
v1.0.0-rc.1
v1.0.0
```

Before rollout, retain the old image digest, Compose file, `.env` backup under protected permissions, configuration hash, and a read-only provider/action aggregate. Do not download CPA credential bodies as a backup artifact.

Rollout sequence:

1. Build and test `v1.0.0-rc.1`; run `cpa-keeper config validate`, `providers list`, and `doctor`.
2. Run `scan --provider all` and `run --once --provider all` in plan mode. Compare the v1 aggregate against the v0 baseline.
3. Verify xAI generated only a CPA list request: zero detail downloads, zero OpenAI usage requests, zero refresh attempts.
4. Stop the old container without removing its image, Compose file, or external `shared` network.
5. Start the v1 daemon in `write_mode = "plan"` without `--apply`. Verify one full Codex inspection interval and at least two xAI scan intervals.
6. Review the planned destructive actions. Change `write_mode` to `apply`, use the explicit applied Compose override that adds `--apply`, run one provider-scoped applied cycle, then start the applied daemon.
7. Observe one full Codex inspection and two xAI scan intervals before promoting the release candidate to `v1.0.0`.

Rollback restores the old image and old configuration only after the v1 container is stopped. A successful CPA physical delete cannot be restored by rolling code back; all destructive policies therefore require a reviewed plan report before first apply.

## Docker and Packaging

The new Compose service may retain the existing operational container name for the first release but removes manual forwarding of every behavior variable.

```yaml
services:
  cpacodexkeeper:
    image: cpa-provider-keeper:${APP_VERSION}
    container_name: cpacodexkeeper
    env_file:
      - .env
    volumes:
      - ./config.toml:/etc/cpa-provider-keeper/config.toml:ro
    # Base Compose is plan-only because it does not include --apply.
    command:
      - daemon
      - --provider
      - all
      - --config
      - /etc/cpa-provider-keeper/config.toml
    networks:
      - shared
```

`docker-compose.apply.yml` is an explicit production override used only after a reviewed plan report:

```yaml
services:
  cpacodexkeeper:
    command:
      - daemon
      - --provider
      - all
      - --config
      - /etc/cpa-provider-keeper/config.toml
      - --apply
```

The override is valid only when `config.toml` also has `write_mode = "apply"`.

Docker changes:

- use a runtime allowlist instead of `COPY . .`;
- exclude `.git`, `.github`, docs, tests, task records, caches, and local environments;
- run as a non-root user with UTF-8 output;
- embed a Git revision/version label;
- add a local health/doctor check;
- retain the external `shared` network but document its creation and ownership as a production prerequisite.

The Python package moves from the generic `src` package to `src/cpa_keeper`. Dependency metadata becomes single-sourced in `pyproject.toml`; `requirements.txt` is generated only if deployment tooling requires it.

## Documentation Deliverables

README becomes a provider-neutral entry page, not the full configuration specification.

```text
README.md
README.en.md
docs/
  architecture.md
  providers.md
  configuration.md
  policies.md
  commands.md
  migration/v0-to-v1.md
  operations/deploy.md
  operations/rollback.md
  development/add-provider.md
  reference/config.example.toml
  reference/config.schema.json
  history/
```

Required documentation matrices:

```text
Provider x Capability
Provider x Phase x Policy x Action x Default State
Command x Provider x Read Scope x Write Scope
```

Provider documentation requirements:

- `providers/codex.md` documents metadata policies, inspection, OpenAI usage, quota reconciliation, and refresh.
- `providers/xai.md` documents scan-only status, the exact two metadata policies, and the explicit absence of inspection/usage/refresh.
- `development/add-provider.md` defines the provider registration contract, capability declaration, policy fixtures, command tests, and documentation updates needed for a new provider.

Historical plans and release notes move under `docs/history/` and are marked `Historical` or `Superseded`. They must not be linked as current operational instructions.

## Refactor Commit Sequence

Every commit must build and pass tests so the branch remains bisectable.

1. `fix(xai): finalize compact access-denied cleanup baseline`
2. `test: snapshot v0 provider decisions and mutation semantics`
3. `feat(config): add v1 schema, validation, and legacy migration`
4. `refactor(core): add provider contracts, decisions, and mutation executor`
5. `refactor(codex): move full maintenance into the codex provider`
6. `feat(xai): register scan-only xai provider and fixed metadata policies`
7. `feat(cli): add provider-scoped config, scan, inspect, run, and daemon commands`
8. `build(docker): ship v1 config-mounted runtime and health checks`
9. `docs: publish provider architecture, migration, and operations manuals`
10. `release: prepare v1.0.0-rc.1`

## Acceptance Gates

- `scan --provider all` scans Codex and xAI.
- `inspect --provider xai` exits with a documented capability error and makes zero CPA detail, external usage, or refresh requests.
- `run --once --provider all` reports xAI inspection as not applicable, not successful inspection and not a failure.
- Existing Codex 401/402, quota, disable/enable, expiry, and refresh actions produce equivalent `PolicyDecision` records before and after refactor.
- Existing xAI strict match positives and negatives remain fail closed, including duplicate JSON keys, nested errors, extra fields, and near matches.
- Plan mode sends zero CPA `DELETE`, `PATCH`, or `POST` requests.
- `DecisionResolver` yields one deterministic operation plan per auth-file name; delete is terminal, and conflicting same-priority mutations fail closed.
- `RefreshThenUpload` holds the mutation lock through provider refresh, validated upload, and required disabled-state restoration.
- Mutation ownership is unique by auth-file name across provider stages and concurrent jobs.
- `config migrate` is repeatable, atomic, secret-safe, and rejects unknown values rather than silently losing them.
- A non-default legacy Codex CSV rule migrates to the typed override schema exactly, or migration fails before deployment.
- `config show --redacted` reports the effective source for every secret field and follows the documented config/env precedence table.
- No provider IDs appear in CLI, scheduler, or mutation executor conditionals outside provider registry and provider packages.
- CI validates Python 3.11 and 3.12, formatting, linting, typing, package build, config schema, docs registry consistency, Compose rendering, Docker smoke, fake CPA contracts, and migration fixtures.

## Approved Decisions

The implementation is a direct `v1.0.0` migration. It does not preserve the current Codex-first runtime or flat behavior environment variables after migration. The approved direction is:

- product/CLI/package rename to CPA Provider Keeper / `cpa-keeper` / `cpa_keeper`;
- direct v0-to-v1 configuration migration rather than a long-lived runtime compatibility layer;
- provider-first capability registry with xAI scan-only in v1;
- default plan mode with explicit `--apply` for all writes;
- direct production release and rollback process described above.
