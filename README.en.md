# CPA Provider Keeper

[![CI](https://github.com/5345asda/CPACodexKeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/5345asda/CPACodexKeeper/actions/workflows/ci.yml)

[中文](README.md)

CPA Provider Keeper maintains existing auth files through a CPA management API. A single global list read feeds configurable provider fast-scan rules, while Codex uses a separate quota, expiry, and refresh inspection.

## Behavior

- `[fast_scan]` schedules one shared CPA list read for every enabled provider.
- Fast scan considers only rows with `status = "error"` and normalizes upstream nested or flat JSON into `error.type`, `error.code`, and `error.message`.
- Rules live in `providers.<id>.fast_scan.rules`. They use `all` or `any`, match with `eq` or `contains`, and can only `disable` or `delete`. The highest-priority match wins.
- Enabled fast-scan rules write to CPA immediately. There is no `--apply`, plan, or dry-run mode; disable a rule or provider before deploying a configuration that should not write.
- Codex inspection has fixed lifecycle behavior. Its TOML section contains only runtime parameters: interval, workers, timeout, quota threshold, and refresh settings. xAI is maintained through fast-scan rules.
- Adding a fast-scan provider takes only a new `[providers.<type>]` TOML section, with no code changes.

The program never registers accounts, creates auth files, or stores CPA tokens and auth-file bodies in TOML. Logs never contain resource names, credential bodies, or raw upstream error text.

## Quick Start

```bash
cp .env.example .env
cp docs/reference/config.example.toml config.toml
python -m pip install .
```

Set connection values in `.env`:

```ini
CPA_ENDPOINT=https://cpa.example.internal
CPA_TOKEN=your-management-token
CPA_PROXY=
```

Validate local files and run one maintenance cycle:

```bash
cpa-keeper config validate --config config.toml --env-file .env
cpa-keeper doctor --config config.toml --env-file .env
cpa-keeper run --config config.toml --env-file .env
```

`run` performs one global fast scan followed by enabled Codex inspections. For the fast scan alone:

```bash
cpa-keeper scan --config config.toml --env-file .env
```

## Commands

| Command | Purpose |
| --- | --- |
| `cpa-keeper config validate` | Validate TOML and connection inputs without calling CPA. |
| `cpa-keeper doctor` | Print the validated scan interval and provider list. |
| `cpa-keeper scan` | Run one global fast scan. |
| `cpa-keeper run` | Run one global fast scan and enabled inspections. |
| `cpa-keeper daemon` | Run one cycle immediately, then schedule recurring work. |

Every runtime command accepts `--config PATH` and `--env-file PATH`; defaults are `./config.toml` and `./.env`.

## Logging and Exit Codes

Logs use a structured key-value format. Fast scan emits `event=fast_scan_action` and `event=fast_scan_summary`; Codex inspection emits `event=inspection_detail`, `event=inspection_action`, and `event=inspection_summary`. Each action line carries the provider, rule or policy ID, action, outcome, and error code; resources appear as a `resource_hash` so records can be correlated without exposing file names.

`scan` and `run` print per-provider summary counts on completion: `scanned` (error rows evaluated), `matched` (rule hits), `applied` (successful writes), `skipped`, and `failed`.

| Exit code | Meaning |
| --- | --- |
| `0` | Success, or no rows to process. |
| `1` | Some CPA actions or inspection details failed. |
| `2` | Configuration or command error. |
| `3` | CPA list request failed. |
| `4` | Unclassified internal state. |

## Docker

```bash
docker network inspect shared
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 cpacodexkeeper
```

Compose injects `CPA_ENDPOINT`, `CPA_TOKEN`, and optional `CPA_PROXY` from its environment and mounts `config.toml` read-only.

## Development

```bash
python -m pip install -e .
ruff check src/cpa_keeper tests
python -m unittest discover -s tests
```

Source lives under `src/cpa_keeper`, organized by layer: `config` (TOML and connection loading), `domain` (metadata and report contracts), `infrastructure` (HTTP and CPA clients), `application` (fast scan, inspection, scheduling, write coordination), `providers/codex` (Codex inspection, lifecycle, and refresh), and `cli` (command entrypoint). Validation is concentrated at system boundaries: pydantic parses TOML and HTTP responses get shape checks, while internal dataclasses stay plain data.

## Documentation

- [Configuration](docs/configuration.md)
- [Commands](docs/commands.md)
- [Architecture](docs/architecture.md)
- [Operations](docs/operations.md)
- [Provider Extension](docs/providers.md)
- [Configuration Example](docs/reference/config.example.toml)
