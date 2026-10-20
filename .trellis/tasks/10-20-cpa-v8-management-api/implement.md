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
