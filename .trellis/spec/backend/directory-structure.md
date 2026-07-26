# Directory Structure

> How backend code is organized in this project.

---

## Overview

单一 Python 包 `src/cpa_keeper`，按层组织。依赖方向自上而下：`cli` → `application` → `providers` / `infrastructure` / `config` → `domain`。`domain` 不依赖任何其他层。

---

## Directory Layout

```
src/cpa_keeper/
├── __init__.py          # 只有 __version__
├── __main__.py          # python -m cpa_keeper 入口
├── cli/
│   └── commands.py      # argparse 命令面 + 服务装配（唯一的组装点）
├── config/
│   ├── fast_scan.py     # pydantic 模型：TOML 行为配置
│   └── loader.py        # TOML + dotenv 加载，连接密钥与行为分离
├── domain/
│   ├── auth_files.py    # AuthFileMetadata 纯数据类
│   ├── identifiers.py   # resource_hash（日志安全哈希）
│   └── reports.py       # ProviderRunReport / RunPhase
├── infrastructure/
│   ├── http.py          # HttpTransport 协议 + curl-cffi 实现 + request_with_retry
│   └── cpa_api.py       # CPA 管理接口客户端
├── application/
│   ├── fast_scan.py     # 规则匹配 + 全局列表扫描
│   ├── fast_scan_scheduler.py  # APScheduler 调度 + 快照发布
│   ├── inspection_service.py   # Codex 巡检编排
│   ├── mutation_coordinator.py # 按资源名串行化写入 + 代际失效
│   └── results.py       # RunStatus / FastScanResult 等结果类型
└── providers/
    └── codex/           # provider 专属：inspector / lifecycle_policies / mutation / refresher / openai_api

tests/                   # 目录与 src 层一一对应：cli/ config/ domain/ infrastructure/ application/ providers/codex/
```

---

## Module Organization

- **新快扫 provider 不写代码**：CPA 列表的 `type` 对应 TOML `[providers.<type>]`，规则数据驱动。
- **新深度巡检 provider** 建 `providers/<id>/`，在 `config/fast_scan.py` 扩展校验，在 `cli/commands.py` 装配服务。
- 跨 provider 复用的逻辑放 `application` 或 `infrastructure`；provider 私有的 HTTP 端点（如 `codex/openai_api.py`）留在 provider 目录内。
- `cli/commands.py` 里运行时 import 放在函数内，保证 `--help` / `--version` 不加载配置和 HTTP 客户端。

---

## Naming Conventions

- 模块名小写下划线；测试文件 `test_<module>.py`，放在与被测模块对应的 tests 子目录。
- 结果/契约类型用 `*Result`、`*Report`、`*Facts`、`*Decision` 后缀。

---

## Examples

- 分层依赖参考：`src/cpa_keeper/application/fast_scan.py`（向下引用 config/domain/infrastructure，不反向）。
- provider 隔离参考：`src/cpa_keeper/providers/codex/openai_api.py`（OpenAI 端点只在 codex 内可见）。
