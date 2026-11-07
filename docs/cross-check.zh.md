# 与另一个观察者交叉核对

[English](cross-check.md) · [Русский](cross-check.ru.md) · [Español](cross-check.es.md) · [Français](cross-check.fr.md) · [中文](cross-check.zh.md)

读取 MCP Registry 中服务器工具列表的服务不只有 HISTOR。CrawlCheck（crawlcheck.io）也在读取，并且从 2026 年 11 月 7 日起
发布与 HISTOR 相同的工具集摘要。两个观察者在大致相同的时间读取同一台服务器、各自独立计算摘要，就能互相核对：出现分歧，
说明服务器给其中一方返回了不同的内容（例如按客户端或地区返回不同结果），或者其中一方有缺陷。

## 摘要

它就是 MTL/1 的工具集摘要（[labels.zh.md](labels.zh.md)）。每个工具取：`name`、`description`（缺失时为 `""`）、
`inputSchema`（缺失时为 `{}`），以及仅当服务器发送了对象时的 `outputSchema`；工具按名称的 UTF-16 码元排序；按 RFC 8785
生成规范 JSON；SHA-256，写成 SRI 字符串。CrawlCheck 在其 lockfile 规范中称之为 `jcs_v1.tools_sha256`
（https://crawlcheck.io/spec/mcp-lockfile ）。

2026 年 11 月 7 日核对：CrawlCheck 公布的两个测试向量、由 CrawlCheck 服务计算的六个取自 HISTOR 记录的工具集，以及
CrawlCheck 自己的服务器，双方得到的摘要都相同。`annotations`、`title` 和 `icons` 不在这个摘要中。两个服务都把它们放在
第二个摘要里，但构造方式不同，因此这两个摘要并不一致，也不做比较。

## 每日运行

每天 UTC 12:00，HISTOR 读取 CrawlCheck 的公开变更流（https://crawlcheck.io/api/v1/mcp/changes ，最新的 500 个事件，
约两天半），并把每个带有该摘要的事件与 HISTOR 对该服务器的时间线（`/api/v1/servers/<id>`）比较。结果发布在
https://histor.modelmarket.dev/datasets/xcheck-crawlcheck/ ，每天一个文件夹：读取时的原始变更流（`feed.input.json`）、
比较结果（`feed.report.json`、`feed.report.md`）和 `SHA256SUMS`。`latest/` 指向最新的一天。分歧原样发布，不做过滤。

| 结论 | 含义 |
|---|---|
| `agree` | 在对方读取的时刻，HISTOR 的摘要与之相同（落在 HISTOR 两次读取之间时，两次中任一次都算） |
| `agree_nearby` | 不是 HISTOR 当时的摘要，但 HISTOR 在 24 小时内见过它：服务器在两次读取之间发生了变化 |
| `disagree` | HISTOR 在 24 小时内有摘要，但从未见过这一个 |
| `histor_no_digest` | HISTOR 在 24 小时内没有该服务器的摘要（未读取，或 MTL/1 无法计算摘要的列表） |
| `not_listed` | HISTOR 未收录这个名称和地址 |
| `bad_row` | 字段缺失或无法读取 |

之所以需要 24 小时的窗口，是因为有些服务器每次被读取时列表都会变化：11 月 7 日试过的七台服务器中，有四台大约每 30 分钟
就得到一个新的摘要。这时两个观察者只能“相近”一致。

## 自己运行

```bash
python3 scripts/xcheck_digests.py https://crawlcheck.io/api/v1/mcp/changes --out report.json --md report.md
```

需要 Python 3.11 或更高版本，只用标准库。输入是文件或 https 地址：按原样发布的 CrawlCheck 变更流，或任何以 JSON、
JSON Lines 或 CSV 表示的行列表，字段为 `name`（注册表中的名称）、`endpoint`、`digest`（SRI 或十六进制）和 `read_at`
（RFC 3339）。退出码 0 表示没有 `disagree`，1 表示至少有一个，2 表示无法读取输入。

## 首次结果

2026 年 11 月 7 日，对变更流的首次运行：8 个带摘要的事件，7 个 `agree`，1 个 `histor_no_digest`，0 个 `disagree`。
