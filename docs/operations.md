# 运维手册

## 发布前检查

```bash
cpa-keeper config validate --config config.toml --env-file .env
cpa-keeper doctor --config config.toml --env-file .env
```

快扫规则会直接写 CPA。首次部署或变更删除规则时，先关闭目标 rule 或 provider，确认配置、日志和 CPA 列表类型后再启用。

## 单次运行与守护进程

```bash
cpa-keeper run --config config.toml --env-file .env
cpa-keeper daemon --config config.toml --env-file .env
```

`run` 适合手动维护窗口。`daemon` 立即生成一份快照，然后以 `[fast_scan].interval_seconds` 和各 provider 巡检周期持续运行。

## Docker Compose

```bash
docker network inspect shared
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 cpacodexkeeper
```

服务健康检查使用 `cpa-keeper doctor --config /app/config.toml`。它只验证本地配置，不调用 CPA。

## 日志排查

| event | 含义 |
| --- | --- |
| `fast_scan_list` | CPA 列表读取失败。检查 endpoint、token、网络和代理。 |
| `fast_scan_action` | 一条规则的执行结果。 |
| `fast_scan_summary` | provider 快扫汇总。 |
| `inspection_detail` | Codex 详情读取失败。 |
| `inspection_action` | Codex 生命周期写入结果。 |
| `inspection_summary` | Codex 巡检汇总。 |

遇到 `resource_hash` 时，使用同一轮 CPA 列表在受控环境中关联记录。不要将认证文件正文粘贴到日志或工单。

## 常见故障

| 现象 | 处理 |
| --- | --- |
| 退出码 `2` | 运行 `config validate`，修正 TOML 字段或 `.env` 连接值。 |
| 退出码 `3` | 检查 CPA endpoint、管理 token、网络和代理。 |
| 退出码 `1` | 查找 `outcome=failed` 事件及对应错误代码。 |
| 配置修改未生效 | 检查进程环境变量是否覆盖 `.env`，然后重启 daemon。 |
