# Migrate Keeper to CPA v8 management API

## Goal

Replace the Keeper's CPA v0 auth-file client with the direct v8 credential API,
then commit, push, build, and deploy the resulting image to the existing
racknerd23 Keeper service.

## Background

- The production CPA binary is v8.0.4 and the live configuration already uses
  `config-version: 8`.
- The deployed Keeper is revision `6837a7c9`, but the authoritative source
  branch has newer work that must be released from an isolated worktree.
- The current infrastructure client uses only `/v0/management/auth-files` and
  related v0 paths.
- The live v8 credential list endpoint returns the same observed list shape as
  the legacy list endpoint, but the Keeper must use v8 paths directly.

## Requirements

- Replace every Keeper CPA auth-file management request with its v8 credential
  endpoint. Do not retain a v0 route, runtime version detection, fallback, or
  compatibility switch.
- Preserve the current provider-neutral client surface so fast scan and Codex
  lifecycle behavior continue to use the same application contracts.
- Preserve safe response handling: validate external response shapes, return
  stable error codes, and never emit credential bodies, names, tokens, or
  upstream response text in logs.
- Cover v8 list, detail download, delete, disabled-state mutation, and raw JSON
  upload request contracts with unit tests.
- Update operator-facing documentation to identify the v8 API contract and the
  release verification boundary.
- Build a linux/amd64 image outside the server, transfer/load it on racknerd23,
  and recreate only the `cpacodexkeeper` service without rebuilding or changing
  CPA, Cloudflared, the shared network, auth files, logs, or plugins.
- Commit only task-scoped changes, push the implementation branch, and record
  release evidence without exposing secrets or auth-file names.

## Acceptance Criteria

- [x] No production source or test references to `/v0/management/auth-files`
  remain outside historical documents.
- [x] The infrastructure client uses `/v8/management/credentials`,
  `/v8/management/credentials/download`, and
  `/v8/management/credentials/status` with the documented request semantics.
- [x] Unit tests prove the exact methods, paths, parameters, JSON bodies, and
  successful status handling for all five operations.
- [x] The full lint, unit-test, compile, package, Compose, and local image
  smoke checks pass.
- [x] A task-scoped commit is pushed to the remote branch.
- [x] The deployed Keeper runs the new tagged image, remains healthy with zero
  new restarts or OOM events, and its only production API verification is an
  authorized v8 GET credential-list request with a valid aggregate response.
- [x] No CPA config write, manual management-mutation probe,
  credential-content read, tunnel recreation, or server-side image build
  occurs. The released daemon performed one pre-existing configured
  fast-scan `disable` action during its startup cycle; it was not an
  acceptance probe, and its credential state was not changed again during
  rollout verification.

## Out of Scope

- CPA version upgrades, CPA configuration migration, management-key rotation,
  auth-file policy changes, and provider lifecycle-rule changes.
- A compatibility layer for v0 CPA releases or a runtime v0/v8 fallback.
- Changes to Cloudflared, Docker networks, CPA ports, auth/log/plugin mounts,
  or any service other than `cpacodexkeeper`.

## Decisions

- Direct v8 migration is intentional. If the target v8 API is unavailable or
  violates the verified contract, deployment stops rather than falling back.
- The client continues to use filename-oriented lifecycle operations because
  the supported v8 credential file operations accept the existing identifier.
- Release is an image-only, app-only rollout. The server loads a completed
  linux/amd64 archive; it does not build source.

## Notes

- The source worktree used for this task is isolated from unrelated local
  Trellis edits in the original checkout.
