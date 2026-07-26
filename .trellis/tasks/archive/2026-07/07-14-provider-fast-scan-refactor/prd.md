# Provider Fast Scan Refactor

## Goal

Replace only the static fast-scan policy path with the supplied TOML contract:
one global fast-scan cadence and provider-scoped rules. The result must be
smaller, configuration-driven, observable, and ready for another provider to opt
into fast scanning without Python policy code.

## Confirmed Facts

- Docker runs `cpa-keeper daemon` and only copies `src/cpa_keeper` into the image.
- The working tree already contains an uncommitted v1 implementation. It has 181
  passing tests, though its configuration contract rejects the requested TOML.
- Current v1 fast scanning depends on fixed policy IDs, provider registries,
  decision plans, and policy overrides. These layers do not represent the target
  contract.
- The existing Codex inspection behavior remains in scope only as a consumer of
  its nested runtime settings. Its lifecycle policy, worker behavior, and xAI
  boundary are not being redesigned in this task.

## Requirements

### R1: TOML Contract

- `[fast_scan].interval_seconds` controls one shared list-read cadence.
- Each provider has `enabled`, optional `fast_scan`, and optional `inspection`
  tables.
- A provider fast scan has `enabled` and ordered `rules`.
- A rule has `id`, `enabled`, `action`, `priority`, and `when`.
- Fast-scan actions are limited to `disable` and `delete`.
- Rule conditions support only `all`, `any`, `field`, `op`, `value`, and optional
  `ignore_case`. Supported fields are `error.type`, `error.code`, and
  `error.message`. Supported operations are `eq` and `contains`.

### R2: Fast-Scan Behavior

- One fast-scan tick reads the CPA auth-file list once and evaluates every enabled
  provider from that snapshot.
- Fast scan evaluates only rows whose status is `error`.
- Nested and flat CPA error payloads project into one `error.type`, `error.code`,
  `error.message` context. Rule code never depends on the upstream nesting shape.
- Rules are evaluated by descending priority. Equal priorities keep TOML declaration
  order. Each auth file receives at most one fast-scan action.
- `providers.<id>.enabled = false` disables the provider's fast scan and inspection.
- `providers.<id>.fast_scan.enabled = false` disables only fast-scan actions.

### R3: Inspection Boundary

- `[providers.codex.inspection]` loads the supplied nested runtime settings.
- Codex inspection remains rule-free and keeps its existing lifecycle behavior.
- Disabling a provider stops its fast scan and inspection. Disabling only
  `providers.<id>.fast_scan` leaves its inspection behavior available.

### R4: Runtime and Logging

- The scheduler has one global fast-scan job. That job reads the CPA list once and
  dispatches all enabled provider rules from the same list snapshot.
- Configuration parsing uses TOML and a config-validation library. Dotenv loading
  uses a maintained dependency where local secret loading remains necessary.
- Logs use the standard logging library with stable `key=value` fields. Fast-scan
  action logs include provider, rule ID, action, outcome, safe resource hash, and
  error code when present. Logs never include credentials, response bodies, raw
  `status_message`, or raw error text.
- A fast-scan write attempt invalidates an older inspection snapshot for the
  same auth file. Writes for one auth file are serialized while unrelated auth
  files remain independent.

### R5: Cleanup and Documentation

- Remove the fixed fast-scan policy scanners, fast-scan override configuration,
  duplicate status-message parsing, and the obsolete rule-template documentation.
- Keep the existing Codex inspection implementation and its supporting lifecycle
  policy code unless a direct fast-scan integration requires a narrow adapter.
- Publish one executable configuration example that matches the parser.
- Keep architecture, configuration, command, and lifecycle documentation concise.

## Acceptance Criteria

- [ ] The TOML supplied in this request loads without adaptation apart from the
  optional existing `[control_plane]` connection timeout table.
- [ ] The four supplied Codex and xAI rules execute the requested action for both
  nested and flat error payloads. `revoked` matches the Codex delete rule.
- [ ] A fast-scan tick reads the CPA list once while handling all enabled providers.
- [ ] Disabling Codex fast scan leaves a scheduled Codex inspection active; disabling
  Codex entirely stops both jobs.
- [ ] The existing Codex inspection tests still pass with the nested inspection
  configuration. xAI remains outside inspection.
- [ ] Every fast-scan and inspection action produces an auditable safe log event.
- [ ] The executable example config validates in a test.
- [ ] The full test suite, ruff, compile check, package build, Compose validation,
  and Docker image build pass.
- [ ] A refresh/upload or lifecycle mutation cannot overwrite a successful
  fast-scan action for the same auth file from a newer scan.

## Out of Scope

- Remote deployment, current credential mutation, and live CPA API calls.
- Reworking Codex lifecycle decisions, inspection concurrency, provider registry,
  or retired v0 runtime code.
- A generic inspection rule language. Inspection remains provider-owned runtime
  logic.

## Current Result

- The supplied TOML contract is the executable configuration example and parses
  without translation.
- Fast scan now reads the CPA list once per global tick, normalizes nested and
  flat errors, and applies ordered provider rules. Successful fast-scan writes
  are excluded from the captured Codex inspection snapshot; skipped writes are
  still available to inspection.
- Codex inspection remains provider-owned and rule-free. Its runtime settings
  are accepted only under `providers.codex.inspection`; xAI has no inspection
  implementation.
- Fast scan publishes an immutable snapshot before inspection begins. Inspection
  runs outside the snapshot lock, so a long inspection does not delay the next
  scheduled fast scan.
- A shared auth-file mutation coordinator serializes writes per resource and
  rejects an inspection action when a later fast-scan write attempt changed that
  resource.
- `pytest`, `unittest`, ruff, compile, package build, installed console-script
  smoke checks, source-level CLI smoke checks, Docker Compose validation, image
  build, and `--rm` container smoke checks passed. Docker Desktop is available
  through its absolute CLI path while the current PowerShell `PATH` omits that
  directory.
