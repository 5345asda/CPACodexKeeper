# CPA Provider Keeper

[![CI](https://github.com/5345asda/CPACodexKeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/5345asda/CPACodexKeeper/actions/workflows/ci.yml)

[English](README.en.md)

CPA Provider Keeper 维护 CPA 管理接口中已有的认证文件。它读取全局列表，按 TOML 规则处理错误记录，并为 Codex 执行独立的额度、有效期和刷新巡检。

## 工作方式

- `[fast_scan]` 决定一次全局 CPA 列表扫描的周期。每轮列表只读取一次，所有启用的 provider 共用该快照。
- 快扫只处理 `status = "error"` 的记录。程序将上游的嵌套或扁平错误 JSON 统一为 `error.type`、`error.code` 和 `error.message`。
- 每个 provider 的规则都在 `providers.<id>.fast_scan.rules` 中配置。规则只允许 `disable` 和 `delete`，按 `priority` 从高到低选择第一条匹配规则。
- 启用的快扫规则会立刻写入 CPA。当前版本没有 `--apply`、plan 或 dry-run 开关；上线前可先关闭目标规则或 provider。
- Codex 巡检使用固定生命周期行为，配置只提供周期、并发、超时、额度阈值和刷新参数。xAI 通过快扫规则维护。
- 新增快扫 provider 只需在 TOML 中添加 `[providers.<type>]` 配置，代码无需改动。

程序不会注册账号、创建认证文件或把 CPA token、认证文件正文写入 TOML。日志不会输出资源名、认证正文或上游错误原文。

## 快速开始

```bash
cp .env.example .env
cp docs/reference/config.example.toml config.toml
python -m pip install .
```

在 `.env` 中填写连接信息：

```ini
CPA_ENDPOINT=https://cpa.example.internal
CPA_TOKEN=your-management-token
CPA_PROXY=
```

先校验本地配置，再执行一轮：

```bash
cpa-keeper config validate --config config.toml --env-file .env
cpa-keeper doctor --config config.toml --env-file .env
cpa-keeper run --config config.toml --env-file .env
```

`run` 会先进行一次全局快扫，再执行已启用的 Codex 巡检。若只需要快扫，使用：

```bash
cpa-keeper scan --config config.toml --env-file .env
```

## 命令

| 命令 | 用途 |
| --- | --- |
| `cpa-keeper config validate` | 解析 TOML 与连接配置，不访问 CPA。 |
| `cpa-keeper doctor` | 校验运行参数并输出快扫周期和 provider 列表。 |
| `cpa-keeper scan` | 执行一次全局快扫。 |
| `cpa-keeper run` | 执行一次全局快扫和已启用的巡检。 |
| `cpa-keeper daemon` | 立即执行一轮，然后按 TOML 周期持续调度。 |

所有运行命令接受 `--config PATH` 和 `--env-file PATH`。省略时使用当前目录的 `config.toml` 和 `.env`。

## 日志与退出码

日志为结构化键值格式。快扫使用 `event=fast_scan_action` 与 `event=fast_scan_summary`，Codex 巡检使用 `event=inspection_detail`、`event=inspection_action` 与 `event=inspection_summary`。每条动作日志携带 provider、规则或 policy 标识、动作、结果和错误代码；资源以 `resource_hash` 表示，便于关联同一记录而不泄露文件名。

`scan` 与 `run` 结束时按 provider 输出汇总计数：`scanned`（本轮评估的错误记录数）、`matched`（命中规则数）、`applied`（成功写入数）、`skipped`、`failed`。

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功完成，或当前没有可处理记录。 |
| `1` | 本轮存在部分失败。 |
| `2` | 配置或命令错误。 |
| `3` | CPA 列表请求失败。 |
| `4` | 未分类的内部状态。 |

## Docker

```bash
docker network inspect shared
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 cpacodexkeeper
```

Compose 从环境变量读取 `CPA_ENDPOINT`、`CPA_TOKEN` 和可选的 `CPA_PROXY`，只读挂载 `config.toml`。

## 开发

```bash
python -m pip install -e .
ruff check src/cpa_keeper tests
python -m unittest discover -s tests
```

源码在 `src/cpa_keeper` 下按层组织：`config`（TOML 与连接加载）、`domain`（元数据与报表契约）、`infrastructure`（HTTP 与 CPA 客户端）、`application`（快扫、巡检、调度与写入协调）、`providers/codex`（Codex 巡检、生命周期与刷新）、`cli`（命令入口）。校验集中在系统边界：TOML 由 pydantic 解析，HTTP 响应做形状检查；内部数据类是纯数据。

## 文档

- [配置指南](docs/configuration.md)
- [命令参考](docs/commands.md)
- [运行架构](docs/architecture.md)
- [运维手册](docs/operations.md)
- [Provider 扩展](docs/providers.md)
- [配置样例](docs/reference/config.example.toml)
