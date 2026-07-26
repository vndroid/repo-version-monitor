# Repo Version Monitor

定时检查 GitHub 仓库 tags，与本地 SQLite 中保存的版本对比；发现新 tag 后，通过 Mailgun API 使用 `httpx` 发送邮件通知。

邮件发送使用 Mailgun API，具体可参考[官方手册](https://documentation.mailgun.com/docs/mailgun/user-manual/sending-messages/send-http)。

## 安装

```bash
python3 -m pip install -e .
```

## 配置

复制示例配置：

```bash
cp config.example.toml config.toml
```

设置密钥：

```bash
export GITHUB_TOKEN="github_pat_xxx"       # 可选，但建议设置，避免 GitHub 低限额
export MAILGUN_API_KEY="key-xxx"
```

密钥读取优先级（GitHub token 与 Mailgun API key 一致）：先读环境变量（变量名分别由 `token_env`、`api_key_env` 指定，默认 `GITHUB_TOKEN`、`MAILGUN_API_KEY`），不存在时回退到配置文件中的 `token` / `api_key` 字段。推荐使用环境变量，内联字段仅作为本地调试的便捷方式，注意不要将含密钥的配置文件提交到版本库。执行 `check` 时会输出密钥的实际来源（环境变量还是配置文件）。

## 使用

整理配置文件：

```bash
uv run repo-version-monitor --config config.toml format
```

`format` 会：config.toml 不存在时从同目录的 `config.example.toml` 复制一份；存在时校验格式是否合法，并规范化所有 `[[products]]` 块——块之间保留一个空行，未配置 `branch` 的产品补上 `branch = ""`（空值等同于未配置）。

添加一个公开仓库：

```bash
uv run repo-version-monitor --config config.toml add encode/httpx --name httpx
```

追踪某个特定分支线（如 PostgreSQL 13）：

```bash
uv run repo-version-monitor --config config.toml add postgres/postgres --name pg13 --branch v13
```

指定 `--branch v13` 后，会获取该仓库所有标签，只保留以 `v13` 或 `13` 开头的标签，并记录其中最新的一个。同一仓库可以用不同 `--branch` 添加多次。

查看当前监控的仓库：

```bash
uv run repo-version-monitor --config config.toml list
```

发送测试邮件（检查邮件配置并跟踪发送结果）：

```bash
uv run repo-version-monitor --config config.toml mailtest
```

若配置中 `mailgun.enabled = false`，可加 `--ignore` 忽略开关继续测试：

```bash
uv run repo-version-monitor --config config.toml mailtest --ignore
```

单次检查：

```bash
uv run repo-version-monitor --config config.toml check
```

循环定时检查：

```bash
uv run repo-version-monitor --config config.toml run
```

也可以覆盖配置中的间隔：

```bash
uv run repo-version-monitor --config config.toml run --interval 1800
```

首次发现某个产品时，默认只写入数据库，不发送邮件；这样可以避免初始化时收到一堆“更新”。如果希望首次也通知，把配置中的 `notify_on_first_seen` 改成 `true`。

## 测试

pytest 在 `dev` 可选依赖中，运行测试需带上 extra：

```bash
uv run --extra dev pytest tests/ -q
```

## 配置变更自动清理

修改配置的命令（`add`、`format`）会把 config.toml 的 SHA-256 哈希记录到数据库；`check`、`list`、`run` 执行时对比该哈希，若不存在则创建，若发现配置文件已变化，会自动清理数据库中失效的数据——例如某产品已从配置移除，其 `products` 记录和相关 `tag_events` 会被一并删除。

## 关闭邮件通知

把配置中的 `[mailgun]` 段的 `enabled` 改成 `false` 即可关闭邮件通知（默认为 `true`）。关闭后：

- 检查逻辑照常运行，版本变化仍会写入数据库（`tag_events` 中 `notified_at` 保持为 NULL）；
- 不再需要设置 `MAILGUN_API_KEY` 及其他 mailgun 配置项。

## 补发漏掉的通知

发信失败或关闭通知期间检测到的更新，都会以 `notified_at IS NULL` 留在数据库里。重新开启通知后，可以一次性补发：

```bash
repo-version-monitor --config config.toml resend
```

补发成功后事件会被标记为已通知，重复执行不会重复发信。
