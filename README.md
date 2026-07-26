# Repo Version Monitor

定时检查 GitHub 仓库 tags，与本地 SQLite 中保存的版本对比；发现新 tag 后，通过 Mailgun API 使用 `httpx` 发送邮件通知。

## 安装

```bash
python -m pip install -e .
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
