# Repo Version Monitor

定时检查 GitHub 仓库 tags，与本地 SQLite 中保存的版本对比；发现新 tag 后，通过 Mailgun API 使用 `httpx` 发送邮件通知。

邮件发送使用 Mailgun API，具体可参考[官方手册](https://documentation.mailgun.com/docs/mailgun/user-manual/sending-messages/send-http)。

## 配置

复制示例配置：

```bash
cp config.example.toml config.toml
```

设置密钥：

```bash
export GITHUB_TOKEN="github_pat_xxx"       # 可选，但建议设置，避免 GitHub 匿名限额
export MAILGUN_API_KEY="key-xxx"
```

密钥读取优先级（GitHub 令牌与 Mailgun 令牌 一致）：先读环境变量（变量名分别由 `token_env`、`api_key_env` 指定，默认 `GITHUB_TOKEN`、`MAILGUN_API_KEY`），不存在时回退到配置文件中的 `token` / `api_key` 字段。推荐使用环境变量，内联字段仅作为本地调试的便捷方式，注意不要将含密钥的配置文件提交到版本库。执行 `check` 时会输出密钥的实际来源（环境变量还是配置文件）。

获取标签时采用分层策略：配置了 GitHub 令牌时走 GraphQL API（按 tag 提交时间倒序拉取全量）；未配置令牌或 GraphQL 失败时回退到 REST API 接口（该接口无时间排序，程序会筛选符合版本号形态的标签并按数值取最大）。

## 使用

### 整理配置文件

命令参考：

```bash
uv run repo-version-monitor --config config.toml format
```
当 `config.toml` 不存在时从同目录模板配置 `config.example.toml` 复制一份；

存在则校验格式是否合法，并规范化所有 `[[products]]` 块，产品之间留空行，为未配置 `branch` 的产品补空值。

### 添加公开仓库

命令参考：

```bash
## 以仓库 https://github.com/encode/httpx 为例
uv run repo-version-monitor --config config.toml add encode/httpx [--name httpx] [--branch 0.28]
```

默认截取仓库名作为产品名，可用 `--name` 参数指定自定义名称。支持 `--branch` 参数指定分支，支持存在同产品的不同分支，若不指定则默认跟踪所有标签。


### 修改追踪分支

命令参考：

```bash
uv run repo-version-monitor --config config.toml edit grafana --branch 13.0
```

支持参数 `--name` 指定，存在重名时可用 `--repository` 精确指定，赋空值 `--branch ""` 为清除。

### 删除已有记录

命令参考：

```bash
uv run repo-version-monitor --config config.toml delete --name grafana [--repository grafana/grafana] [--branch 13.0]
```

支持参数 `--name` 指定，若存在同名时报错退出；支持 `--repository` 精确删除，同仓库多分支时再加 `--branch` 缩小范围，删除后配置哈希会同步更新，数据库中该记录及其事件在下次哈希对比时触发自动清理。

### 查看当前追踪全部仓库列表

命令参考：

```bash
uv run repo-version-monitor --config config.toml list
```

默认按产品名排序（不区分大小写，等同 `--sort-by-name`）；加 `--sort-by-repository` 可改为按 repository（次级按 branch）排序，两个参数互斥。

### 发送测试邮件

命令参考：

```bash
uv run repo-version-monitor --config config.toml mailtest [--ignore]
```

检查邮件配置并跟踪发送结果，若配置中指定关闭邮件功能 `mailgun.enabled = false`，可加 `--ignore` 参数忽略开关继续测试：

### 检查仓库标签

命令参考：

```bash
uv run repo-version-monitor --config config.toml check [--name grafana] [--only-blank]
```

支持参数 `--name` 只检查指定名称的仓库（存在同名条目时全部检查）

支持参数 `--only-blank` 只检查数据库中还没有版本记录的仓库（`list` 中状态为 `(not checked yet)` 的），支持与 `--name` 参数组合使用。

### 循环定时检查

命令参考：

```bash
uv run repo-version-monitor --config config.toml run
```

支持参数 `--interval` 覆盖配置中的查询间隔。

首次发现新产品时，默认只写入数据库，不发送邮件；这样可以避免初始化时收到一堆“更新”。如果希望首次也通知，把配置中的 `notify_on_first_seen` 改成 `true`。

## 测试

所需依赖 `pytest` 在 `dev` 可选依赖中，运行测试需带上 `extra`：

```bash
uv run --extra dev pytest tests/ -q
```

## 其他

### 配置变更自动清理

修改配置的命令（`add`、`format`）会把 `config.toml` 的 SHA-256 哈希记录到数据库；`check`、`list`、`run` 执行时对比该哈希，若不存在则创建，若发现配置文件已变化，会自动清理数据库中失效的数据——例如某产品已从配置移除，其 `products` 记录和相关 `tag_events` 会被一并删除。

### 容器化部署

镜像默认执行 `run`（内置定时循环），配合 restart 策略实现后台常驻。配置文件挂载到 `/config/config.toml`，数据库放在 `/data` 卷上——配置中需设置 `path = "/data/versions.sqlite3"`。

```bash
docker compose up -d --build
```

或手动运行：

```bash
docker build -t repo-version-monitor .
docker run -d --restart unless-stopped \
  -e GITHUB_TOKEN="github_pat_xxx" -e MAILGUN_API_KEY="key-xxx" \
  -v ./config.toml:/config/config.toml:ro \
  -v rvm-data:/data \
  repo-version-monitor
```

### 关闭邮件通知

把配置中的 `[mailgun]` 段的 `enabled` 改成 `false` 即可关闭邮件通知（默认为 `true`）。

关闭后：

- 检查逻辑照常运行，版本变化仍会写入数据库（`tag_events` 中 `notified_at` 保持为 NULL）；
- 不再需要设置 `MAILGUN_API_KEY` 及其他 mailgun 配置项。

### 补发漏掉的通知

发信失败或关闭通知期间检测到的更新，都会以 `notified_at IS NULL` 留在数据库里。重新开启通知后，可以一次性补发：

```bash
repo-version-monitor --config config.toml resend
```

补发成功后事件会被标记为已通知，重复执行不会重复发信。
