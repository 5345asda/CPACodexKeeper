# Backend Development Guidelines

> Conventions for `src/cpa_keeper` in this project.

---

## Pre-Development Checklist

写代码前确认：

1. 改动落在哪一层？依赖方向是否仍然自上而下（`cli` → `application` → `providers`/`infrastructure`/`config` → `domain`）？→ [Directory Structure](./directory-structure.md)
2. 新增校验是否在系统边界（TOML / HTTP 响应 / CLI 顶层）？内部数据类保持纯数据。→ [Error Handling](./error-handling.md)
3. 新日志是否符合 `event=... key=value` 格式，且不含资源名、token、上游原文？→ [Logging Guidelines](./logging-guidelines.md)
4. 是否有现成依赖（pydantic / python-dotenv / APScheduler / curl-cffi）或已有工具函数可用？→ [Quality Guidelines](./quality-guidelines.md)
5. 新配置项是否同步了 `docs/reference/config.example.toml` 和 `docs/configuration.md`？

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | 分层布局、依赖方向、provider 扩展方式 | Filled |
| [Database Guidelines](./database-guidelines.md) | 本项目无数据库；说明状态存放位置 | Filled |
| [Error Handling](./error-handling.md) | 边界校验、error code 约定、退出码映射 | Filled |
| [Quality Guidelines](./quality-guidelines.md) | 禁止防御性编程、ruff 配置、测试要求 | Filled |
| [Logging Guidelines](./logging-guidelines.md) | 结构化 event 日志、敏感信息边界 | Filled |

---

## Quality Check

提交前本地跑（与 CI 一致）：

```bash
ruff check src/cpa_keeper tests
python -m unittest discover -s tests
python -m compileall -q src/cpa_keeper tests
```
