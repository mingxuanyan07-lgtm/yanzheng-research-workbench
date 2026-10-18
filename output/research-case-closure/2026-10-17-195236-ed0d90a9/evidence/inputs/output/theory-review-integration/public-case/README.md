# 比赛主案例：失败材料怎样推动可审阅规划

原问题：连续函数列在 [0,1] 上逐点收敛，极限是否连续？原卡与原输入笔记见 [original-context.json](original-context.json)。

真实历史：2026-10-16，千问 theory-workspace-v1.4，调用 `06c7c5d1b59d4e02906e1e58d7072ea2`，服务商 request_id `7efd52fe-e283-9989-a6d0-669d41205782`，4610输入＋1626输出＝6236 Token。[原文](historical-output.txt)保持原样；服务返回JSON成功只说明服务阶段结束，随后结构拒绝。第三条路线的义务ID长49字符，不能修短后宣称原件通过。

本次网页仅 historical_replay，回放ID `d96cd4eae7f9456e9b962d46b348632b`，新增真实调用0次。服务端读取公开历史响应，经真实适配器的MockTransport和生产理论API归档。历史输入和回放输入分别保留，回放的本地时间/调用ID不冒充2026-10-16真实网络调用。输入消息见上级 next-context-before-curation.json 与 browser-failure-download.json；后者是浏览器实际下载的本条材料。

具体审阅：保留49字符ID，指出 `proof_routes[0].evidence[0].claim` 超出唯一反例笔记的支持范围；其background标签不能使超范围归因成立。ε/3主干与反例识别有可保留价值，仍待独立审阅。浏览器QA反馈包含转义探针，探针不是数学内容；原文并未因此改动。

人工版：[curated-proposal.json](curated-proposal.json) 为既有公开人工整理，source=curated_example，model_call_id=null；补充一致收敛定理陈述的定位笔记后，再采用充分修订规划。不是模型纠错成功，不是新定理。页面明确采用该规划后，卡从v1到v2，四项义务仍open。

| 关键断言 | 实际笔记与引用位置 | 支持范围 | 待审阅项 |
|---|---|---|---|
| x^n反例否定原普遍命题 | quality-pointwise-note；The problems with pointwise convergence — x^n example | x<1与x=1的不同点态极限 | pointwise_limit；witness_domain |
| 一致收敛保持连续性 | demo-next-uniform-continuity-note；Uniform convergence定义、Three consequences首条定理 | 后补的定义与充分定理陈述 | uniform_quantifiers；relative_continuity |
| ε/3三项误差界 | 人工版路线1步骤1—3，以上定理笔记仅为背景支撑 | 具体推导来自人工整理，非原文逐字摘录 | 同一个N、固定f_N后选δ、相对定义域端点 |
| 任意δ>0合法反例见证 | 人工版路线2步骤2；原笔记记载x^n反例 | x=1-min(δ/2,1/2)是人工补充 | x∈[1/2,1)、0<距离<δ、函数差=1 |

每条笔记的完整内容、来源版本、URL、已读范围见 [notes-and-sources.json](notes-and-sources.json)，机器可读的逐条支持边界见 [claim-ledger.json](claim-ledger.json)。公开讲义链接：[UW–Madison Math 521](https://people.math.wisc.edu/~angenent/521.2017s/UniformConvergence.html)。该网页版本为2017 spring URL，实时HTML修订号未知、历史材料记录页面under construction；无原网页字节指纹。本窗口只读取既有归档，没有重新浏览网页或声称重新阅读全文。

导出证据：[本轮浏览器单条下载](../browser-failure-download.json)完整保留原文、历史调用关联与QA审阅；原人工整理的历史全案导出见 [2026-10-16公开导出](../../theory-quality-next/demo/05-theory-export.md)。后者明确是既有历史制品，不能当成本次浏览器整份下载成功。本次整份JSON/Markdown的浏览器权限被拒，API导出已由专项测试验证。

讲述重点：原始失败可追溯、意见具体、证据范围不扩大、人工版本归因明确、未决义务不隐藏。v1.4三份原样输出均整体拒绝，模型效果改善仍待真实验收和独立数学审阅。
