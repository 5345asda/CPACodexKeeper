# Implementation Plan

1. Replace the five v0 route literals in `src/cpa_keeper/infrastructure/cpa_api.py`
   with their direct v8 credential equivalents.
2. Update infrastructure contract tests to assert only v8 requests and retain
   response validation and error handling coverage.
3. Update concise README and operations references to identify the CPA v8
   credential API.
4. Run focused infrastructure tests, the full unit suite, Ruff, compileall,
   package build, Compose validation, and local linux/amd64 image smoke tests.
5. Review the full diff and test evidence; correct any task-required docs or
   tests before committing.
6. Commit only task artifacts, code, tests, and documentation; push the branch.
7. Build and hash a linux/amd64 image locally, transfer and load it on
   racknerd23, then recreate only `cpacodexkeeper` with no dependency rebuild.
8. Verify the release by inspecting image revision, health, restart/OOM state,
   internal v8 list GET shape, and public CPA health/auth boundary.

## Rollback

If the new image fails health, lacks the expected revision, or the authorized
v8 GET credential list fails its aggregate contract, stop the replacement
Keeper and restore the prior tagged image/Compose reference. Do not invoke v0
routes, write CPA configuration, or issue credential mutations as a rollback
test.

## Completion Evidence

- Implementation commit `3444666f8843398266819602ca639052a93d56e5` was pushed
  to `origin/codex/cpa-v8-management-api`; its GitHub CI run `39451902038`
  completed successfully.
- Local validation passed: Ruff, 65 unit/contract tests, compileall, package
  build, Compose config, linux/amd64 image build, and image `--version` plus
  `doctor` smoke tests.
- The completed local image was tagged `cpacodexkeeper:cpa-v8-3444666`, had
  image ID `sha256:17a77095e9018bf588a1aab44fb7b54d6c4062ef381541be8a35965bbd8b3e96`,
  and was transferred as a 61,869,568-byte archive with SHA-256
  `5797a3155f6aa24fe81837feddd0880321c2bd4198bfddfaab8e6198768f6962`.
- On racknerd23, only `/opt/cpacodexkeeper/docker-compose.yml` changed: its
  Keeper image tag moved from `cpacodexkeeper:6837a7c9` to
  `cpacodexkeeper:cpa-v8-3444666`. The original compose file is preserved at
  `/opt/cpacodexkeeper/docker-compose.yml.bak-cpa-v8-3444666-20261020T160329Z`.
- The deployed Keeper is running and healthy with restart count `0` and OOM
  state `false`; its image carries the expected revision label. An in-container
  `CpaApi.list_auth_files()` call returned `ok=true` with `7055` aggregate
  records through the v8 credential list route. No credential name or body was
  recorded.
- The CPA container remained running; public `healthz` returned `200`, while
  unauthenticated `/v1/models` returned `401`. No CPA configuration write,
  explicit credential mutation probe, tunnel change, dependency restart, or
  server-side image build occurred during rollout. The newly started daemon
  did execute its pre-existing configured fast-scan cycle once and recorded
  one successful Codex `disable` action; it was not reverted because doing so
  would alter a live credential state outside the release scope.
