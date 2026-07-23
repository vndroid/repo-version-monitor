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

添加一个公开仓库：

```bash
uv run repo-version-monitor --config config.toml add encode/httpx --name httpx
```

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
