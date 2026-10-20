# Direction provenance preview — implementation report

- 分支：`feature/direction-provenance-preview`
- 工作树：`D:\天猫AI-feature-direction`（独立 worktree，基于候选提交 `31c787a2fea847dd107fa613052debd814ed6bcc`）
- 状态：Gap A（精确阅读笔记绑定）与 Gap B（方向修订预览/接受）均已实现，离线相关测试通过；**未启动服务、未调用模型/Lean、未使用真实 DB/.env、未修改主/候选工作树、未 push**。
- 证据副本：`D:\天猫AI\output\lead-review\20261020-direction-gaps\`（diff、测试日志、源码 SHA、git status）。

## 1. Gap A — 方向证据绑定精确阅读笔记

### 行为
- `supports` / `limits` 关联现在**必须**提供 `note_id`，且该 id 必须属于**同一检索轮次、同一来源**、`user_confirmed_reading=true`、有非空 `locator`/content/checked_at 的阅读笔记；不能借用其它 run/source、未确认、仅题录或无定位的笔记。
- `background` / `unverified_lead` 保持原行为：不绑定阅读笔记，`note_id` 必须为空；题录级 `background` 的 claim 继续由程序改写为题录线索说明。未核实来源仍只允许 `unverified_lead`。
- 每个合法绑定会持久化快照：`note_id`、`note_sha256`（整条笔记规范化 hash）、`content_sha256`、`run_id`、`source_id`、`level`、`locator`、`checked_at`、`origin`、`confirmed`，以及明确 caveat：**快照只表示用户确认阅读的自述摘录未变，不是程序核验，也不证明模型 claim 与摘录相符**。
- 模型契约：`workflow._schema()` 的 evidence 增加 `note_id`（string|null）；`build_messages` 的 system 规则说明 supports/limits 必须给出同一轮次/来源的已确认笔记 id，background/unverified_lead 用 null。
- 导出：`engine.render_plan` 的“课题与来源的关联”包含绑定笔记、locator、两种 hash 与 caveat；方向地图 JSON 原样保留快照；旧项目无快照的 evidence 仍可显示/导出（UI/plan 对缺失快照做兼容）。
- UI：`renderEvidence` 在来源行下显示“已绑定用户确认阅读笔记”、等级、阅读定位、摘要摘录（从当前项目 `reading_notes` 查找）、note_id、笔记/内容 SHA256、核对时间与 caveat。

### 主要文件
- `webapp/workflow.py`：schema、system prompt、`NOTE_BINDING_CAVEAT`、`_note_snapshot`、evidence 校验/规范化。
- `webapp/engine.py`：计划 Markdown 的证据关联输出。
- `webapp/static/app.js`：`renderEvidence` 的绑定笔记展示。
- `webapp/tests/test_direction_note_binding.py`：6 项聚焦测试。

### 测试覆盖（6）
- 正确绑定 + 快照字段/哈希 + claim 含 note id + 选择后仍保留 + 计划 Markdown/JSON 导出保留 + 修订后旧地图归档保留快照。
- 错误 run / 错误 source / 不存在 note 被拒绝。
- 未确认、错误等级、缺 locator、非 user_attested 来源的 note 被拒绝。
- supports 缺 note_id 被拒绝；background 不绑定可正常工作且“带 note_id 的 background”被拒绝。
- unverified 来源仅允许 unverified_lead 且无需 note。
- limits 同样使用该绑定/快照。

## 2. Gap B — 方向模型结果的显式预览、接受与拒绝

### 行为
- `/api/projects/{id}/advance` 的模型结果不再自动应用：校验成功后由 `workflow.store_proposal` 保存为 `project.workflow.pending_proposal`，方向地图、候选、推荐、选择与 `direction_versions` 均不变；模型调用事实记录为 `application_status=pending_review`。
- 预览对象保存：`id/source/created_at/status`、`model_call_id`、`prompt_version`、`input_revision`、`input_sha256`（模型输入 hash）、`context_sha256`（生成时上下文快照 hash）、`base_map`（旧地图完整快照）、`base_map_sha256`、`payload`（规范化建议）、`submitted_payload`、`diff`（阶段/问题/候选标题/推荐/假设/检索建议/修订说明/绑定笔记摘要）。
- 新输入被保存（`prepare_input`）时旧 pending 标记 `superseded` 并清除；新模型结果也会替代并归档旧 pending。
- 显式接受：`POST /api/projects/{id}/direction/proposals/{proposal_id}/accept`。接受前检查 pending 是否存在、当前阶段非 topic_selected、`context_sha256` 未变化；通过后归档旧地图（`direction_versions`，含 `proposal_id` 与 reason `skill_workflow_proposal_accepted`）、提交 pending answers、应用阶段/问题/候选/推荐、写 provenance/flow、模型调用状态改为 `applied`、记录事件。推荐仍不自动成为 `selected_topic_id`。
- 显式拒绝：`POST .../reject`（可带 reason）。方向地图与用户选择不变，pending 清除，提议状态 `rejected`，模型调用状态 `rejected_by_user`，记录事件；之后仍可重新请求。
- 过期：预览生成后若方向地图、简报/intake、阅读笔记、检索轮次、实验、或待处理回答/材料任一项变化（上下文快照 hash 变化），accept 返回 **409**（`status=stale_proposal`），不应用、不清除 pending；若期间已选题，同样 409。过期提议仍可显式拒绝。
- 兼容：旧的 `/api/projects/{id}/generate` 兼容路径仍是立即应用（不改变旧行为）；`workflow.apply_result` 保留为内部直接应用入口（旧测试/内部调用）；旧项目无 `pending_proposal/proposals` 字段时按缺省读取，页面行为不变。
- UI：方向页在存在 pending 时显示“待确认的方向修订”面板（阶段变化、候选标题前后、澄清问题、推荐理由、检索建议、修订说明、绑定笔记摘要、上下文/旧地图/输入 SHA256、接受/放弃按钮）；pending 期间隐藏旧的候选/问卷/生成面板，接受/拒绝后重新渲染；toast 明确“尚未应用”。
- API 变更均为新增端点；`/advance` 的响应仍是项目 JSON，但语义从“已应用”变为“已保存待确认预览”，这是本任务要求的显式语义变化（见 residual）。

### 主要文件
- `webapp/workflow.py`：`ProposalConflict`、pending/proposals 初始化、`_proposal_context_snapshot/_sha256`、`_proposal_diff`、`_invalidate_pending`、`_proposal_status`、`_apply_direction_payload`、`store_proposal`、`accept_proposal`、`reject_proposal`；`apply_result` 复用了统一应用 helper。
- `webapp/app.py`：`DirectionProposalDecision` 模型；accept/reject 路由；`advance_skill` 的成功分支改为保存预览。
- `webapp/static/app.js`：`renderDirectionProposal`、`performDirectionProposal`、点击处理、pending 时的渲染分支、toast 文案。
- `webapp/tests/test_direction_proposal.py`：6 项聚焦测试；`test_workflow_api.py` 按新语义调整（4 项）。

### 测试覆盖（Gap B 6 + 调整后的 API 4）
- 预览保存且不改变 map/selection/versions；pending 含 input/context/base-map hash 与 diff；模型调用 pending_review。
- accept 应用一次、归档旧图（reason/proposal_id）、状态 accepted、call applied、推荐不等于选择；重复 accept 被拒绝。
- reject 保留旧 map/selection/versions、提议 rejected、call rejected_by_user；之后可重新预览。
- 新请求 supersede 旧 pending；旧 id accept 失败，新 id accept 成功。
- 本地后续编辑（新增 reading note）使 accept 409 stale_proposal，且 pending 不被擦除，可显式 reject。
- 预览后选题使 accept 409，已选题状态/选择保留。

## 3. 测试执行（全部使用工作树 `webapp/.venv/Scripts/python.exe` 与 `scripts/run_tests.py`）

命令模式：`python -X utf8 -B scripts/run_tests.py --suite web --pattern '<file>'`（工作树根目录）。

| 测试文件 | 结果 |
|---|---|
| test_direction_note_binding.py | Ran 6 · OK |
| test_direction_proposal.py | Ran 6 · OK |
| test_workflow.py | Ran 11 · OK |
| test_workflow_api.py | Ran 4 · OK |
| test_model_api.py | Ran 6 · OK |
| test_planner.py | Ran 6 · OK |
| test_backend.py | Ran 5 · OK |
| test_literature_api.py | Ran 7 · OK |
| test_theory_api.py | Ran 8 · OK |

合计 **59 个 Python 测试通过、0 失败/错误**。前端 VM 测试：`node --test webapp/tests/frontend/test_app_ui.cjs` → **44/44 通过**（含新增 1 项 pending 预览面板渲染）。`py_compile` 8 个改动 Python 文件通过。

未运行：完整 `--suite web`/`all`（会启动 Lean 集成用例，任务明确禁止 full Lean）、skills 套件、A/B evidence registry 套件、任何服务/浏览器/模型调用。

## 4. 变更与 diff

`git diff --stat`（实现提交前）：
```
 webapp/app.py                     |  23 +++-
 webapp/engine.py                  |  12 +-
 webapp/static/app.js              |  93 ++++++++++++++-
 webapp/tests/test_workflow.py     |  19 ++-
 webapp/tests/test_workflow_api.py |  61 ++++++++--
 webapp/workflow.py                | 259 ++++++++++++++++++++++++++++++++----
 webapp/tests/frontend/test_app_ui.cjs | 25 +++-
 + 2 new test files (test_direction_note_binding.py, test_direction_proposal.py)
```
完整 diff：`D:\天猫AI\output\lead-review\20261020-direction-gaps\direction-gaps.diff`；源码 SHA：同目录 `source-sha.json`。

## 5. 残余缺口与风险

1. **文档未同步**：根 `README.md`、`webapp/README.md`、`docs/研证技术架构.md` 仍描述“方向修订校验后直接应用、差异预览尚待实现”“每次 /advance 直接应用”。实现已改变该语义，文档应随分支评审单独更新，避免把本候选误当正式发布口径。
2. **API 语义变化**：`/advance` 对已有 API 消费者从“应用”改为“保存待确认”；新端点 accept/reject 为增量。UI 已更新，但外部脚本/旧文档需同步。
3. **契约层**：note 绑定与 pending 状态只在 `workflow.py` 业务层校验；`skills/research-direction-guide/scripts/validate_direction_map.py` 未扩展校验 note_snapshot（额外字段被容忍）。直接手改 JSON 仍可绕过，需要后续把不变量下沉到方向契约或服务端写入口。
4. **过期 pending 的清理**：上下文变化后不会自动标记 stale/移除 pending，只在 accept 时返回 409；页面仍显示 pending（可显式拒绝）。如需自动提示，可在工作区视图计算 stale 标记（下一步）。
5. **UI diff 粒度**：预览为字段级摘要（阶段/候选标题/问题/推荐/检索建议/绑定笔记/hash），非逐候选字段全文 diff；拒绝理由输入框未加入 UI（API 支持 reason，默认空）。
6. **历史增长**：`workflow.proposals` 保留完整 base_map/payload/submitted_payload 供审计，没有数量上限；长时间高频修订会增大项目 JSON。
7. **前端覆盖**：新增预览面板有 1 项 VM 渲染测试；accept/reject 的真实浏览器/服务链路未在本任务执行（任务禁止服务操作），需在允许环境的端到端验收中补跑。
8. **快照语义**：绑定笔记仍是用户自述 + 程序保存；caveat 明确内容 hash 一致不等于科学核验，claim 与摘录是否相符仍需人工审查；Gap A 不改变这一边界。

## 6. 复核方式

```powershell
git -C D:\天猫AI-feature-direction status
git -C D:\天猫AI-feature-direction log --oneline -2
git -C D:\天猫AI-feature-direction show --stat HEAD
# 测试（工作树根目录）
webapp\.venv\Scripts\python.exe -X utf8 -B scripts\run_tests.py --suite web --pattern test_direction_proposal.py
webapp\.venv\Scripts\python.exe -X utf8 -B scripts\run_tests.py --suite web --pattern test_direction_note_binding.py
node --test webapp\tests\frontend\test_app_ui.cjs
```
