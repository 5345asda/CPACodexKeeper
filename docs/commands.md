# 命令参考

所有运行命令支持 `--config PATH` 和 `--env-file PATH`。未提供时使用当前目录的 `config.toml` 与 `.env`。

| 命令 | 行为 |
| --- | --- |
| `cpa-keeper config validate` | 解析 TOML 和环境连接值。 |
| `cpa-keeper doctor` | 显示已验证的周期和 provider 列表。 |
| `cpa-keeper scan` | 执行一次全局快扫。 |
| `cpa-keeper run` | 执行一次快扫，再执行所有启用的巡检。 |
| `cpa-keeper daemon` | 先执行一轮，再进入 APScheduler 调度。 |

示例：

```bash
cpa-keeper config validate --config config.toml --env-file .env
cpa-keeper doctor --config config.toml --env-file .env
cpa-keeper scan --config config.toml --env-file .env
cpa-keeper run --config config.toml --env-file .env
cpa-keeper daemon --config config.toml --env-file .env
```

`scan` 和 `run` 会执行启用的快扫动作。命令行没有 provider 范围、`--once`、`--apply` 或 dry-run 参数；运行范围和动作均由 TOML 的开关与规则决定。

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功，或没有记录需要处理。 |
| `1` | 部分 CPA 动作或巡检详情失败。 |
| `2` | 配置解析失败或命令无效。 |
| `3` | 获取 CPA 列表失败。 |
| `4` | 未分类内部状态。 |
