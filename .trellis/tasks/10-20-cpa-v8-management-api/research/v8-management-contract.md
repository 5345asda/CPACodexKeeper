# CPA v8 Management Contract Research

## Sources

- Official v8.0.4 management API documentation:
  https://raw.githubusercontent.com/router-for-me/CLIProxyAPI/v8.0.4/docs/management-api-v8.md
- Official v8.0.4 credential list implementation:
  https://raw.githubusercontent.com/router-for-me/CLIProxyAPI/v8.0.4/internal/api/handlers/management/auth_files.go
- Official v8.0.4 credential CRUD implementation:
  https://raw.githubusercontent.com/router-for-me/CLIProxyAPI/v8.0.4/internal/api/handlers/management/auth_files_crud.go
- Official v8.0.4 credential status implementation:
  https://raw.githubusercontent.com/router-for-me/CLIProxyAPI/v8.0.4/internal/api/handlers/management/auth_files_fields.go

## Findings

- The v8 management base is `/v8/management`.
- Credential list is `GET /credentials`; the response is an object with a
  `files` array and `observed_at`. With no pagination query, v8.0.4 returns the
  complete list. Optional `page` and `page_size` activate pagination.
- Credential download is `GET /credentials/download?name=<name>`.
- Credential upload accepts raw JSON at `POST /credentials?name=<name>` and
  returns a successful 200 JSON status response.
- Credential deletion is `DELETE /credentials?name=<name>` and successful
  deletion returns 200 JSON status. v8 also supports batch forms, but Keeper
  continues to issue one named operation at a time.
- Disabled state is `PATCH /credentials/status` with JSON fields `name` and
  `disabled`; successful state change returns 200 JSON status.
- The v8 server can return 503 when its core auth manager is unavailable, and
  may return 404 or 409 for unavailable or virtual credential records. Keeper
  exposes these as stable HTTP error codes rather than changing API families.
- The direct client must not issue a v0 retry or version probe after a v8
  failure.

## Live Evidence

On 2026-10-20, the target CPA v8.0.4 returned 200 for both
`/v8/management/credentials` and `/v0/management/auth-files`. Both responses
had top-level keys `files,observed_at` and the same observed entry key set. The
implementation is nevertheless intentionally switched to v8 only; the v0
request was a read-only comparison probe and is not part of the new runtime.
