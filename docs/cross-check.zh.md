# 与其他观察者交叉核对

[English](cross-check.md) · [Русский](cross-check.ru.md) · [Español](cross-check.es.md) · [Français](cross-check.fr.md) · [中文](cross-check.zh.md)

读取 MCP Registry 中服务器工具列表的服务不只有 HISTOR。独立读取同一批服务器的观察者可以互相核对：出现分歧，说明服务器
给它们返回了不同的内容（因客户端、地区或时间而异），或者其中一方有缺陷。HISTOR 按计划与这些观察者核对自己的记录，并把
每一次核对（包括分歧）发布在 https://histor.modelmarket.dev/datasets/peers/ 。

## 观察者

| 观察者 | 方法 | 计划 | 发布位置 | 起始日期 |
|---|---|---|---|---|
| CrawlCheck（crawlcheck.io） | 工具集摘要 | 每天 UTC 12:00 | `/datasets/peers/crawlcheck/` | 2026-11-07 |
| ScraperWatch（scraperwatch.com） | 按字段的变更 | 每天 UTC 12:00 | `/datasets/peers/scraperwatch/` | 2026-11-01 |

这个列表是一个配置文件：主机上的 `/etc/histor/peers.json`（本仓库中的 `deploy/peers.json`）。它的公开形式是
https://histor.modelmarket.dev/datasets/peers/index.json ：每个观察者、其方法、来源和计划，以及最近一次核对的日期。
每天一个文件夹 `/datasets/peers/<观察者>/<YYYY-MM-DD>/`，`latest/` 指向最新的一天。列表出现之前使用的地址
`/datasets/xcheck-crawlcheck/` 也指向同一位置。

## 添加观察者

发布 MTL/1 工具集摘要（见下文）的观察者，只需要在列表中加一条记录：

```json
{
  "slug": "example",
  "name": "Example Observer",
  "contact": "ops@example.org",
  "method": "digest",
  "window_hours": 24,
  "sources": [{"name": "list", "url": "https://example.org/histor-list.jsonl"}]
}
```

来源是观察者通过 https 发布的任何文件，每读取一次服务器一行：`name`（注册表中的名称）、`endpoint`、`digest`（SRI 或
十六进制）和 `read_at`（RFC 3339），格式为 JSON、JSON Lines 或 CSV。CrawlCheck 的变更流按原样读取。`digest` 为 null 并带
有 `outcome` 字段的行，表示观察者未能读取该服务器。`histor-peers.timer` 每天运行所有记录
（`deploy/peers_check.sh`、`histor/peers.py`）；见 [operations.zh.md](operations.zh.md)。以 ScraperWatch 格式发布按字段变更日志的观察者，使用方法 `field-diff`，参数参照 `deploy/peers.json` 中 ScraperWatch 的记录。

## 方法：工具集摘要

它就是 MTL/1 的工具集摘要（[labels.zh.md](labels.zh.md)）。每个工具取：`name`、`description`（缺失时为 `""`）、
`inputSchema`（缺失时为 `{}`），以及仅当服务器发送了对象时的 `outputSchema`；工具按名称的 UTF-16 码元排序；按 RFC 8785
生成规范 JSON；SHA-256，写成 SRI 字符串。CrawlCheck 在其 lockfile 规范中称之为 `jcs_v1.tools_sha256`
（https://crawlcheck.io/spec/mcp-lockfile ）。2026 年 11 月 7 日核对：CrawlCheck 的两个测试向量、由 CrawlCheck 服务计算的
六个取自 HISTOR 记录的工具集，以及 CrawlCheck 自己的服务器，双方得到的摘要都相同。

两个服务都把 `annotations`、`title` 和 `icons` 纳入单独的附加字段摘要。2026 年 11 月 7 日，
[CrawlCheck 报告](https://github.com/modelcontextprotocol/registry/issues/82) 30 个附加字段摘要一致，
其中 crawlcheck.io/mcp 的值为 `sha256-G44DGgW7…`。这一结果适用于当时的样本。比较附加字段摘要还取决于编码方式和存储限制。
每日运行只比较工具集摘要，因为两个来源都不提供附加字段摘要。

观察者发布的每一行都与 HISTOR 对该服务器的时间线（`/api/v1/servers/<id>`）比较：

| 结论 | 含义 |
|---|---|
| `agree` | 在对方读取的时刻，HISTOR 的摘要与之相同（落在 HISTOR 两次读取之间时，两次中任一次都算） |
| `agree_nearby` | 不是 HISTOR 当时的摘要，但 HISTOR 在窗口内见过它：服务器在两次读取之间发生了变化 |
| `disagree` | HISTOR 在窗口内有摘要，但从未见过这一个 |
| `histor_no_digest` | HISTOR 在窗口内没有该服务器的摘要（未读取，或 MTL/1 无法计算摘要的列表） |
| `not_listed` | HISTOR 未收录这个名称和地址 |
| `theirs_no_digest` | 对方观察者未能读取该列表（`digest` 为 null，原因见 `outcome`） |
| `bad_row` | 字段缺失或无法读取 |

之所以需要窗口（默认 24 小时），是因为有些服务器每次被读取时列表都会变化：11 月 7 日试过的七台服务器中，有四台大约每
30 分钟就得到一个新的摘要。这时两个观察者只能“相近”一致。

对 CrawlCheck，运行读取两个来源：其公开变更流（https://crawlcheck.io/api/v1/mcp/changes ，最新的 500 个事件，约两天半，
因此每天运行），以及其每周的共享列表（30 台服务器，每周一 UTC 11:30 之前重新读取；
https://crawlcheck.io/data/xcheck/histor/latest.jsonl ，带日期的各期文件旁附有签名声明）。每天的文件夹按来源保存读取时的
原始文件（`<来源>.input.*`）、比较结果（`<来源>.report.json`、`<来源>.report.md`）和 `SHA256SUMS`。

自己运行核对，只需 Python 3.11 或更高版本，无需安装任何东西：

```bash
python3 scripts/xcheck_digests.py https://crawlcheck.io/data/xcheck/histor/latest.jsonl --out report.json --md report.md
```

退出码 0 表示没有 `disagree`，1 表示至少有一个，2 表示无法读取输入。

首批结果，2026 年 11 月 7 日：共享列表第一期（sha256 `9271b8ac…`），30 台服务器，30 个 `agree`；变更流，16 行带摘要，
15 个 `agree`，1 个 `histor_no_digest`，0 个 `disagree`。

## 方法：按字段的变更

ScraperWatch 针对一个固定的远程服务器面板，每天发布工具变更日志，以 JSON 路径加新旧值的形式给出。每天，HISTOR 从两个方向核对该日志的最新一天：其日志中的每一项变更逐路径对照 HISTOR 的记录；HISTOR 在其面板上记录到而其日志未列出的每一项变更，
都给出原因（`histor/peer_xcheck.py`，[operations.zh.md](operations.zh.md)）。它读取 HISTOR 的数据库，因此在单独的容器中使用数据库设置运行；其参数（日志和面板地址、robots.txt 代理名、运行时间和
首次运行）写在列表中它的记录里。2026 年 11 月 7 日之前，它每周在数据集导出之后运行一次。
## 第四个见证者

自 2026 年 11 月 7 日起，CrawlCheck 还从其自己的网络以见证者身份（c2sp.org/tlog-witness）为 HISTOR 的检查点联署：
`crawlcheck.io/witness`，vkey `crawlcheck.io/witness+f6b056e2+BCYE6WcaJVekqxEETT9IF/pKvxP7yZM6dLW65YDWpQpE`。
它的联署出现在 https://histor.modelmarket.dev/log/checkpoint ，与此前已在联署的三个见证者并列。
