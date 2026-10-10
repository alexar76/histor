# API

> 🌐 [English](api.md) · [Русский](api.ru.md) · [Español](api.es.md) · [Français](api.fr.md) · **中文**

基础 URL：`https://histor.modelmarket.dev`。除 `POST /api/v1/check`（限速）和运营方路由外，一切都是
公开、无需鉴权且只读的。公开读取接口会发送 `Access-Control-Allow-Origin: *`。OpenAPI：
[`/api/openapi.json`](https://histor.modelmarket.dev/api/openapi.json)。

```mermaid
flowchart LR
    subgraph Read["读取（GET，公开）"]
        S["/api/v1/servers<br/>/api/v1/servers/&lt;id&gt;"]
        C["/api/v1/changes · /feed.xml"]
        L["/api/v1/labels/&lt;id&gt;<br/>/api/v1/labels/&lt;id&gt;/proof"]
        E["/api/v1/toolsets/&lt;sri&gt;<br/>/api/v1/descriptors/&lt;sri&gt;<br/>/api/v1/pattern-sets/&lt;sri&gt;<br/>/api/v1/record-sets/&lt;sri&gt;"]
        G["/api/v1/log/sth · /log/entries<br/>/log/proof/inclusion · /log/proof/consistency"]
        ST["/api/v1/stats · /stats/hosts<br/>/api/v1/badges/&lt;name&gt; · /badge/&lt;id&gt;.svg"]
    end
    subgraph Write["写入"]
        K["POST /api/v1/check<br/>限速"]
        A["POST /api/v1/admin/crawl<br/>x-histor-operator"]
        F["POST /ai-market/v2/invoke<br/>Hub 联邦"]
    end
```

## 服务器

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/api/v1/servers?q=&state=&limit=&offset=` | 端点列表；`state` ∈ `all`、`pinned`、`changed`、`flagged`、`unobserved`；`q` 匹配名称、端点、标题 |
| GET | `/api/v1/servers/<id>` | 单个端点：当前工具集、带匹配区间的 WARDEN 匹配项、标签、折叠后的时间线、变更、30 天内的客户端报告 |
| GET | `/badge/<id>.svg` | README 徽章：`pinned ‹date›`、`unchanged Nd`、`changed ‹date›`、`not observed`、`not listed` |

`<id>` 是 `sha256(name + "\n" + endpoint)` 的前 16 个十六进制字符——稳定不变，每个远程端点一个。

## 变更

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/api/v1/changes?limit=&before=` | 最新的在前；每条含 `summary.added`、`summary.removed`、`summary.modified[].fields`（描述按词比较差异，schema 按 JSON 路径比较差异） |
| GET | `/api/v1/changes/<id>` | 单条变更 |
| GET | `/feed.xml` | 最近 50 条变更的 Atom 订阅源；`?watch=<id>,<id>,…`（最多 100 个目标 id，即 `/check` 和服务器页面给出的 id）只保留这些服务器和软件包——可在任意订阅阅读器中订阅 |

## 标签与证据

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/api/v1/labels/<id>` | 签名标签，`application/vc`，不可变 |
| GET | `/api/v1/labels/<id>/proof?tree_size=` | 叶子索引、签名树头（STH）以及包含证明 |
| GET | `/api/v1/toolsets/<sri>` · `/descriptors/<sri>` · `/pattern-sets/<sri>` · `/record-sets/<sri>` | 内容寻址的证据，不可变 |
| GET | `/api/v1/issuer` | 日志的 `did:key`、原始公钥、当前模式集与记录集的摘要、WARDEN 软件包 |

## 日志

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/api/v1/log/sth` | 最新的签名树头 |
| GET | `/api/v1/log/sth/<size>` | 在该大小时签发的树头 |
| GET | `/api/v1/log/entries?start=&end=` | 按顺序排列的叶子（每次调用 ≤ 1000 条） |
| GET | `/api/v1/log/proof/inclusion?leaf_index=&tree_size=` | RFC 9162 包含证明，十六进制 |
| GET | `/api/v1/log/proof/consistency?first=&second=` | RFC 9162 一致性证明，十六进制 |

## 收据日志

第二个彼此独立的日志：市场工作收据（AWR/2）的锚点，由签发收据的中心节点提交。一个锚点只携带四项事实——收据的摘要、签发方的 `did:key`、签发时间，以及签发方对这些事实的签名——绝不包含收据本身。它的树头是 `histor.receipts-sth/v1`，与标签日志使用同一把密钥签名，但覆盖的是另一棵树。

| 方法 | 路径 | 返回 |
|---|---|---|
| POST | `/api/v1/receipts/anchors` | `{"anchors": [...]}`（1–100 个）→ 每个锚点一个结果：`logged`、`duplicate` 或附带原因的 `refused` |
| GET | `/api/v1/receipts/sth` | 收据日志最新的已签名树头 |
| GET | `/api/v1/receipts/sth/<size>` | 在该大小时签名的树头 |
| GET | `/api/v1/receipts/proof?digest=&tree_size=` | 锚点、其叶子索引，以及相对某个已签名树头的 RFC 9162 包含证明 |
| GET | `/api/v1/receipts/proof/consistency?first=&second=` | RFC 9162 一致性证明，十六进制 |

只接受 `HISTOR_RECEIPT_ISSUERS` 中列出的签发方（以逗号分隔的 `did:key`；为空即关闭，这是默认值）提交，且必须带有签发方本人对 `type`、`issuer`、`receiptDigest` 和 `issuedAt` 的 RFC 8785 字节所做的 Ed25519 签名。`issuedAt` 最多可滞后 30 天（故障后的积压），最多只能超前 5 分钟。

## /check

```http
POST /api/v1/check
Content-Type: application/json

{
  "endpoint": "https://example.com/mcp",      // or "name": "io.github.org/server"
  // a stdio server instead: "package": "npm:@scope/name" or "pypi:name"
  "tools": [ … ],                             // the tools array you received, or:
  "toolSetDigest": "sha256-…",                // its MTL/1 digest
  "contribute": true                          // optional: add (endpoint, digest, day) to a count
}
```

```json
{
  "type": "histor.check/v1",
  "checkedAt": "2026-09-23T10:02:11Z",
  "issuer": "did:key:z6Mk…",
  "query": { "endpoint": "…", "toolSetDigest": "sha256-…" },
  "target": { "id": "9c7f2af79fa057b2", "page": "…/s/9c7f2af79fa057b2", "badge": "…", "lastStatus": "ok" },
  "observed": { "toolSetDigest": "sha256-…", "unchangedSince": "…", "observations": 12, "changes": 0 },
  "match": "same",
  "note": "The tool definitions you sent are the set HISTOR currently observes at this endpoint.",
  "patternScan": { "status": "scanned", "block": 0, "advise": 1, "matches": [ … ] },
  "log": { "treeSize": 21480, "rootHash": "…", "timestamp": "…" },
  "signature": { "alg": "Ed25519", "verificationMethod": "…", "value": "…" }
}
```

`match` 取值为 `same`、`different`、`previously-observed`（附 `seenBefore`）、`not-observed`、
`not-listed`、`no-digest` 之一。应答像树头一样签名（`type` 为 `histor.check/v1`）。如果该工具集从未
被扫描过，而您的地址仍有扫描预算，WARDEN 边车会当场扫描它。限制：每个地址每分钟 30 次检查、每小时
20 次新扫描；超过 2 MiB 的请求体会被拒绝。使用 `contribute` 时，除了该摘要当天的计数之外，不存储
任何内容。

对于 stdio 服务器（由你的客户端启动的 npm 或 PyPI 软件包），用 `package`——`npm:<名称>` 或 `pypi:<名称>`——代替 `endpoint`；PyPI 名称会替你规范化。`target.packageVersion` 是 HISTOR 在其[软件包沙箱](operations.zh.md#软件包沙箱)中最近观察到的版本。软件包名称永远不会被计为未知主机。 对软件包还有两项应答。`packageLookalike`（`of`、`weekly`、`how`、`ownWeekly`）：对任何被询问的软件包（无论是否已收录），当某个热门软件包的下载量至少高出 100 倍时，指出此名称模仿的那个热门软件包——相差一个字符、在另一个 scope 下同名，或仅分隔符不同；`mcp-server` 这类通用名称不属于任何人。`target.packageSignals` 是注册表对 HISTOR 所观察版本的说明：`provenance`（经证明的 CI 构建）、`publisher`（从不包含邮箱）、`installScripts`，以及 `flags`——相对上一个观察到的版本发生了什么变化：`provenance-lost`、`publisher-changed`、`install-scripts-added`、`new-dependencies`，这些是发布令牌被盗的典型迹象。

## 统计与实时徽章

| 路径 | 返回 |
|---|---|
| `/api/v1/stats` | 最近一次抓取（注册表规模、各端点如何应答、哪些未连接、签发了多少标签）、当前计数、最近 24 小时 / 7 天 / 30 天的变更、日志大小、7 天内的客户端报告 |
| `/api/v1/stats/hosts?limit=` | 在已连接的端点中，按主机统计的端点数 |
| `/api/v1/badges/log` · `pinned` · `changes` · `sth` | 供 README 徽章使用的 [shields.io endpoint](https://shields.io/badges/endpoint-badge) JSON |
| `/health` | 存活状态、存储后端、树大小、是否正在抓取、上次抓取的错误 |

## 联邦（AIMarket Hub）

HISTOR 是一个 AIMarket peer：`/.well-known/ai-market.json`（整体签名）、`/ai-market/v2/manifest`
（按 Hub 的 manifest 规范化形式签名），以及提供三项免费能力的 `POST /ai-market/v2/invoke`：

| 能力 | 输入 | 结果 |
|---|---|---|
| `histor.check@v1` | `/check` 的请求体 | 签名的 `/check` 应答 |
| `histor.server@v1` | `{"endpoint": …}` 或 `{"name": …}` | 最多 10 个匹配的端点 |
| `histor.changes@v1` | `{"limit": 1–100}` | 最新的变更 |

每个回复都附带一份互操作收据，Hub 用 `.well-known` 中的密钥验证它；在 `HISTOR_PQC=1` 时为
Ed25519 + ML-DSA-65 混合签名。

## 运营方

带上 `x-histor-operator: $HISTOR_OPERATOR_TOKEN` 调用 `POST /api/v1/admin/crawl`，会立即启动一次
抓取（202）；如果已有抓取在运行，则返回 409。未配置令牌时，该路由返回 503。

`GET /api/v1/admin/unlisted?days=7&limit=100`（同样的请求头）列出向 `/check` 询问过、但 HISTOR 尚未收录的主机，按询问次数从多到少：`host`、`queries`、`days`、`first_day`、`last_day`。只统计具有公共 DNS 名称的 https 端点的主机名——从不记录路径或参数、调用方地址或任何工具——每个地址、主机每天最多计一次；超过 90 天的记录会被删除。HISTOR 自己的名单（`histor/curated.json`）就是这样按人们真正使用的服务器扩充的。
