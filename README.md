# Repo Version Monitor

定时检查 GitHub / GitLab 仓库 tags，与本地 SQLite 中保存的版本对比；发现新 tag 后发送邮件通知。

邮件发送支持两种通道：Mailgun API（参考[官方手册](https://documentation.mailgun.com/docs/mailgun/user-manual/sending-messages/send-http)）与普通 SMTP，二选一。

## 配置

复制示例配置：

```bash
cp config.example.toml config.toml
```

设置密钥：

```bash
export GITHUB_TOKEN="github_pat_xxx"       # 可选，但建议设置，避免 GitHub 匿名限额
export GITLAB_TOKEN="glpat-xxx"            # 可选，仅追踪私有 GitLab 项目时需要
export MAILGUN_API_KEY="key-xxx"           # 走 Mailgun 时需要
export SMTP_PASSWORD="xxx"                 # 走 SMTP 时需要
```

密钥读取优先级（GitHub 令牌、GitLab 令牌与 Mailgun 令牌一致）：先读环境变量（变量名分别由 `token_env`、`token_env`、`api_key_env` 指定，默认 `GITHUB_TOKEN`、`GITLAB_TOKEN`、`MAILGUN_API_KEY`），不存在时回退到配置文件中的 `token` / `api_key` 字段。推荐使用环境变量，内联字段仅作为本地调试的便捷方式，注意不要将含密钥的配置文件提交到版本库。执行 `check` 时会输出密钥的实际来源（环境变量还是配置文件）。

### 邮件通道：Mailgun 或 SMTP

`[mailgun]` 与 `[smtp]` 各有一个 `enabled`，**只能开一个**——两个都开会在启动时报错，否则每次更新会收到两封重复邮件；两个都关则只记录数据库、不发信。

SMTP 配置：

```toml
[smtp]
enabled = false
host = ""
port = 587
# "starttls"（通常 587）/ "ssl"（通常 465）/ "none"（仅内网中继）
encryption = "starttls"
username = ""
password = ""
from_email = ""
to_emails = []
```

- `encryption` 三选一：`starttls` 明文连接后升级加密，`ssl` 从第一个字节就是 TLS（SMTPS），`none` 完全不加密，只适合内网中继；
- `username` 留空表示免认证中继，不会尝试登录；填了 `password` 却没填 `username` 会报错；
- 密码读取优先级与其他密钥一致：先读环境变量 `SMTP_PASSWORD`（变量名可用 `password_env` 改），再回退到配置里的 `password`。`check` 与 `mailtest` 会打印实际来源；
- `host` 只写主机名，带 `://` 或路径会报错；
- 用 QQ / 163 这类邮箱时注意填的是**授权码**而不是登录密码，且它们通常用 465 + `ssl`。

发信实现用标准库 `smtplib`，在线程里执行，不引入额外依赖。**注意 `[proxy]` 只作用于 HTTP 请求（GitHub / GitLab / Mailgun），SMTP 不走代理。**

`mailtest` 会自动测试当前启用的那个通道：

```bash
uv run repo-version-monitor --config config.toml mailtest
```

两个通道都关闭时加 `--ignore` 仍可测试——此时若 `[smtp] host` 有值就测 SMTP，否则测 Mailgun。

### 代理

所有外发 HTTP 请求（GitHub、GitLab、Mailgun）都可以走代理，支持 http 与 socks5（SMTP 不经过代理）：

```toml
[proxy]
enabled = false
# "http" 或 "socks5"
type = "http"
host = ""
port = 8080
# 可选的代理认证
username = ""
password = ""
```

- `host` 只写主机名或 IP，协议由 `type` 决定，写成 `http://127.0.0.1` 会报错；
- `username` 为空时按免认证处理，只填 `password` 会报错；
- `enabled = false` 时不做任何校验，其余字段填错也不影响运行；
- `enabled = false` 时保持 httpx 默认行为，仍然尊重 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` 环境变量；`enabled = true` 时一律使用配置里的代理，忽略环境变量；
- socks5 下 DNS 解析同样走代理：目标域名原样发给代理，由代理所在网络解析，本机不做解析。

`check` 与 `mailtest` 会打印当前生效的代理，便于确认：

```
Proxy: socks5://127.0.0.1:1080 (authenticated)
```

使用 socks5 需要 `socksio`，已通过 `httpx[socks]` 声明在依赖中，`uv sync` 会自动安装。

## 供应商（provider）

每个产品都有一个 `provider` 字段，留空或缺省均为 `github`，因此旧配置无需改动即可继续使用。目前支持：

| provider | 接口 | 仓库写法 |
| --- | --- | --- |
| `github` | GraphQL + REST | `owner/name` |
| `gitlab` | [REST API v4](https://docs.gitlab.com/api/tags/) | `namespace/project`，支持子组 `group/subgroup/project` |

`add` 命令可以直接粘贴完整仓库地址，由域名自动判断 provider，详见[仓库参数的写法](#仓库参数的写法)。

**GitHub**：获取标签时采用分层策略，配置了令牌时走 GraphQL API（按 tag 提交时间倒序拉取全量）；未配置令牌或 GraphQL 失败时回退到 REST API 接口（该接口无时间排序，程序会筛选符合版本号形态的标签并按数值取最大）。

**GitLab**：调用 `GET /api/v4/projects/:id/repository/tags`，项目路径会做 URL 编码后作为 `:id`，因此支持多级子组；请求参数固定为 `order_by=updated&sort=desc`，按更新时间倒序翻页拉取全量。公开项目无需令牌；私有项目通过 `PRIVATE-TOKEN` 请求头认证。

`[gitlab]` 段只针对官方的 gitlab.com 实例，其中只有 `token` 一项。

### 版本号识别规则

API 返回的顺序不可靠，程序会在全部标签里挑数值最大的**正式版本**。默认只认纯版本号：

| 标签                                    | 结果     | 说明                                                |
|-----------------------------------------|----------|-----------------------------------------------------|
| `v1.2.3` / `1.2.3`                      | 参与比较 | `v` 前缀可有可无，按数值比较，`1.10.0` 高于 `1.9.0` |
| `1.7.0-rc1`、`v11.3.0.pre`、`v1.2.3-ee` | 忽略     | 带任何后缀都不算纯版本号                            |
| `flash-with-wbuf-stack`                 | 忽略     | 不是版本号                                          |

有些项目的正式版本天生带后缀，此时用产品级的 `suffix` 指定，详见[版本后缀 suffix](#版本后缀-suffix)。

同一仓库路径在不同 provider、不同实例下互不冲突，会被视为彼此独立的记录。

### 版本后缀 suffix

有些项目的正式版本号带固定后缀，典型的是 GitLab：`gitlab-org/gitlab` 自 12.0 CE/EE 合并后**只打 `-ee` 标签**（`v19.2.2-ee`），纯版本号 tag 停在 2018 年的 `v11.2.2`。这类项目用产品级的 `suffix` 指定要跟踪的后缀：

```toml
[[products]]
name = "gitlab"
provider = "gitlab"
external_url = ""
token = ""
repository = "gitlab-org/gitlab"
branch = ""
suffix = "-ee"
```

规则很简单：**先按后缀筛选，再比较版本号**。

- 只有以该后缀结尾的标签参与比较，去掉后缀后剩下的部分必须是纯版本号；
- 因此 `v19.2.2-ee` 参与比较（剩 `v19.2.2`），而 `v19.2.0-rc44-ee` 不会（剩 `v19.2.0-rc44`），预发布依旧被自然排除；
- 留空表示只认纯版本号 `v1.2.3`，与旧行为一致；
- 记录和邮件里保留标签原名（去掉 `v` 前缀），即 `19.2.2-ee`。

#### 同时跟踪多个后缀

用 `|` 分隔多个后缀，**靠前的优先**：

```toml
suffix = "-ee|-ce"
```

含义是「以 `-ee` 或 `-ce` 结尾的标签都参与比较」，因此同一个产品可以同时盯企业版和社区版，取两者中版本号最大的那个。版本号相同时（`v19.2.3-ee` 与 `v19.2.3-ce` 并存）取写在前面的 `-ee`，把顺序写成 `-ce|-ee` 则相反。

注意每个分隔出来的部分都是**字面后缀**而不是正则片段：`.` 就是点本身，不会当成通配符，因此 `.Final` 只匹配 `.Final`。写空的分支（`-ee|`、`-ee||-ce`）会报错，多半是笔误。

`list` 的 `SUFFIX` 列会显示每条记录跟踪的后缀，未配置显示 `/` 或 `-`。

**suffix 不属于产品身份**：它只决定读取同一个仓库的哪些标签，因此不参与配置去重，也不在数据库主键里。带来两个后果：

- 给已有产品加上或改掉 suffix 时，数据库里的记录照旧沿用。例如 `gitlab` 原先记的是 `11.2.2`，配上 `suffix = "-ee"` 后下次检查会作为一次正常更新处理，收到一封 `11.2.2 → 19.2.3-ee` 的邮件；
- 同一仓库（同 provider、同实例、同 branch）在配置里只能出现一次。要同时跟踪多个后缀不必加第二条记录，写成 `suffix = "-ee|-ce"` 即可，见上。

### self-managed GitLab 实例

自建实例配置在**产品自己**的 `[[products]]` 块里，因此可以同时监控 gitlab.com 和任意多个自建实例：

```toml
[[products]]
name = "example"
provider = "gitlab"
external_url = "https://jihulab.com"
token = ""
repository = "example/project"
branch = ""
```

- `external_url`：实例的完整 URL，决定去哪台 GitLab 拉 tag。留空表示官方实例（github.com / gitlab.com）；
- `token`：该实例的访问令牌，可选，留空则匿名访问。令牌只发给自己实例，不会与 `[gitlab]` 段的 gitlab.com 令牌互相串用；
- `provider = "github"` 的产品不能填 `external_url` / `token`，GitHub Enterprise 暂未支持。

用 `add` 添加时见[添加公开仓库](#添加公开仓库)一节的 `--external-url` 参数。

### 新增一个供应商

供应商代码集中在 `src/repo_version_monitor/providers/` 下，每个供应商一个模块：

- `base.py`：共用的 `Tag` 数据类、`TagProvider` 协议以及标签筛选/取最大版本等工具函数；
- `github.py` / `gitlab.py`：各自的客户端，只需实现 `async fetch_tags(client, repository) -> list[Tag]`。

新增供应商时：写一个新模块 → 在 `providers/__init__.py` 的 `SUPPORTED_PROVIDERS` 中登记名字 → 在 `monitor.VersionMonitor.providers` 字典中注册客户端实例。配置校验、数据库和 CLI 都按 provider 名字通用处理，无需改动。

## 使用

### 整理配置文件

命令参考：

```bash
uv run repo-version-monitor --config config.toml format
```
当 `config.toml` 不存在时从同目录模板配置 `config.example.toml` 复制一份；

存在则做三件事：

1. 校验格式是否合法；
2. **补全缺失的配置项**：`[database]`、`[github]`、`[gitlab]`、`[mailgun]`、`[smtp]`、`[monitor]`、`[proxy]` 各段中缺失的键会补上默认值，整段缺失时补上整段。已有的值、键顺序和注释都原样保留，只在所属段末尾追加缺失项，命令执行后会列出补了哪些项；
3. 规范化所有 `[[products]]` 块，产品之间留空行，为未配置 `provider`、`external_url`、`token`、`branch`、`suffix` 的产品补空值。

补全时字符串类配置一律写成空值 `""`，含义是"用内置默认值"，因此不必手填也不会覆盖你已经写好的值：

| 配置项 | 留空时的默认值 |
| --- | --- |
| `products.provider` | `github` |
| `products.branch` | 不限分支，跟踪全部标签 |
| `database.path` | `versions.sqlite3` |
| `github.token` / `gitlab.token` / `mailgun.api_key` | 视为未设置，回退到环境变量 |
| `products.external_url` | 官方实例（github.com / gitlab.com） |
| `products.token` | 匿名访问该实例 |
| `products.suffix` | 只认纯版本号 `v1.2.3` |
| `mailgun.api_url` | `https://api.mailgun.net/v3` |
| `smtp.port` | `587` |
| `smtp.encryption` | `starttls` |
| `smtp.username` / `smtp.password` | 免认证，密码回退到 `SMTP_PASSWORD` |
| `proxy.type` | `http` |
| `proxy.port` | `8080` |

`format` 是幂等的，重复执行不会再产生改动。

### 添加公开仓库

命令参考：

```bash
## 以仓库 https://github.com/encode/httpx 为例
uv run repo-version-monitor --config config.toml add encode/httpx [--name httpx] [--branch 0.28]

## 以 GitLab 项目 https://gitlab.com/gitlab-org/gitlab-runner 为例
uv run repo-version-monitor --config config.toml add gitlab.com/gitlab-org/gitlab-runner
```

默认截取仓库名作为产品名，可用 `--name` 参数指定自定义名称。支持 `--branch` 参数指定分支，支持存在同产品的不同分支，若不指定则默认跟踪所有标签。

#### 仓库参数的写法

仓库参数既接受 `owner/name` 路径，也接受完整域名，协议可带可不带（不带时按 `https://` 处理），下面这些写法等价：

```bash
add encode/httpx                                        # 不含域名，默认 github.com
add github.com/encode/httpx                             # 不含协议
add https://github.com/encode/httpx.git
add https://github.com/encode/httpx/releases/tag/0.28.1 # 浏览器里复制的地址
add git@github.com:encode/httpx.git                     # git remote 写法
```

带域名时按域名自动判断 provider：`github.com` → `github`，`gitlab.com` → `gitlab`；GitLab 页面地址中 `/-/` 之后的部分（如 `/-/tags`）会被自动去掉，子组路径 `group/subgroup/project` 保留。不带域名时默认 `github.com`，所以旧写法行为不变。

#### `--provider` 参数

`--provider` 可选 `github`（默认）或 `gitlab`，用于域名无法判断 provider 的场景，典型的就是 self-managed GitLab：

```bash
uv run repo-version-monitor --config config.toml add git.example.com/group/subgroup/project --provider gitlab
```

几点约定：

- 域名不是已知的公开实例（既不是 `github.com` 也不是 `gitlab.com`）且没传 `--provider` 时直接报错，不会静默按 `github` 处理；
- `--provider` 与域名推断结果冲突时（如 `--provider github gitlab.com/a/b`）报错退出；
- GitHub 侧只支持 `github.com`，GitHub Enterprise 暂未支持。

#### `--external-url` 参数（self-managed 实例）

添加自建 GitLab 实例上的项目时用 `--external-url` 指定实例地址，同时必须指定 `--provider`——光有地址无法判断该用哪套 API：

```bash
## 协议可省略，省略时按 https:// 处理
uv run repo-version-monitor --config config.toml add gitlab-org/gitlab-runner --provider=gitlab --external-url=jihulab.com

## 需要 http:// 时显式写出
uv run repo-version-monitor --config config.toml add group/sub/project --provider=gitlab --external-url=http://git.example.com

## 私有实例可用 --token 指定令牌，不写则匿名访问
uv run repo-version-monitor --config config.toml add group/sub/project --provider=gitlab --external-url=git.example.com --token=glpat-xxx
```

地址也可以直接写在仓库参数里，效果相同，不必重复写 `--external-url`：

```bash
uv run repo-version-monitor --config config.toml add https://jihulab.com/gitlab-org/gitlab-runner --provider=gitlab
```

两者都给且域名不一致时报错退出。`--external-url` 指向公开实例（如 `gitlab.com`）时视为官方实例，不会写进配置。

#### `--suffix` 参数（版本后缀）

```bash
uv run repo-version-monitor --config config.toml add gitlab.com/gitlab-org/gitlab --suffix=-ee

## 多个后缀用 | 分隔，靠前的优先
uv run repo-version-monitor --config config.toml add gitlab.com/gitlab-org/gitlab --suffix='-ee|-ce'
```

注意要写成 `--suffix=-ee` 这种等号形式：值以 `-` 开头，写成 `--suffix -ee` 会被 argparse 当成另一个选项；含 `|` 时记得加引号，否则会被 shell 当作管道。含义见[版本后缀 suffix](#版本后缀-suffix)。

### 修改追踪分支

命令参考：

```bash
uv run repo-version-monitor --config config.toml edit grafana --branch 13.0
```

支持参数 `--name` 指定，存在重名时可用 `--repository` 精确指定，赋空值 `--branch ""` 为清除。

### 删除已有记录

命令参考：

```bash
uv run repo-version-monitor --config config.toml delete --name grafana [--repository grafana/grafana] [--branch 13.0] [--provider gitlab] [--external-url jihulab.com]
```

支持参数 `--name` 指定，若存在同名时报错退出；支持 `--repository` 精确删除，同仓库多分支时再加 `--branch` 缩小范围，同路径跨供应商时可加 `--provider` 区分，同路径跨实例时可加 `--external-url` 区分，删除后配置哈希会同步更新，数据库中该记录及其事件在下次哈希对比时触发自动清理。

### 查看当前追踪全部仓库列表

命令参考：

```bash
uv run repo-version-monitor --config config.toml list
```

默认按产品名排序（不区分大小写，等同 `--sort-by-name`）；加 `--sort-by-repository` 可改为按 repository（次级按 branch）排序，两个参数互斥。输出中的 `PROVIDER` 列标明每条记录来自哪个供应商，`SUFFIX` 列标明跟踪的版本后缀（未配置显示 `/`）；self-managed 实例上的项目在 `REPOSITORY` 列带上实例域名（如 `jihulab.com/example/project`），与 `add` 的写法一致。

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

### 配置项变更

| 原写法 | 现写法 | 说明 |
| --- | --- | --- |
| `[mailgun] base_url` | `[mailgun] api_url` | 同名不同义，按用途改名。EU 账号填 `https://api.eu.mailgun.net/v3` |
| `[gitlab] base_url` / `[gitlab] external_url` | `[[products]] external_url` | 实例地址下放到产品级，从而支持同时监控多个自建实例 |
| `[proxy] enable` | `[proxy] enabled` | 与 `[mailgun] enabled` 统一：配置描述状态，用形容词形式 |

旧写法不会被静默忽略——那样会悄悄回退到默认值，把请求发去错误的地址。`load_config` 和 `format` 都会报错并提示新写法（附带原值方便直接复制）：

```
[mailgun] base_url has been renamed to api_url; rename the key, e.g. api_url = "https://api.eu.mailgun.net/v3".
[gitlab] external_url belongs to the product it applies to now; remove it and set external_url = "https://git.example.com" in the [[products]] block of each self-managed project.
```

### 配置变更自动清理

修改配置的命令（`add`、`format`）会把 `config.toml` 的 SHA-256 哈希记录到数据库；`check`、`list`、`run` 执行时对比该哈希，若不存在则创建，若发现配置文件已变化，会自动清理数据库中失效的数据——例如某产品已从配置移除，其 `products` 记录和相关 `tag_events` 会被一并删除。

### 数据库自动迁移

`products`、`tag_events` 两张表的主键为 `(provider, external_url, repository, branch)`，其中 `external_url` 为空串表示官方实例——因此同一仓库路径在两个自建实例上是两条独立记录，版本不会互相覆盖。`suffix` 不在主键里，改动它不会重置已记录的版本。

旧数据库在下次运行时自动迁移，缺失的列按当时的语义补齐（`provider` 记为 `github`，`external_url` 记为官方实例），无需手动处理，也不会丢失历史事件。

### 容器化部署

镜像默认执行 `run`（内置定时循环），配合 restart 策略实现后台常驻。配置文件挂载到 `/config/config.toml`，数据库放在 `/data` 卷上——配置中需设置 `path = "/data/versions.sqlite3"`。

```bash
docker compose up -d --build
```

或手动运行：

```bash
docker build -t repo-version-monitor .
docker run -d --restart unless-stopped \
  -e GITHUB_TOKEN="github_pat_xxx" -e GITLAB_TOKEN="glpat-xxx" -e MAILGUN_API_KEY="key-xxx" \
  -v ./config.toml:/config/config.toml:ro \
  -v rvm-data:/data \
  repo-version-monitor
```

### 关闭邮件通知

把 `[mailgun]` 与 `[smtp]` 两段的 `enabled` 都改成 `false` 即可关闭邮件通知。

关闭后：

- 检查逻辑照常运行，版本变化仍会写入数据库（`tag_events` 中 `notified_at` 保持为 NULL）；
- 不再需要设置 `MAILGUN_API_KEY` 及其他 mailgun 配置项。

### 补发漏掉的通知

发信失败或关闭通知期间检测到的更新，都会以 `notified_at IS NULL` 留在数据库里。重新开启通知后，可以一次性补发：

```bash
repo-version-monitor --config config.toml resend
```

补发成功后事件会被标记为已通知，重复执行不会重复发信。
