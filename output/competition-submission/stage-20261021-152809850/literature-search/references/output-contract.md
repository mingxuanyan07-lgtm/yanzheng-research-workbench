# 文献检索记录 v1 输出契约

`research_literature_search` 保存真实查询、导入文献与内容核对的状态。它与课题地图中的 `sources` 对齐，但允许作者、年份等字段暂缺；交接必须显式适配。校验器只检查结构与一致性，不证明论文真实、作者信息准确、阅读充分、课题新颖或结论成立。额外字段允许存在，例如客户端用于保存版本与字段冲突的 `versions`、`field_conflicts`。

## 顶层字段

| 字段 | 类型/取值 | 含义 |
|---|---|---|
| schema_version | `"1.0"` | 契约版本 |
| record_type | `"research_literature_search"` | 检索记录类型 |
| created_at | 含时区的 ISO 8601 时间 | 本记录实际生成时间 |
| request | 对象 | 检索意图及参数，见下节 |
| provenance | 对象 | 生成与执行事实，见下节 |
| search_log | 对象数组 | 各查询的实际状态；纯导入允许为空 |
| sources | 对象数组 | 去重后的文献；失败、无结果、计划允许为空 |
| evidence | 对象数组 | 内容证据；只有题录时可以为空 |
| limitations | 非空字符串数组，可为空数组 | 未覆盖范围、失败、缺失元数据、全文未读等实际限制 |
| next_action | 非空字符串 | 下一步可执行动作 |

字符串数组中的每一项必须非空，数组是否能为空由字段单独说明。以下字段均为必填；未知值写 `null` 或契约允许的空数组，不生成猜测内容。

## 检索请求与执行事实

`request` 包含：

- `idea`：非空字符串，保存用户的方向或任务。
- `queries`、`providers`：非空字符串数组。保留实际检索式和数据源标识；通常为 `crossref`、`arxiv`、`openalex`。有查询日志时各至少一项；纯导入允许两者为 `[]` 且 `search_log=[]`。浏览工具或其他正式接口可使用其他明确标识。
- `year_from`、`year_to`：正整数或 `null`；两者都有值时起始年不能晚于结束年。
- `max_results_per_query`：正整数。

`provenance` 包含 `generator`（非空字符串）、`live_search_performed`（布尔）、`external_model_api_called=false`、`new_experiments_executed=false`。本契约用于检索和阅读交接；客户端不调用额外模型接口，不执行实验。当前聊天中的模型分析不算调用额外外部模型 API。

`live_search_performed` 必须等于“本记录至少一条查询的 `attempts>0`”。实际请求全部失败或返回空数组也算尝试过实时检索；只有计划、配置错误未发请求、导入或查看历史记录均不算。此字段表示执行事实，不表示成功或已读论文。

## 查询日志

每项包含 `id`、`provider`、`query`、`status`、`searched_at`、`attempts`、`result_count`、`error`。

`id` 为记录内唯一的非空字符串；`provider`、`query` 必须分别属于 `request.providers`、`request.queries`。`attempts` 为非负整数，统计实际发起的请求次数，包括重试；`searched_at` 为实际尝试时间（含时区 ISO 8601），无尝试则为 `null`。

| status | attempts | searched_at | result_count | error |
|---|---|---|---|---|
| planned | `0` | `null` | `0` | `null` |
| success | 至少 `1` | 实际时间 | 至少 `1` | `null` |
| empty | 至少 `1` | 实际时间 | `0` | `null` |
| failed | 非负整数 | 有尝试时实际时间，否则 `null` | `0` | 非空字符串 |

`failed` 且 `attempts=0` 可用于缺少配置等未发起请求的情况。失败必须保留原因；不要用旧结果填补失败查询。`empty` 表示本次查询没有返回可用条目，不能推断不存在相关研究。`result_count` 是该查询解析后的候选数，先于跨查询去重或后续筛选；关联的去重文献数量可以更少，不能更多。

## 来源与阅读等级

每项 `source` 包含：

| 字段 | 类型/取值 |
|---|---|
| id、title | 非空字符串，id 在记录内唯一 |
| authors | 非空字符串数组，可为空数组 |
| year_or_version | 正整数、非空字符串或 `null` |
| url | 真实 HTTP(S) 文献地址或 `null` |
| origin | `this_run` / `provided_material` / `prior_project` |
| verification | `unverified` / `metadata_checked` / `abstract_checked` / `fulltext_checked` |
| locator | 实际核对位置的非空字符串或 `null` |
| checked_at | 实际核对的 ISO 日期/时间或 `null` |
| identifiers | 对象：`doi`、`arxiv_id`、`openalex_id`，值为非空字符串或 `null` |
| abstract | 接口返回或已确认的摘要字符串或 `null` |
| access | 对象：`is_open_access` 为布尔或 `null`；`landing_page_url`、`pdf_url` 为 HTTP(S) URL 或 `null`；`license` 为非空字符串或 `null` |
| retrievals | 对象数组，逐项保存查询归属 |

不能使用占位链接或含凭据的 URL。作者、年份和 URL 不齐全的条目保留为 `unverified`；访问过接口时可以保留真实 `checked_at` 和 `retrievals`，不因此升级阅读等级。`metadata_checked` 及以上必须有非空作者数组、年份或版本、真实 URL 和实际核对日期。

接口返回 `abstract`、提供 `pdf_url` 或标记开放获取，只表明这些字段可用。**脚本自动检索最多标记 `metadata_checked`**；不自动标记摘要或正文已读。阅读后才可把等级升级为 `abstract_checked` / `fulltext_checked`，并填写 `locator`（如 `Abstract`、`§4.5, Theorem 4.3` 或正文页码）及实际 `checked_at`。`fulltext_checked` 指读过与该证据有关的正文，不表示通读或核验所有结论。

`retrievals` 每项含 `search_id`、`provider`、`url`、`retrieved_at`。前三项为非空字符串，`url` 为实际请求/检索位置的 HTTP(S) 地址，`retrieved_at` 为含时区的实际获取时间。`search_id` 必须指向本记录中的 `success` 日志，`provider` 必须与该日志一致。同一论文被多个查询找到时保留多项归属；查询可返回多个论文。

`this_run` 必须有至少一项成功查询归属。用户提供材料与旧项目资料分别保持 `provided_material`、`prior_project`，不能因为本次读入而改为 `this_run`，也不能因导入宣称实时检索。纯导入或历史来源可以没有 `retrievals`。若历史来源在本次又被检索找到，可保留历史 `origin` 并增加本次查询归属；不把旧记录中的日志 ID 带进当前记录。

### 导入格式

`scripts/import_literature.py` 支持 JSON 与 RIS，不调用网络、不读取全文。JSON 输入可为裸来源列表、单个题录对象、含 `sources` 或 `references` 数组的记录、旧演示的 `topics[].references` 或交接的 `selected_topic.references`。RIS 要求标准 `TY  -` 开始和 `ER  -` 结束，解析 `AU/A1`、`TI/T1/CT`、`PY/Y1/DA`、`DO`、`UR`、`AB/N2` 及字段续行；BibTeX 尚未实现。

导入输出重新赋来源 ID，保存输入文件名与 SHA-256 等 `import_details`。来源默认 `unverified`，`checked_at/locator=null`；旧阅读状态保留为额外 `prior_review` 信息，不视作本轮已核对。导入摘要也不会升级阅读等级。文件里的未知字段不充当真实性证明，缺少题名的条目跳过并写出原因。其他导出格式先转换为 JSON/RIS。

## 证据

每项为 `source_id`、`relation`、`claim`、`locator`：

- `source_id`：必须引用本记录真实来源 ID。
- `relation`：`background` / `supports` / `limits` / `unverified_lead`。
- `claim`：非空字符串，说明来源与研究问题的实际关系。
- `locator`：实际证据位置的非空字符串或 `null`；`supports` / `limits` 时必填。

`unverified` 来源只允许 `unverified_lead`。`metadata_checked` 来源只允许 `background`，内容仅描述已核对的书目信息；不能从标题、检索排名或机器生成概述推断科研结论。`supports` / `limits` 必须引用实际阅读过摘要或正文的来源，同时给来源和证据填写定位。摘要能支撑的范围与正文不同，必须在 `claim` 中保留实际边界；定理假设、证明细节和实验数值一般需要对应正文。语义是否真正被原文支持仍须审阅，结构校验不能判定。

## 与方向探索 Skill 交接

方向地图 v1 的来源字段为 `id/title/authors/year_or_version/url/origin/verification/locator/checked_at`。客户端 `export_direction_sources` 仅显式挑选这些字段，并检查作者和年份完整性；缺少必需元数据的来源跳过并报告原因，不能虚构补齐。检索独有的标识符、摘要、可访问性和归属留在原始记录中，方向地图通过来源 ID 与原始记录追溯。

交接时保持 `origin` 和阅读等级；不能把所有导入文献写成本次查询来源，也不能把 API 摘要存在当作读过摘要。方向记录自己的检索日志必须依据实际状态生成：本次尝试过的查询为 `executed`，纯计划仍为 `planned`；失败/空结果及不可用条目的原因同时保留在交接说明中。原始检索 JSON 是完整执行事实的依据。

运行 `scripts/validate_literature_record.py <记录.json>` 检查结构与一致性。验证通过后仍要审阅检索覆盖、数据真实性、版本关系、证据范围以及新颖性待核对的问题。

## 浏览工具的落盘方式

客户端只自动产生接口元数据；浏览工具得到的实际内容由执行者按同一契约记录。直接打开种子论文可用 `provider="browser_direct_lookup"`，`query` 保存实际种子题名或 URL，成功后才填写对应 `search_log` 和 `retrievals`。这种直接查找与关键词搜索在报告里区分；打开派生正文不额外虚构一次关键词检索。

对导入论文阅读正文时，保持 `origin=provided_material/prior_project`，更新实际版本、`verification`、`checked_at` 和 `locator`。可用额外 `content_review_log` 保存本轮逐次阅读操作，例如：

```json
{"source_id":"S001","url":"https://arxiv.org/html/0909.4061v2","read_at":"2026-10-14T19:00:00+08:00","locator":"§4.5","version":"0909.4061v2","method":"browser"}
```

此片段仅演示字段，时间和位置须替换为实际发生的操作。它不能单独替代完整记录或把导入论文改成本轮新检索来源。阅读后添加的 `evidence` 仍引用原 `source_id`，逐项填写支撑关系和实际位置。
