# v0 Source Behavior Characterization

**Status:** Historical source-behavior characterization for the provider-first
v1 migration. This worktree does not yet contain a committed fixture artifact.

## Source Runtime And Artifact State

`f868918` is the `source_runtime_sha`: it identifies the legacy v0 runtime
whose already-committed behavior is characterized by fixtures marked
`baseline_status = "committed_v0"`. It is not the commit SHA of this fixture
artifact, and it must not be described as a committed fixture baseline.

Fixtures marked `baseline_status = "pending_worktree"` are working-tree
characterizations only. They do not satisfy a released-v0, migration, or
rollout gate until their implementation is separately reviewed, committed,
and assigned a new source runtime SHA.

The compact xAI access-denied rule and its near-match, nested-error,
duplicate-key, and disabled-policy fail-closed fixtures are
`pending_worktree`. They must not be represented as behavior released by
`f868918`.

The test-only `load_all_v0_fixtures()` API first validates the complete,
unique fixture inventory and its exact pending-worktree set. The source-only
`load_source_committed_fixtures()` API then returns behavior attributed to
`source_runtime_sha` and excludes every `pending_worktree` fixture.

`load_release_gate_fixtures()` intentionally raises while both
`COMMITTED_FIXTURE_ARTIFACT_SHA` and `COMMITTED_FIXTURE_TREE_DIGEST` are
unset. A future release, migration, or rollout gate must require all of the
following before it can consume the source selection:

1. A complete-format commit SHA whose Git object exists as a commit.
2. A recorded SHA-256 digest of that commit's
   `tests/fixtures/v0_decisions` tree.
3. A matching digest for the current working-tree fixture directory.

The gate reads Git only through `cat-file`, `ls-tree`, and `show`; it performs
no Git mutations. A placeholder SHA, unknown commit, missing tree, committed
tree mismatch, or post-commit fixture edit remains blocked. The current
worktree cannot satisfy that gate.

## Decision Scope

The redacted decision fixtures in `tests/fixtures/v0_decisions/` characterize
the v0 decisions that v1 must preserve:

- Codex metadata usage-limit disable and invalidated-auth delete.
- Codex inspection HTTP `401` and `402` delete decisions.
- Codex inspection expired-without-refresh delete, quota disable, quota
  recovery enable, quota-without-refresh delete, and disabled-near-expiry
  refresh-then-upload decisions.
- xAI metadata chat permission-denied delete candidates marked
  `committed_v0`.
- xAI metadata compact access-denied candidates and near-match,
  nested-error, duplicate-key, and disabled-policy fail-closed
  non-candidates marked `pending_worktree`.

Each fixture records a provider, phase, redacted decision metadata, expected
action, expected policy ID, and expected reason code. The policy ID identifies
the policy that owns a decision; the reason code identifies the triggering
condition. For example, inspection HTTP `401` and `402` share
`codex.inspection.http-401-402.delete` but retain distinct `http_401` and
`http_402` reason codes. A fixture is a characterization contract, not a
captured CPA API response or credential export.

Fixture metadata is exact and strongly typed per fixture ID. It permits no
free opaque strings or nested metadata structures: removing a field, adding a
field, or changing a boolean, integer, or string type fails the fixture
contract. This prevents a status, error signature, policy flag, quota percent,
or expiry fact from being silently weakened during the migration.

## No-Secret Rule

Fixtures must contain only synthetic `redacted-*` auth-file labels and
semantic facts such as a status category, HTTP status, quota percentage, or
policy state. They must not contain real auth-file names, email addresses,
access or refresh credentials, management tokens, API keys, passwords,
authorization headers, raw status JSON, or upstream response bodies.

`tests/contract/v0_baseline.py` is the shared validation API used by all
fixture loaders and contract tests. It enforces the fixture inventory,
baseline-status classification, schema-v2 field allowlists, duplicate-key
rejection, redacted labels, exact typed metadata, separate policy/reason
contracts, and credential-shaped field/value rejection. Any future fixture
that needs a status-message case must encode a stable signature label rather
than copying the original payload.

`tests/contract/test_v0_behavior_adapter.py` converts the redacted signature
labels to minimal synthetic inputs, then invokes legacy code rather than
reimplementing decisions. Metadata scans call the legacy list-error matchers;
inspection fixtures call legacy `process_token`. Only HTTP usage checks,
refresh transport, and CPA write transport are faked at their external
boundaries. A legacy matcher or lifecycle branch change therefore changes the
adapter decision and fails the fixture comparison.

## Aggregate Evidence

| Evidence | Current value |
|---|---|
| `source_runtime_sha` | `f868918` |
| Fixture artifact commit SHA | unset; current artifact is uncommitted |
| Fixture artifact tree digest | unset; no committed artifact is recorded |
| Fixture inventory | `15` |
| Source-committed selection | `10` fixtures |
| Pending-worktree selection | `5` compact-xAI fixtures |
| Fixture/selection/adapter focused verification | passed: `19` tests |
| Full-suite verification | passed: `105` tests |
| Release/migration/rollout gate | blocked until future SHA and tree-digest bindings are recorded |

## Verification

Run the complete v0 suite with this exact command:

```powershell
python -m unittest discover -s tests
```

Run the fixture contract alone while editing baseline data:

```powershell
python -m unittest discover -s tests -p "test_v0_decision_fixtures.py"
```

The migration gate additionally requires `python -m compileall -q src tests`
and `git diff --check`, but those checks do not replace the full test command
above. Passing local checks records characterization evidence only; it does
not satisfy the future committed-artifact gate.
