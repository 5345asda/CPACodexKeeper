# Quality Guidelines

> Code quality standards for backend development.

---

## Overview

项目风格由用户明确设定（2026-07-26）：极简、无防御性编程、用现成依赖、边界校验、结构化日志。CI 强制 ruff + unittest + compileall + build + CLI 冒烟。

---

## Forbidden Patterns

- **防御性编程**：内部 dataclass 不写 `__post_init__` 校验；不对自己构造的值做 `isinstance` 检查；不写"以防万一"的 try/except。
- **自造轮子**：配置校验用 pydantic，dotenv 用 python-dotenv，调度用 APScheduler，HTTP 用 curl-cffi。重复的重试/哈希逻辑收敛到 `infrastructure/http.py` 和 `domain/identifiers.py`。
- **泄露敏感信息**：日志、error code、CLI 输出不含资源名、token、上游响应文本（见 logging-guidelines）。
- **鸭子类型逃逸**：层间调用用真实类型或 `Protocol`，不要 `getattr(obj, "ok", False)` 这类猜测式访问。
- 文档不写"不是……而是……"式对比句，不写无意义内容。

## Required Patterns

- 校验集中在系统边界：TOML（pydantic）、HTTP 响应（形状检查）、CLI 顶层（异常分类）。
- 写操作经 `AuthFileMutationCoordinator` 串行化，保证快扫对旧巡检快照的优先权。
- 新配置项加进 TOML 模型并同步 `docs/reference/config.example.toml` 与 `docs/configuration.md`。

---

## Lint

ruff 配置在 `pyproject.toml` `[tool.ruff]`：py311、行宽 120、规则 `E4/E7/E9/F/I/UP/B`。CI 安装最新 ruff，改规则集必须显式写进配置，别依赖版本默认值。

```bash
ruff check src/cpa_keeper tests
```

---

## Testing Requirements

- `python -m unittest discover -s tests` 全绿；测试目录与 src 层对应。
- 测试通过构造函数注入 fake（`transport=FakeTransport(...)`、fake CpaApi），不 monkeypatch 内部实现。
- 涉及日志安全的行为用 `assertLogs` + `assertNotIn` 验证敏感值不出现。
- 只测行为，不测防御性校验（那类校验本身不该存在）。

---

## Code Review Checklist

- 删代码优先于加代码；改动是否引入了可以用现有工具类替代的重复？
- 校验是否放在了边界而不是内部？
- 新日志是否符合 `event=... key=value` 格式且无敏感值？
- `docs/` 与 `config.example.toml` 是否与行为同步？
