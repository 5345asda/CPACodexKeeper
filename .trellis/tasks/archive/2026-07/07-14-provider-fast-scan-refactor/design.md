# Provider Fast Scan Refactor Design

## Architecture

The fast-scan runtime is a small pipeline:

```text
APScheduler global fast-scan job
  -> CPA list snapshot
  -> error projection
  -> provider rule matching
  -> disable or delete
  -> latest snapshot cache

Existing Codex inspection
  -> reads its nested runtime configuration
  -> keeps its current lifecycle implementation
```

`FastScanService` owns generic configured rules. `CpaApi` owns CPA HTTP
operations. The existing inspection service remains the owner of Codex lifecycle
actions. The CLI creates the fast-scan service and starts the scheduler.

## Configuration

`pydantic` validates TOML data and produces the runtime configuration model.
`python-dotenv` loads local secrets before environment variables are read. CPA
endpoint, token, and optional proxy remain in the environment. `curl-cffi`
performs HTTP calls.

The root configuration has an optional `[control_plane]` table for the shared
HTTP timeout, required `[fast_scan]`, and a dynamic `[providers]` mapping. A
provider can be introduced for fast scan through TOML alone when its CPA auth-file
type equals the provider ID. Only Codex uses the inspection table today.

Rules model the supplied grammar directly. A condition is either `all`, `any`, or
a leaf. Leaves access a normalized error context. The matcher supports `eq` and
`contains`; `ignore_case` changes string comparison to case-insensitive matching.
Rules sort by descending priority, preserving TOML order for ties.

## Scheduling

The global fast-scan job reads the CPA list once for every configured interval and
evaluates all enabled provider rules from that list. It holds no provider-specific
timer or rule implementation. The existing Codex inspection schedule remains
separate; its only configuration change is the move to
`[providers.codex.inspection]`.

## Actions and Lifecycle

Fast scan accepts only `disable` and `delete`. A matching disable skips an already
disabled record. Each action calls the existing CPA mutation client directly and
retains its safe error code for the log. The current Codex inspection action order
is deliberately unchanged.

## Mutation Coordination

Fast scan and Codex inspection share one small auth-file mutation coordinator.
The coordinator owns a short state lock for generations and one mutation lock
per resource name. Each fast-scan write attempt holds a resource lock through
its CPA call, then advances the generation before releasing that resource lock,
including when the call returns a failure or raises. Inspection captures the
generation before its concurrent detail work, then checks it again while holding
the same resource lock before a lifecycle write. A changed generation skips the
stale inspection action with a safe reason code.

This keeps a long inspection from delaying snapshot publication or unrelated
fast-scan records. When the two paths target the same record, write order stays
deterministic: an inspection already in progress finishes first, then fast scan
writes; an inspection waiting behind a fast-scan attempt observes the newer
generation and skips its stale action.

## Observability

The standard `logging` module writes a single stdout stream. Events use
`event=<name>` plus stable keys. Resource names are SHA-256 prefixes. No log path
includes a credential value, response body, raw upstream status message, or raw
error message. CPA and OpenAI clients raise safe error codes; service logs retain
those codes with provider, phase, action, and rule ID.

## Compatibility and Cleanup

The refactor removes only v1 static fast-scan scanners, fast-scan overrides, and
their duplicate error parsers. `main.py`, Docker, the Codex inspection stack, and
the retired v0 runtime remain outside this task.

## Implemented Details

The inspection implementation now has a direct Codex service and mutation
executor instead of registry, capability, decision-plan, and static-policy
layers. Its behavior remains provider-owned: configuration supplies only runtime
values, while Codex evaluates fixed lifecycle facts and performs the resulting
CPA mutation.

`FastScanScheduler` serializes list scans and protects only snapshot publication
and capture. A completed scan publishes an immutable result, then an inspection
callback consumes its captured snapshot outside that lock. The combined `run`
result includes both fast-scan and inspection status, so a failed inspection is
visible to the command exit code.
