# Direction robustness (declared fingerprints, stale preview, bounded contract) — implementation report

- 分支：`feature/direction-robustness`
- 工作树：`D:\天猫AI-feature-robustness`（独立 worktree；起点提交 `9a11d0f749b016b109aae98634d0934705983bb9`）
- 状态：三项均已实现并通过离线测试；**未启动服务、未调用模型/Lean、未使用真实 DB/.env、未修改主/候选工作树或冻结证据、未 push**。
- 证据副本：`D:\天猫AI\output\lead-review\20261020-direction-robustness\`（报告、READY、diff、测试日志、源码 SHA）。

## 1. 论文/来源版本 + 内容指纹链（用户声明，非认证）

### API / 存储
- `webapp/app.py::ReadingNote` 新增可选字段：`source_version`（≤300）、`source_sha256`（64 位小写十六进制）、`source_sha256_scope`（`user_declared_fulltext_file` / `user_declared_metadata`）。
- 三项必须**同时提供或同时留空**；部分提供、空版本、大小写/长度/非十六进制哈希、非法 scope、未知字段（如注入 `source_fingerprint`）均返回 422，且不保存。
- 合法三元组保存到阅读笔记：顶层三字段 + `source_fingerprint` 追加快照（`source_version/source_sha256/source_sha256_scope/declared_at/origin=user_declared/caveat`）。caveat 明确：**用户声明；程序没有读取/认证原文，不证明内容真实性、版本正确性或科学结论**。
- 未提供三元组的旧式阅读笔记完全兼容（不写 null 字段、不写 `source_fingerprint`）。

### 方向证据传播 / 展示 / 导出
- `workflow._note_snapshot` 在用户声明的三元组存在且连贯时，把 `source_version/source_sha256/source_sha256_scope/source_fingerprint_caveat` 带入方向证据快照；缺失时不写这些字段，保持旧快照兼容。
- 计划 Markdown（`engine.render_plan`）的“课题与来源的关联”和“阅读记录”部分会输出用户声明版本/文件 SHA256/范围与“未认证”caveat；未登记时输出“未登记原文版本/指纹（程序未读取/认证原文）”。项目 JSON 导出原样包含阅读笔记与方向快照。
- `static/app.js` 阅读记录展示声明指纹（或“未登记原文版本/指纹”）；阅读表单新增三个输入（版本、SHA256、范围），三项须同有同无，只保存声明；方向证据与待确认预览显示绑定笔记的声明版本/指纹。
- 明确不做：抓取/下载原文、任意路径/文件读取、把声明 hash 当作已验证内容或科学结论。

### 测试（`webapp/tests/test_reading_fingerprint.py`，5 项）
- 合法三元组持久化/回读、caveat 不含“已验证/authenticated”类错误声称；
- 部分三元组、大小写/长度/非十六进制哈希、非法 scope、超长版本、注入 `source_fingerprint` 字段均 422 且无记录；
- 旧笔记无指纹兼容；
- 方向 supports 快照带出声明版本/哈希/范围/caveat 并进入计划 Markdown 与 JSON；无指纹时显示“未登记原文版本/指纹”；
- `_declared_source_fingerprint` 拒绝不连贯三元组。

## 2. 过期 pending UI

- `webapp/workflow.py::pending_proposal_is_stale(project)`：基于既有 `context_sha256` 只读计算；无 pending 返回 False，字段缺失/损坏时 fail closed 为 True，不修改项目。
- `webapp/app.py`：新增只读视图 `project_with_pending_status`，仅在 `GET /api/projects/{id}` 上附加派生字段 `workflow.pending_proposal_stale`；持久化项目 JSON 中不写该字段（测试验证）。
- `static/app.js`：待确认面板在 stale 时显示“上下文已变化 · 需重新生成”与错误提示，禁用“接受并应用修订”，保留“放弃本次修订”；`performDirectionProposal` 对 stale 的 accept 直接拒绝并提示。pending 存在时，`mutateProject` 会额外 GET 项目以刷新派生 stale 标志。
- 既有 accept 的 409 stale_proposal 语义不变；reject 始终可用。
- 测试：`test_direction_proposal.py` 增加 GET 标志、持久化不含标志、accept 409、reject 仍成功、reject 后标志清除；前端 VM 测试增加 stale badge/disabled accept/enabled reject 与 fresh 面板对比。

## 3. 有界提议历史 + skills 方向契约校验

### 有界历史
- `workflow.PROPOSAL_HISTORY_LIMIT = 50`；`_prune_proposal_history(flow)` 在追加后保留最近 50 条，**活动 pending 永不被清掉**（即使不在尾部也有保底保留），累计 `workflow.proposals_pruned_count`。
- 旧项目无相关字段时按缺省处理；初始项目写入 `proposals_pruned_count=0`。
- 测试 `test_direction_robustness.py`：55 次提议后长度 50、清理计数 5、pending 在历史中且仍可接受；pending 不在尾部时保底保留。

### skills 契约校验
- `skills/research-direction-guide/scripts/validate_direction_map.py`：
  - evidence 允许并仅允许 `source_id/relation/claim/note_id/note_snapshot`；
  - `supports`/`limits` 必须提供非空 `note_id` 与结构化 `note_snapshot`（`note_id/note_sha256/content_sha256/run_id/source_id/level/locator/checked_at/origin/confirmed/caveat`）；note/content 哈希必须是 64 位小写十六进制，`note_id` 必须与 evidence.note_id 一致，`level` 必须 abstract/fulltext，`origin=user_attested`，`confirmed=true`，locator/caveat 非空，checked_at 为 ISO；未知字段失败；
  - 可选的用户声明三元组若出现，必须齐全且哈希/范围合法并带 caveat；缺失则允许（“未登记”兼容）。
  - `background`/`unverified_lead` 出现 `note_id`/`note_snapshot` 绑定即失败。
  - 只做结构校验，不解析 note_id、不读取项目/原文，fail closed。
- 聚焦测试 `webapp/tests/test_direction_contract.py` 通过 `importlib` 直接加载该脚本，覆盖合法绑定（含/不含声明指纹）、缺 note_id/快照、坏哈希/缺字段/错 level/origin/confirmed、background 绑笔记、未知字段、不完整声明三元组。

## 4. 文档更新（只改陈旧处）

- 根 `README.md`：数据契约与阅读说明改为“用户声明的版本/哈希三元组（程序不读取/认证原文）”。
- `webapp/README.md`：阅读步骤、证据边界、项目字段、API 列表更新（待确认预览、accept/reject、声明指纹、50 条提议上限）。
- `docs/研证技术架构.md`：阅读笔记/方向证据/待确认预览/有界历史说明；数据与接口表；复核日期更新为 2026-10-20。
- `docs/ROADMAP.md`：当前证据与修订审阅状态改为“声明指纹 + stale 预览 + 有界历史”；下一阶段保留“经独立核验/认证的版本与内容指纹”；更新日期 2026-10-20。
- `skills/research-direction-guide/references/output-contract.md`：evidence 契约补充 `note_id`/`note_snapshot`、哈希/定位/等级/来源要求与“用户声明指纹不是认证”的说明；background/unverified_lead 不得绑定笔记。
- 未声称：独立论文版本锁定、全文内容认证、程序读取原文、正式发布接受。

## 5. 测试执行

工作树根目录，使用 `webapp\.venv\Scripts\python.exe`（指向仓库既有 venv 的未跟踪 junction，离线、无安装）与 `scripts/run_tests.py`：

| 项目 | 结果 |
|---|---|
| 新增/相关 Python：test_reading_fingerprint / test_direction_robustness / test_direction_contract / test_direction_proposal / test_direction_note_binding | 5 + 2 + 5 + 6 + 6 = **24 passed** |
| **full `--suite all`（web+skills）** | **Ran 324 tests · OK (skipped=4)**；4 个 skip 是既有 Windows symlink 能力拒绝（WinError 1314）在 evidence IO 测试中的显式跳过，不是本次功能失败；本环境 Lean 集成用例本次通过，未出现环境失败 |
| `node --test webapp/tests/frontend/test_app_ui.cjs` | **45/45 passed**（含新增 stale 面板与声明指纹展示断言） |
| `node --check webapp/static/app.js`；`py_compile` 改动 Python | 通过 |
| 额外 targeted 回归（workflow/workflow_api/model/planner/backend/literature/theory 等） | 全绿（见首次运行日志；full all 已覆盖） |

约束遵守：未启动服务、未调用模型/Lean、未读写真实 DB/.env、未改主/候选工作树、未提交旧 ZIP/冻结证据、未 push。

## 6. 残余缺口与风险

1. **声明 ≠ 认证**：程序不抓取/读取原文，只保存并回显用户输入的版本/文件哈希；不能证明文件内容、版本正确性或 claim 与原文相符。UI/计划均明确标注，无 fake authentication 文案。
2. **入口层校验**：三元组一致性只在 `ReadingNote` API 层强制；直接手改项目 JSON/SQLite 可绕过。方向快照的 note_sha256 在生成时计算，后续外部修改阅读笔记不会被程序自动重算比对（阅读笔记本身为追加保存，无编辑 API）。
3. **契约收紧的兼容性**：skills 方向契约现在拒绝“有 supports/limits 但无 note_id/快照”的旧地图；`GET /api/projects/{id}/export` 会调用 `engine.assert_valid`，因此严格旧地图可能在导出时被拒。本任务按“fail closed”要求执行，未提供迁移；如需兼容旧库，应另加只读迁移/白名单策略并验收。
4. **stale 标志只在 GET 项目视图派生**：`/advance`、`/reading-notes` 等 mutate 响应不附带该字段；前端在 pending 时补 GET，但外部 API 客户端需自行 GET 才能看到 `pending_proposal_stale`。项目持久化 JSON 不含该派生字段。
5. **历史裁剪**：超过 50 条的提议正文被裁剪，仅保留计数；审计追溯只能依赖此前导出，属于有界存储取舍。
6. **UI 仍是摘要级预览**：字段级候选 diff 和拒绝理由输入未加入 UI（reject API 支持 reason）。
7. **未做真实浏览器/服务/模型/Lean 验收**；full suite 的 Lean 用例通过仅是本机测试事实，不构成 Lean 可靠性或正式接受。前端只有 VM 渲染测试，无实际浏览器链路。
8. 方向契约只校验结构，不解析 note_id 对应项目或原文；该项按任务要求保持“结构 only”。

## 7. 复核

```powershell
git -C D:\天猫AI-feature-robustness status
git -C D:\天猫AI-feature-robustness log --oneline -3
git -C D:\天猫AI-feature-robustness show --stat HEAD
webapp\.venv\Scripts\python.exe -X utf8 -B scripts\run_tests.py --suite all
webapp\.venv\Scripts\python.exe -X utf8 -B scripts\run_tests.py --suite web --pattern test_reading_fingerprint.py
webapp\.venv\Scripts\python.exe -X utf8 -B scripts\run_tests.py --suite web --pattern test_direction_contract.py
node --test webapp\tests\frontend\test_app_ui.cjs
```
