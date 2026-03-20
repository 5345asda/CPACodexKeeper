# CPACodexKeeper

[![CI](https://github.com/5345asda/CPACodexKeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/5345asda/CPACodexKeeper/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

[中文](README.md) | [English](README.en.md)

CPACodexKeeper 是一个用于**巡检和维护 CPA 管理端中的 codex token** 的 Python 工具。

它的目标不是生成 token，而是对**已经存储在 CPA 管理 API 中的 codex token** 做持续维护。

## 核心能力

- 检查 token 是否失效
- 按周限额 / 主限额自动禁用或启用
- 自动刷新即将过期的 token
- 支持 `.env` 配置、Docker 和 GitHub Actions CI

## 适合谁用

如果你已经有一个 CPA 管理 API，并且希望：

- 定期清理失效 token
- 控制 token 的 usage 配额占用
- 在额度恢复后自动启用 token
- 在过期前自动刷新 token

那么这个项目就是为这个场景准备的。

## 快速开始

```bash
cp .env.example .env
python main.py --once
```

更多配置和运行方式见下文。

---

## 1. 项目解决什么问题

在实际使用中，codex token 往往不是静态资源，而是会随着时间推移出现以下情况：

- token 已失效，但仍残留在管理端
- token 的 usage 配额已经耗尽，不适合继续分发
- token 已被手动禁用，但额度恢复后没有自动启用
- token 快过期了，需要提前刷新
- team 账号和非 team 账号的 usage 返回结构不同，需要统一处理

CPACodexKeeper 会把这些维护动作自动化，减少人工巡检和手工清理。

---

## 2. 当前维护逻辑

每轮巡检中，程序会按下面的顺序处理：

1. 从 CPA 管理 API 拉取 token 列表
2. 只保留 `type=codex` 的 token
3. 逐个获取 token 详情
4. 读取 token 过期时间和剩余有效期
5. 调用 OpenAI usage 接口检查可用性和限额
6. 如果 usage 返回 `401` 或 `402`，则判定 token 无效或 workspace 已停用，并删除
7. 如果存在**周限额**，优先使用周限额决定禁用 / 启用
8. 如果不存在周限额，则回退到主窗口限额
9. 如果 token 临近过期，则尝试刷新
10. 刷新成功后将最新 token 数据上传回 CPA

这是一个**串行、非并发**流程：一轮结束后才会进入下一轮。

---

## 3. 支持的限额判断规则

项目已经兼容 team 模式和普通模式。

### Team 模式

如果 usage 返回中包含周限额窗口：

- `rate_limit.primary_window`：通常表示较短窗口，例如 5 小时
- `rate_limit.secondary_window`：通常表示周限额

此时程序会：

- 优先使用 `secondary_window.used_percent` 作为禁用 / 启用判断依据
- 自动携带 `Chatgpt-Account-Id` 请求头

### 非 Team / 无周限额模式

如果 usage 中没有周限额窗口：

- 程序会回退到 `primary_window.used_percent` 进行判断

### 默认阈值

默认：

- `CPA_QUOTA_THRESHOLD=100`

也就是：

- 达到 100% 才禁用
- 低于 100% 时可重新启用

---

## 4. 配置方式

项目现在**只保留 `.env` 配置方式**。

已经不再使用：

- `config.json`
- `config.example.json`

先复制模板：

```bash
cp .env.example .env
```

然后编辑 `.env`。

### 配置项说明

- `CPA_ENDPOINT`：CPA 管理 API 地址
- `CPA_TOKEN`：CPA 管理 token
- `CPA_PROXY`：可选代理
- `CPA_INTERVAL`：守护模式轮询间隔，默认 `1800`
- `CPA_QUOTA_THRESHOLD`：禁用阈值，默认 `100`
- `CPA_EXPIRY_THRESHOLD_DAYS`：刷新阈值天数，默认 `3`
- `CPA_HTTP_TIMEOUT`：CPA API 请求超时秒数，默认 `30`
- `CPA_USAGE_TIMEOUT`：OpenAI usage 请求超时秒数，默认 `15`
- `CPA_MAX_RETRIES`：临时网络 / 5xx 错误重试次数，默认 `2`

推荐直接参考 `.env.example` 中的中英双语注释填写。

---

## 5. 运行方式

### 环境要求

- Python 3.11+
- 依赖：`curl-cffi`

安装依赖：

```bash
pip install -r requirements.txt
```

### 单次执行

适合手动巡检、调试或配合外部调度器使用：

```bash
cp .env.example .env
python main.py --once
```

### 守护模式

适合持续运行：

```bash
python main.py
```

### 演练模式

不会真正删除、禁用、启用或上传更新：

```bash
python main.py --once --dry-run
```

---

## 6. Docker 部署

项目支持通过 Docker 运行，配置同样只来自 `.env` / 环境变量。

### 构建镜像

```bash
docker build -t cpacodexkeeper .
```

### 直接运行

```bash
docker run -d \
  --name cpacodexkeeper \
  -e CPA_ENDPOINT=https://your-cpa-endpoint \
  -e CPA_TOKEN=your-management-token \
  -e CPA_INTERVAL=1800 \
  cpacodexkeeper
```

### 使用 Compose

先复制模板：

```bash
cp .env.example .env
```

然后编辑 `.env`，再启动：

```bash
docker compose up -d --build
```

---

## 7. 输出与行为说明

程序会为每个 token 输出一段巡检日志，通常包含：

- token 名称
- 邮箱
- 当前禁用状态
- 过期时间
- 剩余有效期
- usage 检查结果
- 5 小时 / 周限额信息
- 是否被删除、禁用、启用或刷新

在每轮结束后，还会输出汇总统计，例如：

- 总计
- 存活
- 死号（已删除）
- 已禁用
- 已启用
- 已刷新
- 跳过
- 网络失败

---

## 8. 健壮性设计

当前版本已经补了几项关键保护：

- 启动时强校验 `.env` 配置
- 对数值配置做范围检查
- 对 CPA API 和 usage API 设置独立超时
- 对临时网络错误和 5xx 做有限重试
- 对 `secondary_window = null` 做安全回退
- 单个 token 失败不会中断整轮任务
- 守护模式下单轮报错不会导致整个进程退出

---

## 9. 开发辅助

项目内置了 `justfile`，方便统一常用命令。

如果你安装了 `just`，可以直接使用：

```bash
just install
just test
just run-once
just dry-run
just daemon
just docker-build
just docker-up
just docker-down
```

---

## 10. 测试与 CI

### 本地测试

```bash
python -m unittest discover -s tests
```

或者：

```bash
just test
```

### GitHub Actions

项目已包含 CI 工作流：

- 自动运行单元测试
- 自动验证 Docker 镜像可以构建

工作流文件：

```text
.github/workflows/ci.yml
```

---

## 11. 项目结构

```text
CPACodexKeeper/
├─ src/
│  ├─ cli.py
│  ├─ cpa_client.py
│  ├─ logging_utils.py
│  ├─ maintainer.py
│  ├─ models.py
│  ├─ openai_client.py
│  ├─ settings.py
│  └─ utils.py
├─ tests/
├─ .env.example
├─ docker-compose.yml
├─ Dockerfile
├─ justfile
├─ main.py
├─ README.md
└─ README.en.md
```

---

## 12. 故障排查

### 启动时报配置错误

通常是 `.env` 缺字段，或者字段格式不对。

重点检查：

- `CPA_ENDPOINT`
- `CPA_TOKEN`
- 数值项是否为合法整数

### usage 返回 `401`

表示 token 已无效。按当前逻辑会直接删除。

### usage 返回 `402`

通常表示 workspace 已停用或不可用。按当前逻辑也会直接删除。

### `secondary_window = null`

表示没有周限额窗口。程序会自动回退到主窗口判断。

### Docker 无法本地构建

先确认本机是否安装并启用了 Docker CLI。

---

## 13. 适用范围说明

这个项目面向**已授权的内部维护场景**，适合：

- 私有 CPA 管理系统
- 内部 token 池维护
- 已获得授权的自动巡检和清理任务

不建议将真实凭据提交到版本控制中。`.env` 应始终保留在本地或安全的部署环境中。
