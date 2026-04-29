# CPACodexKeeper 可视化面板

## Context

CPACodexKeeper 目前只是后台守护进程，所有运行状态只在容器日志里。我们要给它加一个**轻量管理面板**，能：
- 看每个 codex token 的最新状态（存活/禁用、配额%、剩余有效期、最近一轮的动作与原因）
- 手动触发整轮或单个 token 的巡检
- 手动对单个 token 做 disable/enable/refresh/delete
- 查看并实时编辑 Keeper 的策略配置

UI 必须是独立子页面，由 Keeper 自己的 HTTP 服务承载；CLIProxyAPI 上游 `management.html` 那边只加一个**入口按钮**——通过本地覆盖面板文件并注入一段小脚本实现，不 fork 上游面板仓库。

## 架构

```
┌─────────────────────────────┐
│ CLIProxyAPI :7138           │
│ /management.html (覆盖版)   │ ── 注入按钮 ──┐
└─────────────────────────────┘                │ 新窗口打开
                                               ▼
┌─────────────────────────────────────────────────┐
│ CPACodexKeeper :8318                            │
│  /              静态 UI (vanilla HTML+JS)        │
│  /api/state     最新一轮的 token 报告 + 统计     │
│  /api/scan      触发整轮巡检                     │
│  /api/scan/{name}  单 token 巡检                 │
│  /api/tokens/{name}  PATCH disable / DELETE     │
│  /api/tokens/{name}/refresh  POST               │
│  /api/config    GET / PUT (热更新+持久化)        │
└─────────────────────────────────────────────────┘
                  │
                  ▼  复用现有 CPAClient + OpenAIClient
              CPA /v0/management/auth-files
```

## 关键设计决定

### 1. 两层 Settings（实时配置）

现状：`src/settings.py` 是 frozen dataclass，启动时一次性读 env+.env。

改为：
- **Layer 1（boot defaults）**：env + `.env`，行为不变。
- **Layer 2（runtime overrides）**：新增 `runtime.json`（路径可由 `CPA_RUNTIME_OVERRIDES` 配，默认在工作目录），存 UI 改过的字段。
- `Settings` 改成 thread-safe 的 mutable 容器，提供 `snapshot()` 和 `update(field, value)`；`update` 同时写内存 + runtime.json。
- 启动时 `load_settings()` 顺序：env/.env → runtime.json overlay → 校验。

热更新生效面：
- **policy 字段（每轮重读，立即生效）**：`quota_threshold`、`expiry_threshold_days`、`enable_refresh`、`worker_threads`、`interval_seconds`、`max_retries`
- **transport 字段（baked 进 client，需要重启）**：`cpa_endpoint`、`cpa_token`、`proxy`、`cpa_timeout_seconds`、`usage_timeout_seconds`
- UI 在 transport 字段旁边显示一个"需要重启容器"的角标，PUT 后照常持久化。

要让 policy 字段每轮重读，`maintainer.py` 内部把对 `self.settings.X` 的直接访问改成在每轮/每 token 入口处 `snap = self.settings.snapshot()` 后用 `snap.X`。改动很局部（`run`、`run_forever`、`process_token`、`_apply_quota_policy`、`_apply_refresh_policy`）。

### 2. 持久化最近一轮的 TokenReport

现状：`TokenLogger` buffer 完日志就 flush 到 console，没有结构化结果。

新增 `TokenReport` dataclass：
```python
@dataclass
class TokenReport:
    name: str
    email: str | None
    disabled: bool
    expiry_remaining_seconds: int | None
    plan_type: str | None
    primary_used_percent: int | None
    secondary_used_percent: int | None
    primary_window_seconds: int | None
    secondary_window_seconds: int | None
    has_credits: bool | None
    last_outcome: str       # alive/dead/disabled/enabled/refreshed/skipped/network_error
    last_actions: list[str] # ["DISABLE: 已禁用", "REFRESH: 刷新成功..."]
    last_log_lines: list[str]  # 完整日志 buffer
    checked_at: float       # epoch
```

在 `CPACodexKeeper` 上加一个 `self.reports: dict[str, TokenReport]`（带锁）。`process_token()` 在每个分支决策处把动作累计进当前 token 的 report，结束时存入 `self.reports[name]`。

`TokenLogger` 增加一个 `actions: list[str]` 副产品（不影响现有 console 输出），方便 maintainer 直接抓出 `[level, msg]` 列表。

### 3. 单 token 重扫接口

`process_token()` 现在签名是 `(token_info, idx, total)`。新加一个 thin wrapper `process_one(name) -> TokenReport`：
1. `self.cpa_client.list_auth_files()` 拿到 token_info（拿不到则 raise）
2. 用 `idx=1, total=1` 调 `process_token`
3. 返回 `self.reports[name]`

### 4. FastAPI 服务

新文件 `src/web.py`：
- 单一 FastAPI app，依赖注入一个全局 `CPACodexKeeper` 实例
- 路由（全部加 `Bearer` 鉴权，token 来自 `CPA_UI_TOKEN` env，未设则跳过鉴权）：
  - `GET /api/state` → `{stats, reports: [TokenReport...], settings: SettingsSnapshot, last_run_started, last_run_finished}`
  - `POST /api/scan` → 异步触发 `keeper.run()`（用一个 `asyncio.Lock` + 后台 thread；同时只允许一轮）
  - `POST /api/scan/{name}` → `keeper.process_one(name)`，同步返回该 token 的最新 report
  - `PATCH /api/tokens/{name}` body `{disabled: bool}` → 直接调 `cpa_client.set_disabled` 并刷新 report
  - `POST /api/tokens/{name}/refresh` → 调 `try_refresh + upload_updated_token`，刷新 report
  - `DELETE /api/tokens/{name}` → 调 `cpa_client.delete_auth_file`，从 reports 移除
  - `GET /api/config` → `{values, sources: {field: "env"|"override"|"default"}, restart_required_fields: [...]}`
  - `PUT /api/config` body `{field: value}` → `settings.update`，持久化，返回新 snapshot
- `GET /` → 返回 `static/index.html`
- 静态资源 `/static/*` → `static/` 目录

启动方式：`main.py` 在 daemon 模式下额外起一个 uvicorn server（用 `threading.Thread` 跑 `uvicorn.run`，daemon=True），绑定 `CPA_UI_HOST`（默认 `0.0.0.0`，方便 Docker）`CPA_UI_PORT`（默认 `8318`）。`--once` 模式不起 web。

### 5. 静态前端（无构建步骤）

`static/index.html` + `static/app.js` + `static/style.css`，纯 vanilla：
- 顶部：统计卡（total/alive/dead/disabled/enabled/refreshed/skipped/network_error）+ "立即巡检"按钮 + "上次完成于 …" 时间戳
- token 表格列：name | email | 状态 | plan | primary% | secondary% | 剩余有效期 | 最近动作 | 操作（重扫/禁用切换/刷新/删除）
- 表格行点击展开：显示该 token 的 `last_log_lines`
- 右上角"配置"抽屉：分两组（policy / transport），policy 字段实时保存，transport 字段 PUT 后角标提示"重启后生效"

UI 状态走轮询（每 5s GET `/api/state`），没有 SSE 复杂度。表格用 `<table>`，CSS 单文件，深色 / 浅色随系统。

### 6. management.html 入口按钮（本地覆盖 + 注入）

- 在 `cliproxyapi.conf` 设 `disable-auto-update-panel: true`
- 拉一份当前 release 的 `management.html`（首次跑 CLIProxyAPI 后从 `~/.cli-proxy-api/management.html` 拿，或从 panel repo release 下）
- 在 `</body>` 之前追加：
  ```html
  <script src="/keeper-button.js"></script>
  ```
- 但 CLIProxyAPI 不会 serve 我们的 `keeper-button.js`。两种思路：
  1. **行内方案（推荐）**：直接在 `</body>` 前追加 `<script>` 块，不依赖额外文件，整段 ~30 行，定义一个固定在右下角的悬浮按钮，`onclick` 打开 `window.open('http://<host>:8318/', '_blank')`。Keeper URL 从 `localStorage` 读取，按钮上有齿轮图标可改。
  2. 把 button 文件放进 `~/.cli-proxy-api/`（或同目录）然后通过 nginx/反代另起。不必要的复杂度。

  用方案 1。

- 把覆盖版 `management.html` 通过 docker-compose volume 挂回去，路径要落在 CLIProxyAPI 容器里 panel 的下载目录（按现有 `~/.cli-proxy-api/` 推断为 `/root/.cli-proxy-api/management.html`，实际路径在实施时通过 `docker exec ... ls` 确认）。

- 提供两个产物在 repo 里：`panel-overlay/management.html.patch`（unified diff，记录注入的 `<script>` 块）和 `panel-overlay/apply.sh`（脚本：取最新官方文件 + 注入 + 输出到指定路径）。

## 改动清单

### 新增文件
- `CPACodexKeeper/src/web.py` — FastAPI app + uvicorn 启动
- `CPACodexKeeper/src/reports.py` — `TokenReport` dataclass + 线程安全的 `ReportRegistry`
- `CPACodexKeeper/static/index.html`
- `CPACodexKeeper/static/app.js`
- `CPACodexKeeper/static/style.css`
- `CPACodexKeeper/panel-overlay/apply.sh` — 脚本：拿最新 management.html + 注入按钮 → 输出文件
- `CPACodexKeeper/panel-overlay/inject.html` — 待注入的 `<script>` 片段
- `CPACodexKeeper/panel-overlay/README.md` — 如何启用 + docker-compose volume 示例
- `CPACodexKeeper/tests/test_web.py` — FastAPI TestClient 跑路由
- `CPACodexKeeper/tests/test_settings_runtime.py` — runtime.json overlay + update 持久化

### 修改文件
- `CPACodexKeeper/src/settings.py` — Settings 改 mutable，加 `snapshot()` / `update()` / runtime.json overlay；新增 `CPA_RUNTIME_OVERRIDES`、`CPA_UI_HOST`、`CPA_UI_PORT`、`CPA_UI_TOKEN` 字段
- `CPACodexKeeper/src/maintainer.py` —
  - 加 `self.reports: ReportRegistry`
  - `process_token()` 在每条 log 旁边把 `(level, msg)` 推到当前 token 的 actions 累计；最后写入 reports
  - 加 `process_one(name) -> TokenReport`
  - 把 `_apply_quota_policy / _apply_refresh_policy / run` 等里直接读 `self.settings.X` 的位置改成顶部 `snap = self.settings.snapshot()`
  - 加 `last_run_started_at / last_run_finished_at` 时间戳
- `CPACodexKeeper/src/cli.py` — daemon 模式下起 web 线程；新加 `--no-web` 关掉
- `CPACodexKeeper/src/logging_utils.py` — `TokenLogger` 暴露 `entries: list[tuple[level, msg]]`，flush 不变
- `CPACodexKeeper/requirements.txt` + `pyproject.toml` — 加 `fastapi`、`uvicorn[standard]`
- `CPACodexKeeper/Dockerfile` — 暴露 8318
- `CPACodexKeeper/docker-compose.yml` — 加 `ports: ["8318:8318"]`、`volumes` 挂 `./runtime.json` 到容器内的 overrides 路径，加 `CPA_UI_*` env
- `CPACodexKeeper/justfile` — 加 `web` / `web-dev` 任务
- `CPACodexKeeper/README.md` — 新增"管理面板"段，说明 URL、token、配置项、面板入口注入

## 复用的现有能力

- `src/cpa_client.py:CPAClient` — 全部 5 个方法直接复用，不动
- `src/openai_client.py:OpenAIClient` — `check_usage` / `refresh_token` / `parse_usage_info` 直接用
- `src/maintainer.py:CPACodexKeeper.{get_token_list, get_token_detail, set_disabled_status, delete_token, try_refresh, upload_updated_token, _stats_snapshot}` — 全部直接调
- `src/models.py:MaintainerStats` — `as_dict()` 已经是 JSON-friendly

## 验证

1. **单元 / 接口**
   - `pytest tests/test_settings_runtime.py` — env-only / overrides-only / 两者合并 / `update()` 持久化往返
   - `pytest tests/test_web.py` — 用 FastAPI `TestClient`，mock 一个假 `CPACodexKeeper`，断言 `/api/state`、PUT `/api/config`、PATCH `/api/tokens/{name}` 的状态码与 body
   - 现有 `python -m unittest discover -s tests` 必须仍然通过

2. **本地端到端**
   - `just install`
   - `cp .env.example .env`，填 `CPA_ENDPOINT` / `CPA_TOKEN` 指向你已有的 7138
   - `just daemon`（或 `python main.py`），日志里看到 `Keeper UI listening on 0.0.0.0:8318`
   - `open http://localhost:8318/`
     - 看到 token 表格 ≥ 1 行
     - 点"立即巡检"，5s 内统计卡刷新
     - 对一个 token 点"重扫"，看到该行更新
     - 改 `quota_threshold` 从 100 → 80，下一轮日志显示新阈值生效
   - `cat runtime.json` 应包含刚改的字段

3. **面板入口**
   - 在 conf 里 `disable-auto-update-panel: true`，重启 CLIProxyAPI
   - 跑 `bash panel-overlay/apply.sh ~/.cli-proxy-api/management.html`，覆盖
   - 浏览器打开 `http://localhost:7138/management.html`，右下角应有"Keeper"悬浮按钮，点击在新 tab 打开 Keeper UI

4. **Docker**
   - `just docker-up`，`docker compose ps` 显示 8318 端口已映射
   - `curl http://localhost:8318/api/state` 返回 200
