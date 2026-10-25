# 如何解读 HISTOR 测量

> 🌐 [English](measurement-limits.md) · [Русский](measurement-limits.ru.md) · [Español](measurement-limits.es.md) · [Français](measurement-limits.fr.md) · **中文**

根据读者问题补充，2026-10-25。这些是观察与比较的边界，不保证每个安装或在线服务器的行为。本地源码、已部署服务和已发布 Python 包是不同状态。

## 安装失败是观察，不是诊断

`sdk-incompatible` 是根据退出文本得到的启发式分类。旧分类器在 traceback 提到 FastMCP 时，也会包含缺少 extras 和 MCP 1/Pydantic 错误；不能据此证明未限定上界的依赖安装了 MCP 2。用 `mcp<2` 诊断重试改变了环境，不能替代主要的新安装观察及其 tools 集合。

重建的 292 包队列已在 Linux/gVisor 中重新安装，不强加我们的 `mcp<2`：12 个新版本列出 tools，另两个原版本安装声明的 MCP extra 后成功，276 个仍失败，两个需要真实认证或已准备的项目索引。11 个新版本成功有全新安装的旧版失败对照；一个旧版已不可用。这不是 292 个已证明的 MCP 2 故障，也不证明安全或完整认证功能。[结果与对照](https://dev.to/alexar76/we-rechecked-292-failing-mcp-packages-12-newer-releases-now-list-tools-p72)。

## Token 估算与 CI

分别检查定义总量、描述和输入 schema：一部分缩短可能隐藏另一部分增长。保留人工审核的 baseline，不要在每次 check 前重建。[可复制公开示例](https://dev.to/alexar76/a-copyable-mcp-tool-cost-check-for-ci-with-passing-and-failing-examples-3hl5) 固定已发布 CLI 0.3.1 与 tokenizer 0.14.0，验证保存快照，不调用 textokit tools。真实变化是 5,565 → 3,776 tokens；反向比较故意返回 **1**，不表示后续版本发生回退。正常方向返回 **0**，无效输入返回 **2**。

计数针对 `o200k_base` 下的特定紧凑表示。客户端包装、工具子集、按需加载、tokenizer 与缓存写入／读取会影响实际用量。历史价格表是标明日期的情景，不是账单或当前价格；账单需要客户端实际 usage records。[成本指南](tool-list-cost.zh.md)。

## 数字变化不一定只是格式变化

空白／数字遮蔽视图帮助审核，不替代按原文字面规范化的 MTL digest 或 continuity 判断。$0.002 → $0.02 只改数字却可能重要：[change 13185](https://histor.modelmarket.dev/api/v1/changes/13185)。区分服务器与变化事件、描述与 schema／工具组成、可重叠标志与互斥类别；缺少 raw state 不代表没有变化。[标签与摘要](labels.zh.md)。

## 付款不能确定客户身份

源码中的 `histor payer-audit` 将结算、行为信号及按交易归属的来源声明分开。2026-10-25 查询 PyPI 得到 **0.3.1**，该发行版不含此命令。本地 checkout 的版本字符串不能证明已发布。[从源码运行，EN](payer-audit.md)。

匹配的 Base USDC 转账不能证明服务交付、钱包所有者或购买意图。卖方 own-wallet 标记及 inputExample 匹配说法在独立核实前都是有归属的声明。`unknown` 不确认自然客户；`probe-like` 是未经校准的线索，不是欺诈判断或概率。一次已知研究付款不会永久分类整个钱包。包括探测和监控在内，每笔付款都保留在金额总计中。[五个付款者示例](https://dev.to/alexar76/five-wallets-paid-an-x402-seller-what-can-we-say-about-demand-5gek)。

## 轮询会漏掉中间状态

Remote HTTP 使用独立队列：通常六小时；七天内至少观察到两次变化后，目标间隔为 30 分钟。注册表发现和软件包计划保留各自每日节奏。这是目标间隔，不保证实际延迟；停机、积压、rate limits 和 backoff 会拉长间隔。查看 `/api/v1/stats` 的 `remotePolling` 与实际观察间隔。两次检查之间完整发生的 A→B→A 不可见；提高频率只能缩小盲区，不能记录每次变化。[运行细节，EN](remote-polling.md)。

## Canary 与 launcher 的边界

成功的 `initialize`／`tools/list` 或一次合成 canary 不能测试全部输入，也不能证明免费。Canary 可能选择免费 preview，而省略参数触发服务器付费默认值。服务器的 `readOnlyHint` 不证明计费行为；错误或 timeout 不证明没有效果。远程只读观察与包 canary 是不同测量。

比较 launcher 时保存两份完整 tools 快照、精确命令、runtime／runner 版本和测试条件。包版本、实际 runner 与自报的 `serverInfo.version` 可能不同。读取历史快照不是重新运行，也不是人类可用性研究；注解和版本声明需要各自来源。
