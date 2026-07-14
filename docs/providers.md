# Provider 扩展

快扫 provider 是数据驱动的。CPA 列表中的 `type` 与 `providers.<type>` 对应后，程序会读取该 provider 的 `fast_scan` 开关和规则；不需要静态注册表、枚举或 Python policy catalog。

新增快扫 provider 的步骤：

1. 确认 CPA 列表中的 `type` 值。
2. 添加 `[providers.<type>]` 和 `[providers.<type>.fast_scan]`。
3. 用 `error.type`、`error.code`、`error.message` 定义 `disable` 或 `delete` 规则。
4. 运行 `cpa-keeper config validate`。
5. 先关闭规则部署，确认列表类型和日志，再开启规则。

示例 xAI 规则已在 [配置样例](reference/config.example.toml) 中提供：`access-denied-delete` 和 `chat-permission-denied-delete`。

Codex 目前是唯一支持深度巡检的 provider。添加另一个深度巡检 provider 时，需要扩展配置校验、实现详情解析和生命周期行为，并在 CLI 中构造相应巡检服务；快扫配置模型无需变化。
