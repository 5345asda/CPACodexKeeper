# Error Handling

> How errors are handled in this project.

---

## Overview

核心原则：**校验只在系统边界，内部信任数据**。错误以稳定的字符串 error code 传播，不携带上游响应文本或凭据。

三个边界：

1. **TOML / 环境配置** — pydantic 解析，失败抛 `ConfigError` / `ConfigLoadError`（`src/cpa_keeper/config/`），CLI 捕获后退出码 2。
2. **HTTP 响应** — 形状检查后转成结果类型（`CpaListResult` / `CpaAuthFileResult` / `CpaOperationResult`），失败携带 `error_code`，不抛异常。
3. **CLI 顶层** — `main()` 捕获 `(OSError, ValueError)` 打印 `configuration_error` 退出 2；其余异常只打印 `internal_error` 退出 4，防止泄露敏感信息（`src/cpa_keeper/cli/commands.py`）。

---

## Error Types

- `ConfigError`：pydantic 校验失败的汇总消息，不回显未知字段值（`config/fast_scan.py`）。
- `ConfigLoadError`：文件缺失、TOML 语法错误、密钥缺失（`config/loader.py`）。
- 运行期没有自定义异常类；用结果对象上的 `ok: bool` + `error_code: str | None` 表达失败。

---

## Error Code 约定

稳定、小写、下划线分隔，可直接进日志：`transport_error`、`http_503`、`invalid_list_response`、`cpa_mutation_failed`、`refresh_rejected`。HTTP 状态失败统一 `http_<status>`（见 `cpa_api.py` 的 `_failure_code`）。

---

## Error Handling Patterns

- 写操作失败计入 `failed` 计数并记 ERROR 日志，本轮继续处理其余记录；只有列表读取失败会让整轮以 `UPSTREAM_FAILURE`（退出码 3）终止。
- Codex 刷新链路里的异常（`CodexRefresher.refresh` 抛 `RuntimeError`）在 `CodexMutationExecutor._refresh_then_upload` 被捕获并转为 `refresh_failed` code，不外泄。
- 部分失败 → `RunStatus.PARTIAL_FAILURE`（退出码 1）；无记录 → `EMPTY`（退出码 0）。

---

## Common Mistakes

- 不要在内部数据类加 `__post_init__` 校验——那是防御性编程，已在重构中整体移除。
- 不要把上游异常消息或响应正文放进 error code、日志或 CLI 输出。
