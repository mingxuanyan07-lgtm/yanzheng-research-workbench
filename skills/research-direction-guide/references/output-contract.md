# 课题地图 v1 输出契约

本格式用于“问问题→给方向”的聊天状态，以及未来网站后端返回值。它不会执行检索、调用 Qwen 或启动实验。旧展示的 `research_direction_map_demo` 和 `research_task_handoff` 是不同格式；接入时应做显式适配，不覆盖旧文件。

顶层字段：

| 字段 | 类型/取值 | 用途 |
|---|---|---|
| schema_version | `"1.0"` | 契约版本 |
| record_type | `"research_direction_map"` | 真实规划记录 |
| created_at | ISO 8601 字符串，含时区 | 实际生成时间 |
| stage | `"clarifying"` / `"directions_ready"` / `"topic_selected"` | 正在澄清、已给候选、用户已选 |
| initial_idea | 非空字符串 | 用户的原始方向 |
| intake | 对象 | `known` 为已知事实对象；`assumptions` 为明确假设列表；`unknowns` 为缺失信息列表 |
| questions | 对象数组 | 每项 `id`、`question`、`why_it_matters`；只列尚未回答的问题 |
| provenance | 对象 | `generator`、`live_search_performed`、`external_model_api_called`、`new_experiments_executed`；布尔状态须依据实际调用 |
| search_log | 对象数组 | 每项 `query`、`searched_at`、`outcome`（`executed` / `planned`） |
| sources | 对象数组 | 见下方来源格式 |
| topics | 对象数组 | 见下方课题格式；澄清阶段允许为空 |
| recommendation | 对象 | `topic_id` 可为 null；`reason`；`would_change_if` 列表 |
| selected_topic_id | 字符串或 null | 只有用户已明确选择时填写 |
| next_action | 非空字符串 | 下一个操作 |

来源格式：`id`、`title`、`authors`、`year_or_version`、`url`、`origin`（`this_run` / `provided_material` / `prior_project`）、`verification`、`locator`、`checked_at`。`id` 和 `title` 为非空字符串；`authors` 可为非空字符串或非空字符串列表，`year_or_version` 可为非空字符串或整数年份。

`verification` 可取 `unverified`、`metadata_checked`、`abstract_checked`、`fulltext_checked`。未核实条目的 `url` / `checked_at` / `locator` 可为 null；不要填虚构链接。已核实条目必须给真实网址和日期；已读摘要/正文须给位置，如“Abstract”或“§4.5”。`fulltext_checked` 表示读过相关正文，不意味着通读或证明该论文所有结论。

课题格式：`id`、`title`、`question`、`known_work`、`potential_difference`、`novelty_status`（固定 `needs_review`）、`evidence`、`first_validation`、`resources`、`stop_conditions`、`risks`、`open_questions`、`readiness`（`provisional` / `ready_to_plan`）。前三项与 `potential_difference` 为非空字符串；`known_work` 为非空字符串或非空字符串列表；`resources`、`stop_conditions`、`risks`、`open_questions` 为字符串列表。

`evidence` 每项为 `source_id`、`relation`（`supports` / `limits` / `background` / `unverified_lead`）、`claim`，可选 `note_id` 与 `note_snapshot`。未核实来源只可作为 `unverified_lead`；只核过元数据的来源不能标记 `supports` / `limits`，用作 `background` 时仅描述书目信息，不推断科研内容。`supports` / `limits` 必须绑定同一轮次、同一来源、用户确认且有 locator 的阅读笔记 `note_id`，并提供结构完整的 `note_snapshot`：`note_id` 与 evidence.note_id 一致；`note_sha256`、`content_sha256` 为 64 位小写十六进制；另有 `run_id`、`source_id`、`level`、`locator`、`checked_at`、`origin=user_attested`、`confirmed=true`、`caveat`；`background` / `unverified_lead` 不得绑定阅读笔记。若阅读笔记带有用户声明的原文版本/文件哈希/范围三元组，`note_snapshot` 可附带 `source_version`、`source_sha256`、`source_sha256_scope`、`source_fingerprint_caveat`，但这只是用户声明，程序不读取/认证原文，不证明内容真实性、版本正确性或科学结论。已读内容的实际支持范围仍须人工审阅。

`first_validation` 为 `steps`（列表）、`baseline`（字符串）、`metrics`（列表）、`success_signal`（字符串）。理论课题的 baseline 可说明对照命题或已知定理，metrics 可为证明目标与未闭合依赖，不强行套用数值指标。`resources` 是字符串列表；时间与成本可写估计，注明依赖。`ready_to_plan` 的步骤、指标、资源与停止条件列表均不能留空。

一致性要求：

- `clarifying` 阶段应有待回答问题，不能设置已选课题；暂定候选标记 `provisional`。
- `directions_ready` 至少有一条候选，未选择时 `selected_topic_id=null`。
- `topic_selected` 必须指向候选中的真实 id，且 `intake.known.selection_basis` 保存用户的选择证据。
- 推荐与选择是两个字段。推荐不等于用户选择；推荐也可能是暂定的。
- 此 Skill 到方向交接为止，`new_experiments_executed=false`；旧离线数据只能记作已有材料。
- 用浏览工具检索可以令 `live_search_performed=true`，但不代表调用了百炼或 Qwen API；当前聊天生成与独立代理评估由 `generator` 说明，`external_model_api_called` 仅记额外外部模型接口调用。
- 有访问失败时保留计划/失败信息，不伪装检索成功。结构通过不代表资料和创新性已确认。
