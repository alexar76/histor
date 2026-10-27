# 复现 HISTOR MCP 更新分析

[English](README.md) · [Русский](README.ru.md) · [Español](README.es.md) · [Français](README.fr.md) · [中文](README.zh.md)

本示例比较三个依赖的六份已保存的合成观测，将定义变化关联到四个工作流程，并给出升级前
需要进行的具体检查。这些数据不对应已发布的软件包、在线端点或客户部署。

## 从源码目录运行

在 `histor/` 目录中使用 Python 3.11 或更高版本。无需安装软件包、凭据、模型或网络：

```bash
python3 -m histor upgrade-brief \
  --lang zh \
  --watchlist examples/upgrade-brief/watchlist.zh.json \
  --out-dir ../output/histor-upgrade-brief-example/zh
python3 examples/upgrade-brief/verify.py \
  --out-dir ../output/histor-upgrade-brief-example
```

如果 Python 3.11+ 解释器位于 `.venv/bin/python`，请用它替换 `python3`。第一条命令
写入 `report.md` 和 `report.json`。第二条命令将五份语言版本的关注列表各验证两次，
对照 [expected.json](expected.json) 检查预期结果，确认重复输出相同且输入未变化，然后
输出 `PASS`，退出码为 `0`。检查失败时退出码为 `1`。报告分别位于 `en/`、`ru/`、
`es/`、`fr/` 和 `zh/` 下；再次运行会替换生成的报告。

验证脚本还会不带 `--lang` 运行两次，并要求输出与显式使用 `--lang en` 时逐字节相同：总计 12 次离线 CLI 运行。主文件 `watchlist.json` 为英语版本，`watchlist.en.json` 保留为等效别名。

## 预期结果

汇总为 `changed: 1`、`unchanged: 0`、`context_changed: 1`、`unknown: 1`。

| 依赖 | 结果 | 流程结果 |
|---|---|---|
| `demo:research` | `changed`，定义可比 | `weekly-review` 和 `archive-import`：`retest` |
| `demo:migration` | `context_changed`，协议发生变化 | `status-check`：`unknown` |
| `demo:offline` | `unknown`，新的观测失败 | `daily-status`：`unknown` |

对 `weekly-review`，重放已保存的 `search_docs` 请求，补上新要求的 `workspace` 字段。
确认 `limit` 现在声明为字符串，并将结果解析从 `items` 改为 `results`。阅读变更后的
描述，确认工具选择符合预期。`export_brief` 的 `readOnlyHint` 从 `true` 变为 `false`，
`destructiveHint` 从 `false` 变为 `true`；在测试导出前，检查操作确认和权限处理。
这些提示仅是服务器自己的声明。

对 `archive-import`，已选择的 `legacy_fetch` 工具被移除。新出现但未选中的
`fetch_document` 工具使用 `id`，而非 `document_id`。检查这一候选工具，适当时显式
更新流程的工具选择，再使用一个已知文档测试；分析不会认定它是自动替代品。

对 `status-check`，`read_status` 定义保持不变，而协议从 `2025-06-18` 变为
`2026-07-28`。使用支持目标协议的客户端，在相同启动和访问条件下重新观测。
对 `daily-status`，先取得完整且成功的新观测，再比较工具。采集失败不能证明工具被移除。

验证脚本检查定义和预期报告结构。上述流程复测步骤供您在自己的测试环境执行；本示例
不会启动 MCP 服务器或执行工具。

## 语言版本与您自己的数据

| 语言 | 关注列表 |
|---|---|
| English | [watchlist.json](watchlist.json) |
| Русский | [watchlist.ru.json](watchlist.ru.json) |
| Español | [watchlist.es.json](watchlist.es.json) |
| Français | [watchlist.fr.json](watchlist.fr.json) |
| 中文 | [watchlist.zh.json](watchlist.zh.json) |

各语言版本翻译标题和流程描述，共用快照、ID 和已选工具。使用 `--lang ru|en|es|fr|zh`
选择报告内置解释、复测清单，以及 JSON 中复测文字和限制说明的语言；默认使用英语（`en`）。
字段名、状态码、诊断原因、ID、schema 和哈希保持不变，JSON 的 `language` 字段记录选择。
您的标题、流程描述和原始定义片段会原样保留，因此请将此选项与对应的本地化关注列表配合
使用；输入数据不会自动翻译。

如需使用您自己的数据，请将数据包复制到独立目录，替换快照并修改工具选择和工作流程。
`expected.json` 和 `verify.py` 针对此固定合成示例；换用数据时也需要修改预期结果。
独立的 `capture` 命令可以通过网络保存 HTTP 观测，详见[指南](../../docs/upgrade-brief.zh.md)。
