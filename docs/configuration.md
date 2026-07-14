# 配置指南

运行时使用两个文件：

- `.env` 保存 CPA 连接值：`CPA_ENDPOINT`、`CPA_TOKEN`、`CPA_PROXY`。
- `config.toml` 保存扫描周期、provider 开关、快扫规则和 Codex 巡检参数。

从权威样例开始：

```bash
cp .env.example .env
cp docs/reference/config.example.toml config.toml
```

进程环境变量优先于 `.env`。传入空的 `CPA_PROXY` 会禁用 `.env` 中的代理并使用直连。

## 全局快扫

```toml
[fast_scan]
interval_seconds = 60
```

每个周期只读取一次 CPA 认证文件列表。所有启用 provider 的快扫规则共享这个列表快照。

## Provider 快扫

```toml
[providers.example]
enabled = true

[providers.example.fast_scan]
enabled = true

[[providers.example.fast_scan.rules]]
id = "temporary-error-disable"
enabled = true
action = "disable"
priority = 50
when = { all = [
  { field = "error.code", op = "eq", value = "temporary" },
] }
```

`providers.<id>` 的名称必须与 CPA 列表中的 `type` 对应。provider 无需 Python 注册表或静态 policy 声明，新增快扫 provider 只需增加 TOML 配置。

规则字段：

| 字段 | 可用值 |
| --- | --- |
| `id` | 小写稳定标识，用于日志。 |
| `enabled` | 关闭时保留规则配置，不执行动作。 |
| `action` | `disable` 或 `delete`。 |
| `priority` | 整数；数值更大者先匹配。相同优先级按文件声明顺序处理。 |
| `when` | 一个 `all`、`any` 或叶子条件。 |

叶子条件只允许以下字段：`error.type`、`error.code`、`error.message`。操作符为 `eq` 和 `contains`；`contains` 可通过 `ignore_case = true` 忽略大小写。

快扫固定只处理 `status = "error"`。启用规则后，匹配记录会立即调用 CPA 的 `disable` 或 `delete` 接口。部署前如需保留观察窗口，可先设置对应 provider 或规则的 `enabled = false`。

## Codex 巡检

```toml
[providers.codex.inspection]
enabled = true
interval_seconds = 1800
workers = 8
usage_timeout_seconds = 15
quota_threshold_percent = 100
refresh_enabled = true
refresh_before_expiry_days = 3
```

巡检规则由程序固定维护，TOML 不包含生命周期规则 DSL。参数含义：

- `interval_seconds`：巡检周期。
- `workers`：并行下载和额度检查的最大工作数；CPA 写操作仍按记录串行执行。
- `usage_timeout_seconds`：OpenAI usage 请求超时。
- `quota_threshold_percent`：禁用或删除判定的额度阈值。
- `refresh_enabled` 与 `refresh_before_expiry_days`：控制已禁用且临近过期凭据的刷新流程。

## 校验

```bash
cpa-keeper config validate --config config.toml --env-file .env
cpa-keeper doctor --config config.toml --env-file .env
```

`config validate` 只读取本地文件。`doctor` 输出已验证的快扫周期和 provider 列表，不执行 CPA 请求。
