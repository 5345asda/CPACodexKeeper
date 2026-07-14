# CPA Provider Keeper

[![CI](https://github.com/5345asda/CPACodexKeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/5345asda/CPACodexKeeper/actions/workflows/ci.yml)

[中文](README.md)

CPA Provider Keeper maintains existing auth files through a CPA management API. A single global list read feeds configurable provider fast-scan rules, while Codex uses a separate lifecycle inspection.

## Behavior

- `[fast_scan]` schedules one shared CPA list read for every enabled provider.
- Fast scan considers only rows with `status = "error"` and normalizes upstream JSON into `error.type`, `error.code`, and `error.message`.
- Rules live in `providers.<id>.fast_scan.rules`. They use `all` or `any`, match with `eq` or `contains`, and can only `disable` or `delete`.
- Rules run in descending priority order. The first match executes immediately against CPA.
- Codex inspection has fixed lifecycle behavior. Its TOML section contains only runtime parameters such as interval, workers, quota threshold, and refresh settings.

There is no `--apply`, plan, or dry-run mode. Disable a rule or provider before deploying a configuration that should not write to CPA. Logs use resource hashes and never include auth-file bodies, token values, raw resource names, or upstream error text.

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

Use `scan` for one fast-scan pass and `daemon` for recurring maintenance.

## Commands

| Command | Purpose |
| --- | --- |
| `cpa-keeper config validate` | Validate TOML and connection inputs without calling CPA. |
| `cpa-keeper doctor` | Print validated runtime settings. |
| `cpa-keeper scan` | Run one global fast scan. |
| `cpa-keeper run` | Run one global fast scan and enabled inspections. |
| `cpa-keeper daemon` | Seed one cycle, then schedule recurring work. |

Every runtime command accepts `--config PATH` and `--env-file PATH`; defaults are `./config.toml` and `./.env`.

## Docker

```bash
docker network inspect shared
docker compose config --quiet
docker compose up -d --build
docker compose logs --tail=100 cpacodexkeeper
```

Compose injects `CPA_ENDPOINT`, `CPA_TOKEN`, and optional `CPA_PROXY` from its environment and mounts `config.toml` read-only.

## Documentation

- [Configuration](docs/configuration.md)
- [Commands](docs/commands.md)
- [Architecture](docs/architecture.md)
- [Operations](docs/operations.md)
- [Provider Extension](docs/providers.md)
- [Configuration Example](docs/reference/config.example.toml)
