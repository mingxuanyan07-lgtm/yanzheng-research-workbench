# 来源选择与机构资源

核查日期：2026-10-14。此表指导选源，额度和权限可能变化；首次接入新渠道、遇到鉴权/限流或用户询问价格时查其当前官方文档，不把表内信息当永久承诺。

## 已实现的客户端

| 来源 | 作用 | 客户端与内容边界 |
|---|---|---|
| Crossref | 搜索出版题录与 DOI | `https://api.crossref.org/works`；无需注册即可查询，题录可缺摘要；摘要和底层论文可能有独立版权 |
| arXiv | 数学/AI 预印本、版本与摘要 | `https://export.arxiv.org/api/query`；Atom XML；连续请求至少间隔 3 秒；显示预印本和版本，DOI/期刊信息可缺失 |
| OpenAlex | 跨领域题录、主题/作者信息、开放获取位置 | `https://api.openalex.org/works`；脚本显式启用，有 Key 时用 `OPENALEX_API_KEY`；本客户端只做基本关键词查询 |

OpenAlex 当前基础匿名访问可用，注册 Key 有更高免费预算；服务另有按量收费。它不在客户端默认来源内，避免无意增加请求。已有 Key 不等于用户同意额外付费，先控制少量查询，遇到额度错误停止。

以每个提供方实际查询语法为准：arXiv 查询可用 `ti:`、`au:`、`all:` 与布尔组合；Crossref/OpenAlex 默认自然语言关键词。不要把某一数据库的高级检索式直接发送给其他渠道并假设含义一致。日期过滤也可能取出版年或预印本提交年，在记录和报告里说明所用渠道。

Crossref 返回的 DOI 与出版信息用于出处核对，不能自动证明论文科学结论正确。OpenAlex 的开放获取位置/arXiv PDF 链接是获取线索，链接存在不表示已下载、读过或获准再分发正文。

官方文档：

- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
- [arXiv API 手册](https://info.arxiv.org/help/api/user-manual.html)与[使用条款](https://info.arxiv.org/help/api/tou.html)
- [OpenAlex API](https://help.openalex.org/api/)、[鉴权](https://help.openalex.org/api/authentication/)与[额度/计费](https://help.openalex.org/access/pricing/)

## 可按需补充的渠道

Semantic Scholar 提供论文、摘要（可缺）、引文与推荐接口，可在已有环境工具可用时补引用链。共享匿名额度可能受限；独立 Key、缓存和退避有助稳定。当前客户端没有实现此适配器，不能在产物中写它已接入。

- [Semantic Scholar 官方 API](https://www.semanticscholar.org/product/api)
- [接口与字段文档](https://api.semanticscholar.org/api-docs/)

普通网页检索用于作者主页、代码与官方项目说明；打开真实来源再写依据，搜索摘要片段只作线索。优先使用环境已有的浏览/学术工具，不默认安装插件或注册收费服务。

## 复旦资源

用户的复旦身份可以用于本人访问图书馆订购的资源、核对相关全文和导出题录。按需提供数据库/图书馆入口，让用户在自己的认证会话里阅读；不把校园账号、VPN 会话或代理地址作为公开应用统一取全文的后端。

复旦提供 Web of Science、Scopus、IEEE、ScienceDirect 等资源；数学场景的 SIAM 使用说明明确有相关订购，同时禁止 spider 爬行访问。具体论文是否能访问以本馆订购范围为准。用户依法导出的 RIS/JSON 题录可通过本技能导入，保留 `provided_material` 或 `prior_project` 来源。

Web of Science Starter API 当前列出免费机构成员计划，订阅机构成员可申请；复旦学生可用机构身份询问平台/馆员资格。它主要提供基本题录和被引次数，不是全文接口；本客户端暂未实现。

Elsevier 的 Scopus/ScienceDirect API 需要核对具体数据和用途授权；Key 本身不授予访问权。正规 API 或文本数据挖掘申请可向图书馆/厂商确认。导出、缓存、公开展示与把订阅正文交给云端模型，分别核查相应条款；不从“我能阅读”推导“网站可向任何用户提供”。

- [复旦常用数据库](https://library.fudan.edu.cn/23/list.htm)
- [复旦校外访问](https://library.fudan.edu.cn/24/list.htm)
- [复旦 Web of Science 使用说明与导出](https://library.fudan.edu.cn/e6/e6/c42799a518118/page.psp)
- [复旦 SIAM 使用说明](https://library.fudan.edu.cn/e7/ed/c42799a518125/page.htm)
- [复旦电子资源使用管理办法](https://library.fudan.edu.cn/e7/e2/c42797a518114/page.htm)
- [Web of Science Starter API 计划](https://developer.clarivate.com/apis/wos-starter)
- [Elsevier API 鉴权](https://dev.elsevier.com/tecdoc_api_authentication.html)与[用途说明](https://dev.elsevier.com/use_cases.html)
