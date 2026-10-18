# 独立数学工作者审阅包

状态：**准备完成，尚未发送，独立审阅 pending**。本目录是审阅入口；完整便携包为上级 `review-package.zip`，内含本轮交付及原件/来源快照。复制或解压后从根 README 开始，无需运行网站、模型或 Lean。

作者：助手。本文件以及包内每件材料的来源和 SHA256 见根 INPUT_MANIFEST / ARTIFACT_MANIFEST / READY / SHA256SUMS。请核对审阅版本后写意见；助手已经填写的数学判断不是您的预设结论。

建议顺序：

1. CASE_SPEC：确认 P0、P1、P2、定义域、量词与边界。
2. DERIVATION：逐行审查 D1–D6，优先检查四义务与 P2。
3. OBLIGATIONS、claim-ledger：核对逐条材料与来源位置，不把状态标签当证明。
4. SOURCE_VERIFICATION 与 `sources/` 的实际 HTML/带行号文本：核查版本、正文范围、支持边界。
5. MODEL_COMPARISON 与 `evidence/inputs/`：分别读取历史 v1.2 上下文、v1.4 原返回、实际冻结输入、具体反馈、人工提议。原文副本按字节保留。
6. QUESTIONS 和 REVIEW_FORM：留下实际读过的范围、具体疑点/修正、逐义务意见与签署日期。未读部分请保持未读。

仅需阅读即可完成数学审阅。`calculations/results.json` 是有限有理算术和归档检查，不是普遍定理证明。若复算，按根 README 在本轮目录运行离线脚本；不要执行旧材料中出现的 live、网站、模型或 Lean 命令。

历史文件名含 independent 仍是助手审阅，不是独立数学工作者认证。此包没有已邀请的审阅人，也没有预填任何人的身份或“通过”意见。审阅返回后由总验收结合实际证据判断最终状态；不能直接把此模板导入生产数据库以关闭义务。
