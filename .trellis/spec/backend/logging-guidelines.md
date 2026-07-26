# Logging Guidelines

> How logging is done in this project.

---

## Overview

标准库 `logging`，每模块 `LOGGER = logging.getLogger(__name__)`。CLI 的 `main()` 统一 `logging.basicConfig(level=INFO, stream=sys.stdout)`。日志消息是结构化键值格式，方便 grep 和采集。

---

## 消息格式

```
event=<事件名> key=value key=value ...
```

固定 event 清单（新增事件请沿用此命名并更新 `docs/operations.md` 的排查表）：

| event | 级别 | 位置 |
| --- | --- | --- |
| `fast_scan_list` | ERROR | `application/fast_scan.py`，列表读取失败 |
| `fast_scan_action` | INFO / ERROR | 单条规则执行结果 |
| `fast_scan_summary` | INFO | provider 快扫汇总 |
| `inspection_detail` | WARNING | Codex 详情下载失败 |
| `inspection_action` | INFO / WARNING | 生命周期写入结果 |
| `inspection_summary` | INFO | Codex 巡检汇总 |
| `internal_error` | ERROR | `cli/commands.py` 顶层兜底 |

动作日志固定携带：provider、rule_id 或 policy_id、action、outcome（applied / skipped / failed）、失败时的 error_code。

---

## 禁止输出的内容

认证文件名、token、认证正文、上游错误原文一律不进日志。资源用 `resource_hash`（`domain/identifiers.py` 的 sha256 前 12 位）表示，同一轮内可关联同一记录。

测试用 `assertNotIn` 强制这条约定，例如 `tests/application/test_inspection_service.py` 的 `test_failed_detail_is_counted_and_logs_only_a_hash`。

---

## 日志级别

- INFO：正常动作与汇总。
- WARNING：单条记录失败但本轮继续（详情下载失败、写入失败）。
- ERROR：影响整轮的失败（列表读取失败、规则写入失败、内部错误兜底）。

---

## 使用 %s 占位

用 `LOGGER.info("... %s", value)` 惰性格式化，不要 f-string。
