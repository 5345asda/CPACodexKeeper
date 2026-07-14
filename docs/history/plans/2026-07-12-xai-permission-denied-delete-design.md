# xAI Permission-Denied Deletion Design

## Goal

Delete only CPA auth files that represent an unrecoverable xAI chat-endpoint
permission denial, without sending xAI files through the Codex usage, refresh,
or token-detail workflow.

## Background

The existing error-status sweep reads list metadata from
`GET /v0/management/auth-files`. It currently handles only `type=codex`.
The xAI management UI reports a flat JSON status message shaped like:

```json
{
  "code": "permission-denied",
  "error": "Access to the chat endpoint is denied..."
}
```

This differs from the existing nested Codex error payload, so the current
parser can read the code but not the string error message. The full maintenance
loop must remain Codex-only because it downloads auth details and calls OpenAI
usage APIs.

## Policy

The xAI deletion rule is fail-closed. A file is eligible only when all of the
following are true:

1. The list row has `type == "xai"`.
2. The list row has `status == "error"`.
3. The parsed status-message code equals `permission-denied`.
4. The parsed scalar error text contains `access to the chat endpoint is denied`.
5. The dedicated xAI deletion setting is enabled.

The rule does not depend on the file's disabled state. Both enabled and
disabled matching files are deleted because the provider rejects the chat
endpoint itself. Missing fields, malformed JSON, wrong provider, wrong code,
or a near-match message must never delete a file.

## Architecture

The implementation keeps the existing Codex list-error rules unchanged.

- Keep the legacy Codex list-error message accessor limited to its existing
  `message` and nested `error.message` fields.
- Read a top-level scalar `error` only inside the dedicated xAI predicate, so
  an xAI payload cannot broaden the generic Codex deletion policy.
- Let the lightweight error sweep consider only `codex` and `xai` list rows.
  Full maintenance continues to filter to Codex rows only.
- Add an xAI-only one-shot CLI mode. It invokes the metadata sweep with an
  xAI provider scope and never starts full maintenance.
- In dry-run mode, report `would_delete` separately from `deleted` so a
  preflight cannot be mistaken for a real deletion.

## Configuration

Add one conservative setting:

```text
CPA_XAI_PERMISSION_DENIED_DELETE_ENABLED=false
```

The exact provider, error code, and message fragment remain static code policy.
This prevents an accidental environment change from broadening the deletion
surface. Both the xAI dry-run and the actual one-shot require the switch to be
enabled; operators can keep the persistent setting disabled and supply a
process-scoped enabled value for the preflight. Production enables the
persistent switch only after a dry-run preflight has produced the expected
xAI-only candidates.

## CLI And Operations

Add `--xai-error-sweep-once`.

- `python main.py --once` remains a Codex-only full maintenance run.
- `python main.py --dry-run --xai-error-sweep-once` requires the xAI setting
  to be enabled, then lists and evaluates only xAI rows without a DELETE
  request.
- `python main.py --xai-error-sweep-once` performs the scoped deletion only
  when the xAI setting is enabled.
- Daemon mode continues to run the periodic metadata sweep. Once explicitly
  enabled, the same strict xAI rule can clean new matching rows on that path.

## Tests

Coverage must prove:

1. The screenshot-equivalent xAI payload is parsed and deleted.
2. The same payload under a non-xAI type is not deleted.
3. Wrong code, near-match message, non-error status, malformed JSON, and
   non-string scalar errors are not deleted.
4. Existing nested Codex invalidated-auth deletion behavior is unchanged.
5. Dry-run records `would_delete` without invoking the CPA DELETE method.
6. The xAI-only CLI mode calls only the provider-scoped sweep; `--once`
   retains its full Codex maintenance behavior.
7. Default settings and compose configuration keep the xAI rule disabled.

## Verification And Rollout

Run focused unit tests, the full unittest suite, compose validation, and a
Docker build. Deploy with the new setting disabled. Run a process-scoped dry
run with the setting enabled, inspect only aggregate/candidate evidence, then
enable the persistent setting and run one xAI-only non-dry pass. Finally,
re-query the CPA list and verify that no exact matching xAI rows remain.
