# Error Status Auto Disable Design

## Goal

Disable or delete Codex auth files automatically when the CPA management list already marks them with an actionable error, without turning the full token maintenance round into a one-minute heavy usage scan.

## Current Context

The existing keeper has a full maintenance loop that lists Codex auth files, downloads each selected file, checks OpenAI usage, applies quota disable/enable logic, deletes invalid tokens, and refreshes eligible disabled tokens. That loop is intentionally heavier and currently runs by `CPA_INTERVAL`.

The CPA list endpoint also returns lightweight metadata for each auth file. For quota-limit screenshot cases, the relevant fields are list-level metadata:

- `status == "error"`
- `disabled == false`
- `status_message.error.type == "usage_limit_reached"`

For invalidated-auth screenshot cases, the relevant metadata is:

- `status == "error"`
- `status_message.error.type == "authentication_error"`
- `status_message.error.code == "auth_unavailable"`
- `status_message.error.message` contains `invalidated`

The downloaded auth file body does not include this list-level error metadata, so the fast path must use the list response.

## Approved Approach

Add a lightweight error sweep that runs every minute by default. The sweep only calls `GET /v0/management/auth-files`, parses list metadata, deletes configured hard authentication failures when type, code, and message keyword all match, and calls the existing status API to disable configured quota failures.

The sweep must not download auth file details, check OpenAI usage, refresh tokens, or re-enable files. This keeps it cheap and prevents it from fighting the existing full maintenance loop.

## Non-Conflict Rule

Two scheduled tasks may run in the same process:

- Full maintenance loop: authoritative for detailed usage, delete, enable, refresh, and quota policy.
- Error sweep loop: narrow fast path for `status=error` metadata delete/disable actions only.

The sweep applies delete rules before disable rules. Delete rules may apply to enabled or disabled files because invalidated credentials are not recoverable by waiting, but the default delete rule also requires the invalidated-message keyword to avoid deleting recoverable auth-pool/cooldown errors that reuse `auth_unavailable`. Disable rules only mutate files that are currently `disabled != true` and whose parsed error type is explicitly configured for disable. It never enables a token. If the full maintenance loop later sees the token and the real usage quota has recovered, the existing quota policy may re-enable it.

## Configuration

Add these environment variables:

- `CPA_ERROR_SWEEP_ENABLED`, default `true`
- `CPA_ERROR_SWEEP_INTERVAL`, default `60`
- `CPA_ERROR_DISABLE_TYPES`, default `usage_limit_reached`
- `CPA_ERROR_DELETE_TYPES`, default `authentication_error`
- `CPA_ERROR_DELETE_CODES`, default `auth_unavailable`
- `CPA_ERROR_DELETE_MESSAGE_KEYWORDS`, default `invalidated`

## Operational Behavior

When daemon mode starts, it starts one background error sweep thread and then continues the existing full maintenance loop. The sweep and the full round share a maintenance lock; if a full round is running, the sweep skips that minute instead of reading and mutating from a concurrent stale list. `--once` still runs one full maintenance round only; it does not start the repeated sweep.

Each sweep logs a compact summary:

- scanned Codex auth file count
- delete match count
- disable match count
- deleted count
- disabled count
- failed mutation count

Per-token logs redact secrets and should only include auth file names and error types.
