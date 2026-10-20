# CPA v8 Credential Client Design

## Boundary

The only production code boundary changed in this task is
`infrastructure/cpa_api.py`. Existing method names remain stable so the
application and provider layers keep their current business semantics. The
HTTP implementation changes from legacy v0 auth-file routes to v8 credential
routes.

## Contract

| Keeper operation | v8 request |
| --- | --- |
| List records | `GET /v8/management/credentials` |
| Download one record | `GET /v8/management/credentials/download?name=<name>` |
| Delete one record | `DELETE /v8/management/credentials?name=<name>` |
| Set disabled state | `PATCH /v8/management/credentials/status` with `{name, disabled}` |
| Upload raw JSON | `POST /v8/management/credentials?name=<name>` with the JSON body |

The client keeps Bearer authorization, JSON headers, existing 5xx retry
behavior, response-shape validation, and stable `http_<status>` error codes.
The v8 list response must be an object with a `files` list. Each row must have
a non-empty string `name` and `type`, which are required by the existing
provider dispatch model.

## Explicit Non-Compatibility

There is one direct API family: v8. The client will not probe a version,
select an API family from configuration, retry a v0 route after a v8 failure,
or contain an alternate v0 implementation. A v8 contract failure is surfaced
through the existing operation result path and prevents a silent downgrade.

## Data Flow

`cli.commands` constructs one `CpaApi`; `FastScanService` consumes list,
disable, and delete operations; `CodexInspectionService` consumes list
snapshots and download operations; `CodexMutationExecutor` consumes delete,
status, and upload operations. No provider receives a raw management response
outside the current client result types.

## Testing

The infrastructure fake transport asserts exact v8 URLs, methods, query
parameters, JSON bodies, and successful status handling. Existing application
tests remain the regression guard for lifecycle behavior because their fake API
uses the unchanged client surface.

## Release

Build an explicitly tagged linux/amd64 image locally. Save it to an archive,
hash it, transfer it to racknerd23, load it, update only the Keeper image
reference, and run an app-only Compose recreate with `--no-deps --no-build`.
Acceptance probes are container revision/health/restart/OOM, an internal v8
credential-list GET shape, and the existing public CPA health/auth boundary.
A failed release restores the prior Keeper image/Compose reference; it does
not restore CPA files because this release does not write them.
