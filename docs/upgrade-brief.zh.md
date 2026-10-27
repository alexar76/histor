# HISTOR MCP 更新分析指南

[English](upgrade-brief.md) · [Русский](upgrade-brief.ru.md) · [Español](upgrade-brief.es.md) · [Français](upgrade-brief.fr.md) · [中文](upgrade-brief.zh.md)

本地原型 `histor upgrade-brief` 将 MCP 工具定义的变化关联到您实际使用的工作流程。
为每个依赖提供两次已保存的观测，并明确选择要关注的工具，即可生成带有复测建议的
`report.md`，以及记录差异、版本、比较范围限制和输入 SHA-256 哈希的 `report.json`。

请使用当前源码目录运行此原型。报告命令以确定性方式离线运行，无需大语言模型、数据库、
凭据或生产环境配置。工具描述中的文字仅作为数据处理。定义变化意味着需要复测相关流程，
并不能据此认定服务器行为已经变化或升级已经导致故障。

## 运行并验证示例

需要 Python 3.11 或更高版本。在此工作区的 `histor/` 目录中运行：

```bash
python3 --version
python3 -m histor upgrade-brief \
  --lang zh \
  --watchlist examples/upgrade-brief/watchlist.zh.json \
  --out-dir ../output/histor-upgrade-brief-example/zh
python3 examples/upgrade-brief/verify.py \
  --out-dir ../output/histor-upgrade-brief-example
```

如果符合版本要求的解释器是 `.venv/bin/python`，请在各命令中用它替换 `python3`。
离线示例无需安装软件包。验证脚本会将五份语言版本的关注列表各运行两次，检查预期变化和
流程状态，逐字节确认输出可重复，并确认输入文件保持原样。成功时输出 `PASS`，退出码为
`0`；检查失败时退出码为 `1`。每个语言目录中均包含 `report.md` 和 `report.json`。
再次运行这些命令会替换生成的报告。

验证脚本还会不带 `--lang` 运行两次，并要求输出与显式使用 `--lang en` 时逐字节相同：总计 12 次离线 CLI 运行。

六份观测数据均为合成数据。示例不会启动 MCP 服务器，也不会调用任何工具。
[示例说明](../examples/upgrade-brief/README.zh.md)解释了预期结果和具体复测步骤。
使用说明、关注列表标题和流程描述提供五种语言版本。

## 选择报告语言

`--lang` 支持 `ru`、`en`、`es`、`fr` 和 `zh`。CLI、Python API 和缺少 `language` 字段时的渲染默认使用英语（`en`）。
它选择 Markdown 内置标题和解释、工具及流程复测清单，以及 JSON 中的复测文字和限制说明
所使用的语言。JSON 的 `language` 字段记录所选语言。字段名、状态码、诊断原因、ID、
schema 和哈希保持不变。

关注列表标题、流程描述和原始定义片段按输入原样保留。要获得相应语言的标签，请选择
示例中对应的本地化关注列表；`--lang` 不会自动翻译输入数据。

## 选择依赖和工作流程

创建一个包含 `watchlist.json` 和 `snapshots/` 的数据包目录。快照路径相对于关注列表所在
目录解析，且必须位于该目录内。URL、绝对路径、包含 `..` 的路径，以及指向目录外部的
符号链接均会被拒绝。

```json
{
  "format": "histor.upgrade-watchlist/v1",
  "title": "我们的研究流程",
  "dependencies": [{
    "id": "our-research-server",
    "before": "snapshots/before.json",
    "after": "snapshots/after.json",
    "tools": ["search_docs"],
    "workflows": [{
      "id": "weekly-review",
      "description": "准备每周文档综述",
      "tools": ["search_docs"]
    }]
  }]
}
```

每个依赖必须选择至少一个工具，并定义至少一个流程。流程只能引用该依赖已选择的工具。
翻译可读标签时，请保持依赖 ID、工具名称和流程 ID 不变。`before` 或 `after` 可以为
`null`；缺少观测时，比较结果将保持未知。进行比较时，每份快照的 `targetId` 必须与
所属依赖的 `id` 相同。

从 `histor/` 目录运行您自己的数据包：

```bash
python3 -m histor upgrade-brief \
  --lang zh \
  --watchlist /path/to/bundle/watchlist.json \
  --out-dir /path/to/report
```

## 保存 HTTP 观测

独立的 `capture` 命令会向您指定的端点发送网络请求。它需要 `pyproject.toml` 中声明的
HISTOR 运行依赖。如果尚未安装，请先从当前源码目录准备环境；安装依赖时可能访问软件包索引：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

使用真实 URL 和新的输出文件名，采集一个公开的 HTTP MCP 端点：

```bash
.venv/bin/python -m histor upgrade-brief capture \
  --url https://your-public-server.example/mcp \
  --target-id our-research-server \
  --out /path/to/bundle/snapshots/before.json
```

`.example` URL 和 `/path/to/` 路径均为占位符。升级后使用 `after.json` 再采集一次，
然后运行离线报告命令。采集会使用 discovery，必要时执行旧版初始化，并读取 `tools/list`
的所有分页。它不会调用工具，也不会安装或启动 MCP 软件包。当前不支持端点认证，包含
用户信息、查询参数或片段的 URL 会被拒绝。默认禁止访问私有地址；如需采集您自己的测试
端点，请显式添加 `--allow-private`。`--timeout` 默认是 20 秒，允许大于 0 且不超过
300 的值。

采集不会替换已有快照。退出码 `0` 表示已保存完整观测，`1` 表示已保存失败的观测，
`2` 表示输入无效或输出写入失败。失败的观测不会被当作成功返回的空工具列表。采集快照
属于未签名的本地证据：报告中的哈希可以检测输入变化，但不能认证创建者身份。

## 保留观测来源信息

```json
{
  "format": "histor.upgrade-observation/v1",
  "targetId": "our-research-server",
  "status": "ok",
  "observedAt": "2026-10-27T09:00:00Z",
  "packageVersion": null,
  "serverName": "our-research-server",
  "serverVersion": "1.2.0",
  "protocol": "2026-07-28",
  "extrasCoverage": "observed",
  "launch": {"transport": "streamable-http", "endpoint": "https://your-public-server.example/mcp"},
  "tools": [{"name": "search_docs", "description": "Search documents", "inputSchema": {"type": "object"}}]
}
```

成功的观测必须包含完整的 `tools` 数组。请保存带时区的实际时间、观测到的协议版本和
启动上下文。软件包的实际观测版本与服务器自行报告的 `serverVersion` 应分开记录。
远程端点的 `packageVersion` 可以为 `null`。报告展示 `launch` 的哈希和已知的传输类型，
不会复制命令、参数或环境变量值；原始快照本身仍可能包含敏感数据。

导入 HISTOR 数据时，必须将工具定义与确切的历史观测对应起来。本地 API 实现会提供
`timeline[].protocolVersion`；存在该字段时，将其复制到 `protocol`。旧版本或已部署的
API 可能没有该字段，应保留这一信息缺口。只有历史 `toolExtras` 确实属于那次观测时，
才按工具名称合并这些字段。当前定义或当前 extras 不能用于填补历史快照。此原型没有
自动 API 导入器。

对于仅包含 `name`、`description`、`inputSchema` 和 `outputSchema` 四个字段的 MTL/1
数据源，请将 `extrasCoverage` 设为 `"unknown"`。报告仍会比较这些核心字段，但跳过
extras 的比较，并将整体可比性保留为未知。`"observed"` 表示快照包含该次观测中服务器
展示的全部 extras；这也是现有本地 v1 输入未指定此字段时的默认值。

## 阅读结果并选择复测范围

| 依赖状态 | 含义及下一步 |
|---|---|
| `changed` | 可比观测中的定义发生变化。检查受影响的已选工具及相关流程。 |
| `unchanged` | 可比观测中的定义未发生变化。运行时行为尚未测试。 |
| `context_changed` | 已知的协议或启动条件发生变化。在将差异归因于软件包升级前，先恢复可比条件。 |
| `unknown` | 缺少成功的观测对或比较上下文，时间顺序不正确，某个已选工具在两侧都不存在，或 extras 覆盖范围未知。先解决列出的信息缺口。 |

流程状态为 `retest`、`no_observed_change` 或 `unknown`。依赖的变化可能只涉及未选中的
工具；请根据各流程的状态确定复测范围。`fieldCoverage` 的取值为 `full`、`core-only`
或 `none`。即使整体可比性未知，已知差异仍然会展示。annotations 中的提示仍只是服务器
自己的声明。

`serializedUtf8Bytes` 以 UTF-8 字节数衡量发送给模型的紧凑 JSON，并不衡量 token 数、
账单或实际节省。SHA-256 值标识确切的输入字节。定义不变或成功生成报告，都不能证明
运行时行为正确或安全。

## 限制与问题排查

此原型最多接受 30 个依赖，每个依赖最多选择 128 个工具并定义 64 个流程，每份快照
最多包含 2,000 个工具。关注列表大小上限为 256,000 字节，每份快照上限为 2,000,000
字节。重复的 JSON 键、未知的格式字段、非有限数值、无效的工具定义，以及超过 48 层
或 100,000 个节点的结构均会被拒绝。

报告写入成功时命令退出码为 `0`，包括含有 `unknown` 结果的报告；`2` 表示输入无效或
写入失败。可以重复使用已有输出目录，`report.md` 和 `report.json` 会被替换，但不允许
覆盖输入文件。如果遇到 `No module named histor`，请从源码的 `histor/` 目录运行。
遇到 `unknown` 时，请先查看 `limitations`、`sources` 和 `fieldCoverage`，再决定是否升级。
