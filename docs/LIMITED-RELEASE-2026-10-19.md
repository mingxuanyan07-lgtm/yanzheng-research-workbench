# 研证受限交付说明（2026-10-19）

## 结论

本候选是**不包含 Lean 可靠性承诺的内部受限交付**。它可用于内部评审、试用和录制准备，不得描述为稳定正式版，也不得把 Lean 单次通过当作可靠性修复。

## 包含范围

- 当前 A/B 修复：evidence-registry v1.2 IO 修复、B 适配器接入、A 测试诊断兼容、默认测试入口隔离、链接错误分类收紧。
- 35 份登记公开原件、五份历史运行 JSON 与现有理论/证据/Lean 代码。
- 真实浏览器受影响链路证据：Chrome 155 + Playwright，独立端口/临时库，50/50 UI/API 检查通过；这是候选验证，不是正式发布接受。
- 工程收尾与 Lean 根因诊断证据，均保留在 `output/` 原始目录。

## 明确不包含/不承诺

- **不包含 Lean 可靠性承诺**。最新六例定向采集全部在 10 秒 wall limit 超时；同案例后续单次通过、直接编译变快等观察都不能证明稳定或修复。UI 已增加“受限交付提示”。
- **不包含正式原研究库启动**，不包含 `main` 合并。`main` 仍保持原固定提交；正式启动需 Codex/总验收另行接受确切候选后按原授权执行。
- **不包含数学闭环**。四个证明义务仍 `open / not_formally_verified`；真实独立数学审阅和研究者最终决定未完成，助手记录不能代替。
- 不包含任意形式的 Lean、模型或正式服务通过结论；历史模型输出的拒绝结论不变。

## 候选与 ZIP

- 候选分支：`candidate/20261019-evidence-io`
- 精确提交与 ZIP SHA 以交付记录/候选 ZIP 名称及 `CONTENTS.json` 为准；Codex 验收前不合并 `main`。
- 候选 ZIP 内含源码、候选证据和本说明；旧队友 ZIP 与旧 main 不覆盖。

## 启动/停止（队友试用）

按候选包内《候选版使用说明》执行；只使用独立演示库，不复用真实研究库：

```powershell
$env:YANZHENG_DB_PATH = 'C:\yanzheng-trial\YOURNAME\yanzheng-demo.db'
$env:YANZHENG_MODEL_PROVIDER = 'demo'
python -B -m uvicorn app:app --host 127.0.0.1 --port 18097
```

停止按 Ctrl+C，不结束未知进程。`D:\天猫AI\webapp\data\research.db` 不得用于本轮试用；正式原库启动需另行验收。

## Lean 展示边界

- 可展示固定案例运行记录、错误/超时事实、公理/信任审计字段和“不可用/超时”状态。
- 不得展示为稳定通过、不得声称自然语言命题已证明、不得以一次成功补写历史失败。
- 已知限制：Windows 本地 NTFS 普通路径；其他平台/文件系统未验证；真实 symlink 替换位置仍缺能力验证。
- 根因诊断记录：`output/lead-review/20261019-lean-wait-io-root-cause/REPORT.md`；六例采集：`output/lean-reliability/round-20261019-targeted-compiler-collection/`。

## 验收要求

Codex 应核对：候选分支确切提交、ZIP SHA、170 个源码文件、35 份公开原件、UI 提示、工程收尾证据及本说明；验收前不合并 main、不启动原研究库。若接受，再按限定范围交付；若不接受，保留候选和诊断记录。
