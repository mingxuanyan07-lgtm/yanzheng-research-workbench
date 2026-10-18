# 队友交接：克隆、启动、样例与录制顺序

本文件面向下一批队友（Demo/实操视频录制者）。目标是让你可以**克隆、启动、了解真实能力并录制演示**；它不代表产品最终验收通过，未解决问题在文中如实保留。

仓库：<https://github.com/mingxuanyan07-lgtm/yanzheng-research-workbench> · 分支：`main`
固定提交：以 `main` 最新提交为准，可用 `git log -1 --format=%H` 查看；本轮实际推送 SHA 与直接链接由执行同步的窗口在会话回答中给出，不在仓库内写自引用哈希。

## 1. 获取与最低环境

```shell
git clone https://github.com/mingxuanyan07-lgtm/yanzheng-research-workbench.git
cd yanzheng-research-workbench
```

- Windows 最低 **Python 3.12**（依赖固定 `numpy==2.5.4` 的最低要求）。
- 锁定依赖：`webapp/requirements.txt`（`fastapi==0.143.0`、`uvicorn==0.54.0`、`httpx==0.28.1`、`numpy==2.5.4`）。
- 不需要 GPU、Node 构建或数据库服务；SQLite 本地文件即可。
- 仓库不携带 `.env`、真实研究库或任何凭据。

## 2. 启动（队友本机）

首次准备（需要安装依赖的网络）：

```powershell
cd webapp
.\Setup-Workbench.ps1
.\Start-Workbench.ps1          # 默认 http://127.0.0.1:8097/
```

或从仓库根目录双击 `启动研证网站.cmd`。标准 Python 方式：

```powershell
cd webapp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8097
```

**建议：演示一律使用独立临时库，不要使用任何人的真实研究库。** 应用在导入时创建默认库，因此必须在导入前设置环境变量：

```powershell
cd webapp
$env:YANZHENG_DB_PATH = Join-Path $env:TEMP "yanzheng-demo-$(Get-Random).db"
$env:YANZHENG_MODEL_PROVIDER = "demo"     # 演示默认不调用真实模型
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8097
```

- 本机验证只使用明确临时库；**不要读写 `D:\天猫AI\webapp\data\research.db`**，也不要把真实库复制进仓库或发布包。
- 测试入口 `scripts/run_tests.py` 会在导入应用前自动设置临时库、模型替身；不会触碰真实研究库。

## 3. 可用的公开样例与来源

仓库随附、可审查的样例有三类（均在仓库内，不依赖本机历史）：

1. **“创建公开案例”**（理论工作台内按钮）：新增独立逐点收敛示例，标记 `curated_example`。来源为预先核对的公开大学讲义；**不代表用户阅读、实时检索、千问生成、新定理或形式证明**。
2. **35 份已登记公开原件**：`webapp/evidence_registry_manifest.json` 登记的固定白名单，包括 C 的案例归档（案例规格、推导、义务、来源快照、审阅包）与 2 份历史控制文件。理论工作台中只能按本项目材料明确关联的 `artifact_id` 显式读取；页面显示字节哈希与转义纯文本预览，不提供任意路径/下载端点。
3. **五份历史运行 JSON**：`output/math-ai-workbench/{experiment-data.json,research-direction-map.json}`、`output/literature-search-skill/cases/forward-trial/{literature-search.json,direction-handoff.json}`、`output/project-review/cases.json`。它们供 catalog/engine 读取历史档案与随机 SVD 历史实验，**查看档案不产生新的检索/阅读/实验事实**。

样例使用规则：

- 所有 QA 意见必须显式标记 `test_fixture`；助手核查/意见不是真人审阅，不填真人回执，不认证身份或数学正确性。
- 不用本机原研究库制作视频；不补造“成功数据”。B 的四份实际 Chrome 下载属于 QA 证据，**不随仓库交付**，不要把它当成真人用户操作录制。
- 若需要独立可审查的演示样例：按第 2 节复制仓库到新目录，用 demo 模式 + 临时库即可，无需新增工具。

## 4. 能力边界（分别说明，不要混为一谈）

- **本地规则 / demo 模式**：默认路径，不需要模型 Key，可完整演示方向探索、候选比较、计划导出等规则能力。
- **真实千问**：需要队友自己在本机配置（`webapp\Configure-Qwen.ps1`，Key 隐藏输入，保存在不入库的 `.env`）。默认不调用真实模型；真实调用会消耗额度，且**模型建议不等于科学结论**。仓库不携带任何 Key。
- **固定随机 SVD 实验**：可真实执行小规模 NumPy 子进程（固定矩阵/参数/上限），记录种子、基线、误差与限制。只证明该固定实验本身，不构成一般算法优势或科研提效证明。
- **固定 Lean 核验**：可选固定案例，需要本机已有 Lean 4.33.1 工具链与 Windows 资源管理；只执行注册案例，不证明自然语言命题与形式陈述语义等价。**Lean 运行可靠性当前未通过**：历史间歇性意外超时未定位，有时代为成功（本机观测 `nat_add_zero` 约 8.85s），录制时必须如实说明，不能包装成稳定通过。非 Windows 或缺少工具链时报告不可用属正常。
- **历史结果**：18 论断/19 材料是 QA `test_fixture` 回放，不是真人审阅结论；v1.4 三份真实模型输出整体拒绝，不调用新模型补成绩；四义务保持 `open`、形式状态 `not_formally_verified`。

## 5. 推荐录制顺序（基于已有功能，不补造）

1. 启动 demo 模式 + 临时库，展示首页/研究项目与状态说明。
2. 方向探索：输入想法与资源 → 本地规则给出候选/澄清；说明这是规则能力，不是真实模型。
3. 文献工作台：Crossref/arXiv 真实检索（需要网络，每轮 1–5 条）→ 记录实际阅读的摘要/正文摘录（用户声明），展示“检索结果 ≠ 已读”。
4. 理论论证：创建公开案例（`curated_example`）或选择既有问题卡 → 保存核查材料、登记来源版本 → 对已登记 `artifact_id` 执行**显式原件读取**（展示字节哈希与转义预览）→ 记录一条明确标注 `test_fixture` 的意见 → 单条/整份 JSON 与 Markdown 导出。
5. （可选）运行一次固定随机 SVD 小实验，展示真实执行记录与限制。
6. （可选，工具链具备时）固定 Lean 案例，展示历史记录与当前不可靠边界；不得演示为稳定通过。

- **可以实际演示**：本地规则、真实文献查询（网络可用时）、阅读记录、理论卡/核查材料/原件读取/导出（35 原件已随仓库）、固定 SVD 小实验、固定 Lean 固定案例（须如实说明可靠性限制）。
- **仅历史记录，不要当场重放成新事实**：B 的四份浏览器下载副本、18/19 QA 回放、v1.4 模型输出、A 的 Lean 目标观测、8097 正式运行、真实研究库内容。

## 6. 关于 8104 预览

8104 是本机临时 QA 预览（同 112 文件冻结候选），模型/Lean/数值执行/下载由外围 QA 包装禁用。**这不等于对应源码缺失，也不等于正式功能已验收通过**；不要把它当作正式版或最终验收证据。

## 7. 停止、保存与维护入口

- 停止：终端 `Ctrl+C`。演示数据保存在你设置的临时库文件；正式本机记录路径是 `webapp/data/research.db`（本机实例自行创建），配置在 `webapp/.env`（不入库）。
- 已知限制：本地单人使用、无账户/项目权限/配额；固定实验无硬内存限额、无取消/恢复；Lean 资源管理仅在 Windows 验收；非 Windows 干净克隆时，证据登记读取可能显示 `fixed_registry_unavailable`（因注册清单记录本机绝对根路径，属保留的已知限制，不修改受保护清单字节），其他功能不受影响；favicon 404 为非阻塞已知项。
- 维护入口：`README.md`、`webapp/README.md`、`docs/研证技术架构.md`、`docs/理论论证工作流.md`、`docs/ROADMAP.md`、`docs/STATUS-2026-10-18.md`、`scripts/run_tests.py`、`webapp/tests/`。

## 8. 本机验证说明（如实标注）

本轮源码未变化，**没有重复**重跑 263 项 Python / 83 项前端全量回归、14 项真实网页检查或四份浏览器下载；复用既有冻结证据。本轮对将提交的文件集合做了一次有限可移植性验证：从本仓库新克隆到临时目录（临时库、demo 模式、禁用真实模型/Lean），确认 HEAD 与提交一致、无 `.env`/数据库/`.venv`/`.lake`/`tmp` 被跟踪；35 份登记公开原件与证据注册模块/清单的 SHA256 全部匹配，五份历史运行 JSON 可解析；在临时端口启动后 `/api/status`、`/api/library`、`POST /api/projects`（demo）分别返回 200/200/201，并能正常停止；克隆内的证据登记适配器可 `available=true` 且首次原件读回 `verified`。临时克隆、临时库和临时端口均已清理。

未验证/限制：实际执行使用的是本机已有 Python 3.12.14 解释器与已安装依赖，**不是队友全新系统或全新 pip 安装**；GitHub Actions 的实际结果与跳过范围需另行查看。按 `.gitattributes`，文本源码在克隆时按 LF 规范行尾，而开发机工作区是 CRLF，因此文本源码逐字节哈希可能与开发机冻结证据不同；但登记原件、注册器模块/清单和 Lean 源码锁使用 `-text` 保持字节精确。真实浏览器、真实千问、Lean 运行、8097/原库均未在本验证中执行。
