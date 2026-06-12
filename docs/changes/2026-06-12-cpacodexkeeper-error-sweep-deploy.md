# CPACodexKeeper Error Sweep Deployment

## Context And Goal

Add automatic handling for CPA list-level error states and deploy the keeper service to the live server with the updated image. The target behavior is:

- Run a lightweight daemon sweep every 60 seconds.
- Disable Codex auth files with `usage_limit_reached` list errors.
- Delete Codex auth files with invalidated `authentication_error` / `auth_unavailable` list errors.
- Avoid conflict between the lightweight sweep and the full maintenance loop.
- Preserve the live compose network topology required by the deployed service.

## Scope Of Changes

- Added list error parsing and policy checks in the maintainer.
- Added daemon-only lightweight error sweep thread.
- Serialized full maintenance and sweep execution with a shared maintenance lock.
- Serialized delete, enable, and disable mutations with a shared status mutation lock.
- Added configurable error sweep settings and compose environment defaults.
- Preserved live compose topology with `host.docker.internal:host-gateway` and external `shared` network.
- Added unit and compose tests for the new behavior.

## Affected Paths And Services

- `.env.example`
- `README.md`
- `README.en.md`
- `docker-compose.yml`
- `src/maintainer.py`
- `src/settings.py`
- `tests/test_docker_compose.py`
- `tests/test_maintainer.py`
- `tests/test_settings.py`
- Live host: `racknerd64`
- Live service path: `/home/tao/services/cpacodexkeeper/repo`
- Live container: `cpacodexkeeper`

## Verification Performed

- Local unit tests: `python -m unittest discover -s tests`
- Whitespace check: `git diff --check HEAD~2..HEAD`
- GitHub Actions run `27413343949`: `test` and `docker-build` succeeded for `b26d41a0ab25c90f4f64532128f983fdcac19901`.
- Server build and recreate were performed from the updated repo before this record was added.
- Live logs showed the error sweep started with a 60 second interval.
- Live sweep result showed `delete_matched=3`, `deleted=3`, `disable_matched=12`, `disabled=12`, `failed=0`.
- Live logs showed later sweep attempts skipped while the full maintenance loop held the shared maintenance lock.
- A post-deploy read-only CPA list probe showed zero current error buckets.

## Risks And Follow-Ups

- The delete rule is intentionally narrow and requires type, code, and message keyword matches. If CPA changes error text, the keyword config may need to be adjusted instead of broadening deletion by default.
- The lightweight sweep depends on CPA list metadata. If list metadata omits `status_message`, the full maintenance loop still handles detail and usage based policies, but the sweep will not infer the error.
- This record was added after the first deployment image. Because the Dockerfile copies the whole repository, the server should be rebuilt again after this record commit if strict image-to-HEAD parity is required.
