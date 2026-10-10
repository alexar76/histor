# HISTOR

<!-- aicom-readme-badges -->
<p align="center">
  <a href="https://github.com/alexar76/histor/actions/workflows/ci.yml"><img src="https://github.com/alexar76/histor/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://github.com/alexar76/histor/actions/workflows/pages.yml"><img src="https://github.com/alexar76/histor/actions/workflows/pages.yml/badge.svg" alt="Pages" /></a>
  <a href="https://histor.modelmarket.dev/"><img src="https://img.shields.io/website?url=https%3A%2F%2Fhistor.modelmarket.dev%2Fhealth&label=live&up_message=up&down_message=down" alt="Live service" /></a>
  <a href="https://histor.modelmarket.dev/log"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fhistor.modelmarket.dev%2Fapi%2Fv1%2Fbadges%2Flog" alt="Labels in the log (live)" /></a>
  <a href="https://histor.modelmarket.dev/servers"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fhistor.modelmarket.dev%2Fapi%2Fv1%2Fbadges%2Fpinned" alt="Tool sets pinned (live)" /></a>
  <a href="https://histor.modelmarket.dev/changes"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fhistor.modelmarket.dev%2Fapi%2Fv1%2Fbadges%2Fchanges" alt="Changes in the last 7 days (live)" /></a>
  <a href="https://histor.modelmarket.dev/log"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fhistor.modelmarket.dev%2Fapi%2Fv1%2Fbadges%2Fsth" alt="Age of the last signed tree head (live)" /></a>
  <a href="https://github.com/alexar76/histor/actions/workflows/pages.yml"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Falexar76.github.io%2Fhistor%2Fbadges%2Ftests.json" alt="Tests (measured by CI)" /></a>
  <a href="https://github.com/alexar76/histor/actions/workflows/pages.yml"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Falexar76.github.io%2Fhistor%2Fbadges%2Fcoverage.json" alt="Branch coverage (measured by CI)" /></a>
  <img src="https://img.shields.io/badge/python-%3E%3D3.11-3776AB" alt="Python >=3.11" />
  <img src="https://img.shields.io/badge/store-Postgres%20%C2%B7%20SQLite%20dev-336791" alt="Postgres in production, SQLite for development" />
  <img src="https://img.shields.io/badge/labels-MTL%2F1%20on%20AWR%2F2-8d83ff" alt="MTL/1 labels on AWR/2" />
  <img src="https://img.shields.io/badge/log-RFC%209162-5fe3ff" alt="RFC 9162 Merkle log" />
  <img src="https://img.shields.io/badge/docs-EN%20RU%20ES%20FR%20ZH-9c70ff" alt="Documentation in 5 languages" />
  <a href="https://github.com/alexar76/histor/blob/main/LICENSE"><img src="https://raw.githubusercontent.com/alexar76/histor/refs/heads/main/docs/badges/license.svg" alt="License: MIT" /></a>
</p>
<!-- /aicom-readme-badges -->

<p align="center">
  <strong>HISTOR</strong>（ἵστωρ，“因亲眼所见而知晓的人”）—— MCP 工具定义的公开透明日志<br>
  官方注册表中每个远程 MCP 端点公布过什么 · 何时发生了变更 · 已签名、仅追加、可审计
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.ru.md">Русский</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.fr.md">Français</a> ·
  <a href="README.zh.md"><b>中文</b></a>
</p>

> [WARDEN 质量检查周期：MOMUS → AI-Factory → SKOPOS → 共享节点智能体 → 部署](../momus/docs/quality-cycle.zh.md).

**在线服务：** [histor.modelmarket.dev](https://histor.modelmarket.dev) ·
**落地页：** [alexar76.github.io/histor](https://alexar76.github.io/histor/) ·
**能力：** `histor.check@v1` · `histor.server@v1` · `histor.changes@v1` ·
**端口：** `9490`

## 一段话说清问题

一个 MCP 服务器可以先用无害的工具描述通过您的审查，之后再改掉它们——提示注入只需要描述里多出一句话，而您的客户端会把它原样交给模型，不会再问您一次。MCP 没有针对工具定义的内容寻址，官方注册表验证的是命名空间而不是内容，而您笔记本电脑上的扫描器只能看到服务器展示给这台电脑的东西。HISTOR 就是缺失的那份公共记忆：它记录每个服务器公布过什么，对其签名，并告诉任何来问的人：他们收到的，是否就是其他所有人看到的。

## 它做什么

```mermaid
flowchart LR
    R["官方 MCP 注册表<br/>约 2.1 万个服务器"] --> C["抓取程序<br/>initialize + tools/list<br/>从不调用工具"]
    C --> D["MTL/1 摘要<br/>RFC 8785 + SHA-256"]
    D --> L["四种签名标签<br/>AWR/2 · did:key"]
    W["WARDEN 边车<br/>已发布的模式集"] --> L
    L --> M["默克尔日志<br/>RFC 9162"]
    M --> S["签名树头<br/>每次抓取之后"]
    M --> API["网页端 · API · 徽章 · Atom 订阅源"]
    CL["您的客户端"] -- "它收到的工具" --> K["/api/v1/check"]
    K -- "签名应答：相同 · 不同 · 此前见过" --> CL
    K -. "选择加入的摘要计数" .-> M
```

- **读取**官方注册表中的每个远程端点：先 `initialize`，再 `tools/list`，并翻完所有分页。
  不调用任何工具，不安装也不执行任何东西，私有地址在建立连接之前即被拒绝。
- **另外读取**一份注册表未收录的热门远程服务器短名单（[`histor/curated.json`](histor/curated.json)，其中包括 DeepWiki），使用 HISTOR 自己的命名空间 `urn:awr:mtl:1:registry:histor.modelmarket.dev/curated`，因此任何标签都不会声称服务器在注册表中登记。一旦这类服务器发布到注册表，就以注册表的条目取代我们的条目。
- **运行 npm 和 PyPI 软件包**——人们用 `npx` 或 `uvx` 在本地启动的服务器——于[沙箱](docs/operations.zh.md#软件包沙箱)中：安装时不运行其代码，在带诱饵凭据、无对外路由的 gVisor 中启动，用 `initialize` + `tools/list` 读取，然后在沙箱内用金丝雀参数把每个工具各调用一次。gVisor 自己的跟踪记录说明软件包做了什么——启动了哪些程序、查询了哪些名称、打开了哪些诱饵——分别在安装时、启动时和工具被调用时。每个已发布版本一次；新版本若改变了其工具对模型所说的内容，就像任何端点一样，在日志中记为一次变更。
- **计算摘要**：严格按 [MTL/1](https://github.com/alexar76/aicom/blob/main/awr/adoption/mcp-trust-label/PROFILE.md)
  的定义处理工具集——名称、描述、输入与输出 schema，按 UTF-16 码元排序，RFC 8785。
- **签名**四种标签，每种都是一份独立的 AWR/2 `VerificationVerdict`：观测到了什么、WARDEN 模式集
  匹配到了什么、定义自上一个标签以来是否变更，以及名称是否在威胁列表上。
- **追加**：每个标签都追加进 RFC 9162 默克尔日志，每次抓取之后签发一个签名树头（STH），因此
  一致性证明可以表明这段历史只被追加过。
- **应答** `/check`：发来您的客户端收到的工具，取回一份签名应答，说明 HISTOR 在该端点观测到的
  是同一个工具集、更早的某个工具集，还是都不是。

## 标签不说明什么

该一致性档次（profile）禁止使用“安全”“受保护”“已审计”“已认证”“已批准”和“可信”这些词，本 README
同样不用。模式匹配是去阅读定义的理由，而不是一项发现。四种方法中有三种永远不会返回 `fail`；第四种
（连续性）只在两个摘要不同这一机械意义上返回 `fail`。不读取源码，不解析软件包，不观测行为。变更以
日期和差异呈现，绝不作为指控。详见：[docs/labels.zh.md](docs/labels.zh.md)。

## 快速开始

```bash
cd histor
npm ci --prefix scanner                     # the WARDEN sidecar (node >= 20)
uv sync --extra dev --project .
HISTOR_CRAWL_LIMIT=40 uv run --project . python -m histor crawl   # observe 40 endpoints
uv run --project . python -m histor serve   # desk + API on :9490 (SQLite under ./data)
```

生产环境运行在 Postgres 上，没有 Postgres 时 `HISTOR_PROFILE=prod` 拒绝启动：

```bash
HISTOR_POSTGRES_PASSWORD=… HISTOR_OPERATOR_TOKEN=… HISTOR_PUBLIC_BASE=https://histor.example \
  docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
```

Schema 变更以编号迁移的形式进行（`python -m histor migrate up|status`），在服务开始监听之前应用；
见 [docs/operations.zh.md](docs/operations.zh.md)。

## 向日志提问

```bash
# Is what my client received what HISTOR observed at that endpoint?
curl -sS https://histor.modelmarket.dev/api/v1/check -H 'Content-Type: application/json' \
  -d '{"endpoint":"https://example.com/mcp","tools":[ …the tools/list result… ]}'

# The signed tree head, and a proof that today's log extends yesterday's
curl -sS https://histor.modelmarket.dev/api/v1/log/sth
curl -sS "https://histor.modelmarket.dev/api/v1/log/proof/consistency?first=1000&second=1200"
```

所有端点见 [docs/api.zh.md](docs/api.zh.md)。如何在不信任 HISTOR 的前提下审计日志，见
[docs/log.zh.md](docs/log.zh.md)。内部结构见 [docs/architecture.zh.md](docs/architecture.zh.md)。

## 测试

```bash
make test          # unit tests; set HISTOR_TEST_DATABASE_URL to run each storage test on Postgres too
make integration   # real sockets: a loopback registry and MCP servers, uvicorn, the real WARDEN sidecar
```

上方的测试与覆盖率徽章由 CI 在每次 Pages 部署时测得，经 shields.io 读取；日志徽章读取的是在线
服务，因此那里的每个数字都是最新的。

## ERC-8004 身份

HISTOR 是 [Base 上的 `96683` 号](https://8004scan.io/agents/base/96683) ERC-8004 智能体，于 2026-10-01 在官方 IdentityRegistry `0x8004A169…a432` 中注册，归 AIMarket 运营者钱包 `0x1218ff36…Ad0a` 所有。它的[注册文件](https://modelmarket.dev/.well-known/erc-8004/histor.json)列出了网页、在线的 `histor-check` 端点，以及为每个标签签名的 `did:key`，因此标签的签名可以追溯到这个已注册的智能体。交易及其核对方法：[ERC-8004 身份](https://github.com/alexar76/aicom/blob/main/docs/erc-8004-identities.zh.md)。

## 所处位置

| 组件 | 角色 |
|---|---|
| [WARDEN](https://github.com/alexar76/warden) | 在客户端、于连接时检查工具定义 |
| **HISTOR** | 公开地记住服务器公布过什么 |
| [THEMIS](https://github.com/alexar76/themis) | 在 Hub 上于发布时准入一项能力 |
| [AIMarket Hub](https://modelmarket.dev) | 在目录中上架 `histor.check@v1` |
| [AWR](https://github.com/alexar76/aicom/tree/main/awr) | 每个标签所用的签名文档格式 |

## 许可证

MIT——见 [LICENSE](LICENSE)。属于 [AIMarket 生态](https://modeldev.modelmarket.dev)的一部分。
