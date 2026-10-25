# 研证 · 魔搭创空间在线演示

本目录用于独立部署公开演示，不包含本机研究库、个人项目、模型密钥或私人验收档案。云端实例使用自己的临时状态目录，各访客的数据隔离；本机 8097 服务和本地研究数据保持独立。

## 生成可上传目录

先完成并冻结云端入口和最新界面，再在仓库根目录运行：

```powershell
webapp\.venv\Scripts\python.exe -X utf8 -B deploy\modelscope\build_bundle.py --plan
webapp\.venv\Scripts\python.exe -X utf8 -B deploy\modelscope\build_bundle.py
```

若本轮界面新增 CSS，应明确加入其路径，例如：

```powershell
webapp\.venv\Scripts\python.exe -X utf8 -B deploy\modelscope\build_bundle.py --extra-static-css webapp/static/实际文件名.css
```

脚本输出一个新的 `output/modelscope-demo/<时间与随机编号>/stage/`。只把这个 **stage 目录里的文件** 作为创空间仓库或 Docker 构建上下文，不要上传个人仓库根目录、整个 `output/` 或旧队友 ZIP。

构建器只读取明确列出的运行代码、静态素材、Skill 文件、固定 Lean 案例源码、五份历史 JSON 和登记的 35 份公开原件；不会递归复制工作区。`SOURCE-MANIFEST.json` 记录实际发布字节和基础提交，未提交的界面与云端入口标为 `working-tree-snapshot`，不冒充已经提交的 Git 版本。构建器不安装依赖、不启动 Docker、不调用模型、不上传。

## 容器与平台配置

- Dockerfile 使用 `python:3.12-slim`，按 `webapp/requirements.txt` 安装固定版本：FastAPI 0.143.0、Uvicorn 0.54.0、HTTPX 0.28.1、NumPy 2.5.4。构建需要平台依赖下载网络；尚未进行云端构建时，不应声称已验证全新安装。
- 容器以非 root 用户 UID 1000 运行，监听 `0.0.0.0:7860`，启动 `cloud_app:app`，**单 worker**。不要增加 worker 或副本，否则当前进程内访客状态不能保证一致。
- 在创空间配置匹配的 Docker 类型与端口 7860。`PUBLIC_ORIGIN` 必须是应用实际运行域名的 HTTPS origin，如 `https://实际应用运行域名`，不能填写创空间介绍页 URL。
- `YANZHENG_DEMO_STATE_ROOT` 默认 `/tmp/yanzheng-demo`，须可写。演示数据不是持久备份，容器重建可能清空；重要结果应由访客主动导出。

真实千问仅使用平台环境变量及 Secrets，不上传或读取本地 `.env`：

| 配置项 | 用途 |
| --- | --- |
| `YANZHENG_MODEL_PROVIDER=qwen` | 选择千问提供方；还需有效配置和下方显式开关 |
| `YANZHENG_DEMO_MODEL_ENABLED=0` | 默认关闭真实调用，即使已配置 Secret 也不请求；作者明确批准模型费用后才设为 `1` |
| `DASHSCOPE_API_KEY` | 仅设置为平台 Secret，不写入 Dockerfile、README、前端或源码 |
| `QWEN_BASE_URL` | 官方百炼兼容接口地址，需与账号地域一致 |
| `QWEN_MODEL` | 账号实际可用的模型名称 |
| `QWEN_TIMEOUT_SECONDS`、`QWEN_MAX_TOKENS` | 请求超时与输出上限 |

模型试用次数无上限：不限制单访客或全站每日的模型请求次数。旧的 `YANZHENG_DEMO_DAILY_CALLS`、`YANZHENG_DEMO_SESSION_CALLS` 环境变量已停用，即使平台保留旧值也不再限制调用；旧 `daily-budget.sqlite` 也不再读取或更新。`/demo/info` 的两项次数上限均返回 `null`，`model_call_limit_policy` 返回 `unlimited`。

平台账户的余额、计费和提供方限流仍由平台管理。默认模型开关为 `0`，由作者在平台明确开放真实调用。

平台配置完毕后，仍需实际检查 HTTPS 入口、访客隔离、无次数上限状态和一次明确授权的真实千问完整流程。成功构建镜像不等于这些功能已通过验收。真实请求会消耗模型额度；网络或配置失败必须显示实际错误，不能用预置结果冒充成功调用。

## 公开流程与材料

可体验研究想法、澄清、方向提议、阅读笔记绑定、明确接受/放弃、选题、理论问题卡、公开案例及导出。公开案例来自已知数学结果，标为 `curated_example`；它不代表本次千问生成、访客真实阅读、独立专家审阅或新定理。只有实际请求产生的结果才能标为真实模型输出。

`/demo/public-evidence.zip` 提供登记的 35 份公开历史原件及 `PUBLIC-EVIDENCE.json`。原件保持原始字节，清单给出路径、长度与 SHA256；部分历史文件保留开发机路径声明，它们不授权云端文件访问。ZIP 中已有的审阅压缩包保持原样。历史调用、核查样例与人工整理内容始终是历史材料，不能标成本次真实操作。

**在线受限能力**：原 Windows NTFS 原件安全读取器不适用于 Linux，本演示不把 Linux 原件在线读取冒充已验收能力；可下载公开材料后核对。固定 Lean 执行保持禁用，不会自动安装工具链；案例源码仅用于显示固定案例和解释边界。自然语言证明规划、源码哈希一致及已知例题都不等于形式证明或数学认证。

## 验收和保留

上传前核对 `SOURCE-MANIFEST.json`；上传后分别验收页面、两位访客隔离、公开样例、无次数上限状态、真实千问与 JSON/Markdown 导出。明确区分预置样例、真实运行和未验证项。保存云端 URL、平台构建日志及此次发布清单；不要在日志或截图中展示 Secret。需要更新时重新生成唯一 stage，保留之前的发布档案，不修改本机研究库。
