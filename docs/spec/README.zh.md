# HISTOR 透明日志格式规范（`histor-tlog/1`）— 摘要

> 只有英文文本是规范性的：[transparency-log.md](transparency-log.md)。本摘要不具规范性。其他语言：[English](transparency-log.md) · [Русский](README.ru.md) · [Español](README.es.md) · [Français](README.fr.md)。

规范确定的内容：

- **叶子**是一条 label：MTL/1 配置的 W3C Verifiable Credential，由日志密钥签名（`did:key`，Ed25519，cryptosuite `eddsa-jcs-2022`）。叶子输入是文档*按原样提供*（含 proof）的 RFC 8785 规范字节。`leaf_hash = SHA-256(0x00 ‖ JCS(label))`。
- **树**采用 RFC 9162（Certificate Transparency v2）：`node_hash = SHA-256(0x01 ‖ left ‖ right)`；包含证明与一致性证明不作改动。
- **签名树头**（`histor.sth/v1`）：`log`、`treeSize`、`rootHash`、`timestamp`；Ed25519 签名覆盖除 `signature` 外所有成员的规范字节，`type` 在被签名的字节之内，因此一种文档永远无法冒充另一种。每个树大小只有一个树头，永久保存。
- **校验规则**：先核对 `type`；只认日志已固定（pinned）的密钥；在计算群方程之前拒绝小阶密钥与小阶点（严格 Ed25519 规则）。
- **`/check` 应答**（`histor.check/v1`）是一条签名声明：日志对该工具集持有该裁定；它是需要核实的线索，不是证明。
- **未包含的内容**：发布者签名（发布者一侧是在注册表或自己域名下的文件中声明工具集）、时间与完整性的证明。
- **`histor-tlog/2`（2026-10-30）：** 同一树头也以 **C2SP checkpoint** 形式提供（`GET /checkpoint`：origin、大小、base64 根、按 c2sp.org/signed-note 的日志签名，随后是按 c2sp.org/tlog-cosignature 的见证者副署行），且本实例按 c2sp.org/tlog-witness 充当 `HISTOR_WITNESS_LOGS` 中所列日志的**见证者**（`POST /witness/v1/add-checkpoint`，独立密钥 `witness.key`）。树、叶子与证明不变。向量已用参考 Go 库（`golang.org/x/mod/sumdb/note`、`github.com/transparency-dev/formats/note`）验证。
- **向量** `test-vectors/vectors.json` 具规范性：8 个叶子、大小 1–8 的根、全部包含与一致性证明、测试密钥签名的两个树头、三个反例。`tests/test_spec_vectors.py` 使代码与该文件保持一致。
