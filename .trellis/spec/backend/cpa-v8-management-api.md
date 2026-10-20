# CPA v8 Management API

## Scenario: Credential maintenance client

### 1. Scope / Trigger

- Trigger: the infrastructure client integrates with CPA's v8 credential
  management endpoints while fast scan and Codex lifecycle code retain their
  existing `CpaApi` surface.
- Scope: `src/cpa_keeper/infrastructure/cpa_api.py` is the sole route-to-HTTP
  boundary. Application and provider layers do not select CPA API versions.

### 2. Signatures

- `CpaApi.list_auth_files() -> CpaListResult`
- `CpaApi.get_auth_file(name: str) -> CpaAuthFileResult`
- `CpaApi.delete_auth_file(name: str) -> CpaOperationResult`
- `CpaApi.set_disabled(name: str, disabled: bool) -> CpaOperationResult`
- `CpaApi.upload_auth_file(name: str, payload: Mapping[str, object]) -> CpaOperationResult`

### 3. Contracts

- List: `GET /v8/management/credentials` returns an object containing a
  `files` list. Every consumed row has a non-empty string `name` and `type`.
- Detail: `GET /v8/management/credentials/download?name=<name>` returns one
  JSON object.
- Delete: `DELETE /v8/management/credentials?name=<name>` succeeds only with
  HTTP `200`.
- Status: `PATCH /v8/management/credentials/status` sends JSON
  `{ "name": <name>, "disabled": <bool> }` and succeeds only with HTTP
  `200`.
- Upload: `POST /v8/management/credentials?name=<name>` sends the raw
  credential JSON object and succeeds only with HTTP `200`.
- Every request uses the configured Bearer management token. Tokens,
  credential names, bodies, and upstream response text stay out of result
  reprs and logs.

### 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| Transport failure | `ok=False`, `error_code="transport_error"` |
| Non-200 list/detail/write response | `ok=False`, `error_code="http_<status>"` |
| List body is not an object with `files: list` | `invalid_list_response` |
| A list row lacks non-empty `name` or `type` | `invalid_list_entry` |
| Detail body is not a JSON object | `invalid_auth_file_response` |
| Legacy delete `204` response | failure `http_204`; do not treat it as v8 success |

### 5. Good/Base/Bad Cases

- Good: use the five v8 credential routes above and propagate the existing
  result objects to application code.
- Base: retain filename-oriented method names because the v8 credential file
  operations accept the existing identifiers.
- Bad: probe a CPA version, select v0 from configuration, retry v0 after a v8
  failure, or add a v0 success status to a v8 operation.

### 6. Tests Required

- Fake transport tests assert the exact HTTP method, v8 path, query parameters,
  and JSON body for list, download, delete, status patch, and raw JSON upload.
- Tests assert response-shape failures and stable `http_<status>` failures.
- A regression test proves delete rejects `204`, preventing a silent return to
  the legacy v0 contract.
- Run `ruff check src/cpa_keeper tests`,
  `python -m unittest discover -s tests`, and
  `python -m compileall -q src/cpa_keeper tests` with `src` from the target
  worktree ahead of any editable installation.

### 7. Wrong vs Correct

#### Wrong

```python
response = self._request("DELETE", "/v0/management/auth-files", params={"name": name})
ok = response.status_code in {200, 204}
```

#### Correct

```python
response = self._request("DELETE", "/v8/management/credentials", params={"name": name})
ok = response.status_code == 200
```
