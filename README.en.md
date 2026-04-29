# CPACodexKeeper

[![CI](https://github.com/5345asda/CPACodexKeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/5345asda/CPACodexKeeper/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

[中文](README.md) | [English](README.en.md)

CPACodexKeeper is a Python tool for **inspecting and maintaining codex tokens stored in a CPA management system**.

It does not create tokens. Instead, it continuously maintains **existing codex tokens already stored in a CPA management API**.

## Core capabilities

- check whether a token is still valid
- disable or re-enable tokens based on the actual quota windows returned by usage
- optionally refresh disabled tokens that are close to expiry
- support `.env` configuration, Docker, and GitHub Actions CI

## Who this is for

If you already have a CPA-style token management API and want to:

- clean invalid tokens automatically
- control token usage quota
- re-enable tokens when quota recovers
- enable auto-refresh for disabled near-expiry tokens when needed

then this project is built for that workflow.

## Quick start

```bash
cp .env.example .env
python main.py --once
```

See the sections below for full configuration and runtime details.

---

## 1. What problem this project solves

In practice, codex tokens are not static assets. Over time, they may run into issues such as:

- tokens becoming invalid but still remaining in the management system
- usage quota being exhausted
- tokens being manually disabled and never re-enabled when quota recovers
- disabled tokens getting close to expiry and needing refresh only when refresh is explicitly allowed
- team and non-team accounts returning different usage structures

CPACodexKeeper automates those maintenance tasks so they do not need to be handled manually.

---

## 2. Current maintenance flow

Each inspection round follows this sequence:

1. fetch the token list from the CPA management API
2. keep only tokens where `type=codex`
3. fetch token details one by one
4. read expiry information and remaining lifetime
5. call the OpenAI usage endpoint
6. delete the token if usage returns `401` or `402`, meaning the token is invalid or the workspace is deactivated
7. if usage returns two quota windows, evaluate them by their actual meaning
8. disable when either window reaches the threshold, and re-enable only when both drop below it
9. if the token has **no `refresh_token`** and is already expired, delete it directly
10. if the token has **no `refresh_token`** and the checked quota reaches the threshold, delete it directly
11. if automatic refresh is explicitly enabled and the token is still disabled after quota handling and close to expiry, refresh it
12. upload the refreshed token payload back to CPA

This process is **round-based with intra-round concurrency**. One full round still completes before the next round starts, but multiple tokens can be inspected concurrently within the same round.

In daemon mode, two additional paths may run alongside the main full scan:

- When `CPA_USAGE_QUERY_INTERVAL > 0`, the keeper also starts a log-driven inspection loop based on `/v0/management/usage`. It compares against the previous query time, keeps only accounts that had new usage in that interval and still exist in filtered codex auth-files, then runs quota checks only for those accounts. This log-driven loop only disables; it does not re-enable, refresh, or delete.
- For accounts auto-disabled by the keeper and written into `disabled_accounts.json`, the keeper records `next_check_at` and arms an independent timer. When the timer fires, quota is rechecked immediately; if quota has recovered the account is re-enabled, otherwise a new recheck time is scheduled.

---

## 3. Supported quota logic

The project supports both team and non-team usage responses.

### Team mode

When the usage response includes both windows:

- `rate_limit.primary_window`: usually the primary quota window; logs label it from `limit_window_seconds` as `5h`, `Week`, or another appropriate name
- `rate_limit.secondary_window`: usually the secondary quota window; logs also label it from `limit_window_seconds`

In that case, the program will:

- disable when either `primary_window.used_percent` or `secondary_window.used_percent` reaches the threshold
- re-enable only when both windows are below the threshold
- automatically send the `Chatgpt-Account-Id` header

### Non-team or no weekly window

If no weekly window exists:

- the program falls back to `primary_window.used_percent`

### Default threshold

Default:

- `CPA_QUOTA_THRESHOLD=100`

That means:

- disable only when the checked quota reaches 100%
- re-enable when it drops below 100%
- but if a token has no `refresh_token`, reaching the threshold deletes it instead of only disabling it

---

## 4. Configuration

The project now **uses `.env` only**.

These legacy files are no longer used:

- `config.json`
- `config.example.json`

First copy the template:

```bash
cp .env.example .env
```

Then edit `.env`.

### Configuration fields

- `CPA_ENDPOINT`: CPA management API base URL
- `CPA_TOKEN`: CPA management token
- `CPA_PROXY`: optional HTTP/HTTPS proxy
- `CPA_INTERVAL`: daemon interval in seconds, default `1800`
- `CPA_QUOTA_THRESHOLD`: disable threshold, default `100`
- `CPA_QUOTA_RESET_NONE_RECHECK_SECONDS`: recheck delay in seconds when threshold-reaching quota windows expose no `reset_at`, default `18000`
- `CPA_EXPIRY_THRESHOLD_DAYS`: refresh threshold in days for disabled tokens, default `3`
- `CPA_ENABLE_REFRESH`: whether automatic refresh for disabled tokens is enabled, default `true`
- `CPA_ALLOW_DELETE`: whether auth-files may be deleted, default `true`; when set to `false`, main-flow delete actions are converted into disable actions
- `CPA_HTTP_TIMEOUT`: timeout for CPA API requests, default `30`
- `CPA_USAGE_TIMEOUT`: timeout for OpenAI usage requests, default `15`
- `CPA_USAGE_QUERY_INTERVAL`: log inspection interval in seconds, also used as the lookback window when querying `/v0/management/usage`, default `7200`; set to `0` to disable log inspection
- `CPA_MAX_RETRIES`: retry count for transient network / 5xx failures, default `2`
- `CPA_WORKER_THREADS`: number of worker threads per inspection round, default `8`
- `CPA_LOG_ARCHIVE_MAX_SIZE_MB`: total size limit in MB for archived logs under `./logs/archive`, default `500`

The `.env.example` file already includes bilingual comments for direct editing.

Automatic refresh is enabled by default, but the keeper still refreshes only tokens that remain disabled after quota handling; enabled tokens are left to CPA's own auto-refresh logic. If you need to avoid competing with another writer rotating the same shared `refresh_token`, set it to `false` in `.env`.

The keeper maintains `disabled_accounts.json` in the project root to persist the next quota recheck time `next_check_at` for accounts it auto-disabled after reaching `CPA_QUOTA_THRESHOLD`. Only keeper-disabled accounts are tracked there; manually disabled accounts are never auto-enabled. When daemon mode starts, the file is loaded and independent timers are restored; if a recorded time is already in the past, the recheck runs immediately using the normal quota logic. If the threshold-reaching windows do not expose `reset_at`, the first schedule uses `CPA_QUOTA_RESET_NONE_RECHECK_SECONDS`; later due rechecks fall back to `CPA_INTERVAL` when no new `reset_at` appears.

When `CPA_ALLOW_DELETE=false`, accounts that would otherwise be deleted are disabled instead, and a history event is appended to `delete_blocked_accounts.json` in the project root; these fallback records do not enter `disabled_accounts.json` and do not participate in automatic rechecks.

The program also creates a `./logs/` directory in the project root. Logs for the current process are written to `./logs/<start-time>.txt`; on each startup, older `.txt` logs are automatically compressed into `./logs/archive/`. The archive directory is capped by `CPA_LOG_ARCHIVE_MAX_SIZE_MB`, and older archives are pruned in time order when the total size exceeds that limit.

---

## 5. Running the project

### Requirements

- Python 3.11+
- dependency: `curl-cffi`

Install dependencies:

```bash
pip install -r requirements.txt
```

### Run once

Useful for manual inspection, debugging, or external schedulers:

```bash
cp .env.example .env
python main.py --once
```

### Run in daemon mode

Useful for continuous maintenance:

```bash
python main.py
```

By default this starts:

- the original full inspection loop
- timer-based rechecks for accounts already recorded in `disabled_accounts.json`
- and, when `CPA_USAGE_QUERY_INTERVAL > 0`, an additional log-driven inspection thread

### Dry run

This will not actually delete, disable, enable, or upload updates:

```bash
python main.py --once --dry-run
```

---

## 6. Docker deployment

The project supports Docker, and configuration still comes only from `.env` / environment variables.

### Build the image

```bash
docker build -t cpacodexkeeper .
```

### Run directly

```bash
docker run -d \
  --name cpacodexkeeper \
  -e CPA_ENDPOINT=https://your-cpa-endpoint \
  -e CPA_TOKEN=your-management-token \
  -e CPA_INTERVAL=1800 \
  cpacodexkeeper
```

### Use Compose

Copy the template first:

```bash
cp .env.example .env
```

Then edit `.env` and start:

```bash
docker compose up -d --build
```

---

## 7. Output behavior

For each token, the tool logs details such as:

- multiple tokens may be inspected concurrently within a round
- each token log is buffered and emitted as one block so console output does not interleave across threads

- token name
- email
- current disabled state
- expiry time
- remaining lifetime
- usage check result
- actual quota window information
- whether the token was deleted, disabled, enabled, or refreshed

At the end of each round, it prints a summary including:

- total
- alive
- dead (deleted)
- disabled
- enabled
- refreshed
- skipped
- network errors

---

## 8. Robustness features

The current version already includes several protections:

- strict `.env` validation at startup
- range validation for numeric fields
- separate timeouts for CPA API and usage API
- limited retries for transient network / 5xx failures
- safe fallback when `secondary_window = null`
- one bad token does not break the whole round
- daemon mode keeps running even if one round fails

---

## 9. Developer helpers

The project includes a `justfile` for common commands.

If you use `just`, you can run:

```bash
just install
just test
just run-once
just dry-run
just daemon
just docker-build
just docker-up
just docker-down
```

---

## 10. Tests and CI

### Local tests

```bash
python -m unittest discover -s tests
```

Or:

```bash
just test
```

### GitHub Actions

The repository includes a CI workflow that:

- runs unit tests automatically
- verifies that the Docker image builds successfully

Workflow file:

```text
.github/workflows/ci.yml
```

---

## 11. Project structure

```text
CPACodexKeeper/
├─ src/
│  ├─ cli.py
│  ├─ cpa_client.py
│  ├─ logging_utils.py
│  ├─ maintainer.py
│  ├─ models.py
│  ├─ openai_client.py
│  ├─ settings.py
│  └─ utils.py
├─ tests/
├─ .env.example
├─ docker-compose.yml
├─ Dockerfile
├─ justfile
├─ main.py
├─ README.md
└─ README.en.md
```

---

## 12. Troubleshooting

### Configuration error at startup

Usually caused by missing `.env` fields or invalid values.

Check:

- `CPA_ENDPOINT`
- `CPA_TOKEN`
- whether numeric fields are valid integers

### usage returns `401`

The token is invalid. Under the current logic, it will be deleted.

### usage returns `402`

This usually means the workspace is deactivated or unavailable. Under the current logic, it will also be deleted.

### `secondary_window = null`

No weekly window is available. The tool automatically falls back to the primary window.

### Docker cannot build locally

Make sure Docker CLI is installed and available in your environment.

---

## 13. Intended usage

This project is meant for **authorized internal maintenance scenarios**, such as:

- private CPA management systems
- internal token-pool maintenance
- authorized inspection and cleanup jobs

Real credentials should never be committed to version control. Keep `.env` local or inject it securely in your deployment environment.
