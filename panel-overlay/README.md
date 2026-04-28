# 在 CLIProxyAPI 管理面板中加入 CPACodexKeeper 入口

CLIProxyAPI 的管理面板（`/management.html`）来自上游
[Cli-Proxy-API-Management-Center](https://github.com/router-for-me/Cli-Proxy-API-Management-Center)，
默认会自动从 GitHub release 下载，并周期性自动更新。

要让面板上出现一个跳转到 Keeper 控制台的按钮，做两件事：

## 1. 关闭面板自动更新

编辑 CLIProxyAPI 的配置（例如 `cliproxyapi.conf`）：

```yaml
remote-management:
  disable-control-panel: false
  disable-auto-update-panel: true
```

`disable-control-panel` 必须是 `false`，否则 `/management.html` 会不可访问；`disable-auto-update-panel` 设为 `true` 后，上游下次发版时不会覆盖你的本地改动。重启 CLIProxyAPI。

## 2. 用 `apply.sh` 注入按钮

`management.html` 的位置不取决于 `auth-dir`。CLIProxyAPI 会把它放在：

- `MANAGEMENT_STATIC_PATH` 指定的目录；或
- 默认的配置文件同级 `static/` 目录。

`~/.cli-proxy-api` 通常只是 `auth-dir`，用于存 token/auth 文件，不一定有管理页。

先打开一次 CLIProxyAPI 管理页，让它下载面板文件：

```text
http://<cliproxyapi-host>:<port>/management.html
```

Homebrew 安装时，常见位置是：

```bash
ls -lh /opt/homebrew/etc/static/management.html
find /opt/homebrew/etc -name management.html -print 2>/dev/null
```

找到真实文件后再注入，例如：

```bash
bash panel-overlay/apply.sh /opt/homebrew/etc/static/management.html
```

脚本做的事：

- 备份原文件到 `<target>.bak.<timestamp>`
- 删除任何由本脚本之前注入的同名块（用 `<!-- BEGIN/END cpa-codex-keeper -->` 包裹）
- 在 `</body>` 前追加 `inject.html` 的内容

注入的 `<script>` 在右下角加一个圆形浮动按钮：左键打开
Keeper UI（默认 `http://<host>:8318/`，存在 `localStorage`），右键改 URL。

## 3. Docker 部署的 volume 写法

Docker Compose 部署时，建议显式设置 `MANAGEMENT_STATIC_PATH` 到一个可持久化 volume。示例：

```yaml
services:
  cliproxyapi:
    environment:
      MANAGEMENT_STATIC_PATH: /data/management-static
    volumes:
      - ./management-static:/data/management-static
```

重启 CLIProxyAPI 后先访问一次 `/management.html`，确认宿主机出现：

```bash
ls -lh ./management-static/management.html
```

然后在宿主机上注入：

```bash
bash panel-overlay/apply.sh ./management-static/management.html
```

如果没有配置 volume，也可以临时从容器里找并拷出来：

```bash
docker compose exec cliproxyapi sh -lc 'find / -name management.html 2>/dev/null'
docker compose cp cliproxyapi:/path/to/management.html ./management.html
bash panel-overlay/apply.sh ./management.html
docker compose cp ./management.html cliproxyapi:/path/to/management.html
```

这种临时方式在容器重建后可能丢失，所以长期使用还是推荐 `MANAGEMENT_STATIC_PATH + volume`。

## 重新执行 / 还原

- 重新执行 `apply.sh` 是幂等的（旧块会先被剥离）
- 想还原：`mv management.html.bak.<timestamp> management.html`
