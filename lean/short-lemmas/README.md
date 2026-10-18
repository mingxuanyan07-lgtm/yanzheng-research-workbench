# 固定短引理项目

本项目锁定 `leanprover/lean4:v4.33.1`，只用 Lean 自带模块，不下载依赖，不读取或修改 `D:/Lean` 的既有项目及全局设置。现有 mathlib 的版本与轻量导入结果见 [环境检查记录](environment-discovery.json)；最小适配器不依赖 mathlib。

`cases.json` 是经过审查的固定案例目录。调用者只能选择 `case_id`，不能提供 Lean 源码、命令、模块、路径或额外编译参数。`expected_status` 仅供测试对照，不能参与实际核验结论。新增案例必须审查其完整源码与依赖，再更新 Python 侧的源码锁。

适配器将 `Audit.lean`、一个案例文件、`#yanzheng_audit Fully.Qualified.target` 拼成独立临时文件，使用已锁定的真实 Lean 二进制核验。案例正文不包含 import；审计器顶部列出最少的固定导入。`Audit.lean` 可单独用 Lake 构建，但运行核验不要求首次执行 `lake update` 或写入任何用户工具链目录。

审计命令只对已进入 checked kernel environment 的目标输出一行 `YANZHENG_LEAN_AUDIT {JSON}`。`elaborated_type` 开启 `pp.all`、`pp.fullNames`、`pp.universes`，保留 elaboration 后的量词、类型、隐式实例和 universe。源码级陈述与展开后的精确类型由运行记录一起保存。

审计递归访问目标声明的类型和值、归纳构造子、递归子规则，并合并 Lean 官方 `collectAxioms` 的结果（含导入模块的公理摘要）。输出全部依赖、公理、`sorryAx`、不安全或 partial 声明、缺失依赖、商类型 kernel primitive，以及额外信任标记。首期认可的公理清单仅为 `propext`、`Classical.choice`、`Quot.sound`，并逐次列出实际使用项；`Lean.trustCompiler`、`Lean.ofReduceBool` / `ofReduceNat`、native-decide 产生的额外公理不能静默获准。没有发现额外依赖不意味着整个工具链已独立验证。

| 案例 | 用途 |
|---|---|
| `nat_add_zero`、`implication_identity` | 两个有效的短引理 |
| `invalid_equality` | 错误证明；Lean 退出非零，恢复用的 synthetic sorry 不能掩盖错误 |
| `direct_sorry`、`indirect_sorry` | 退出零仍检测直接或间接的未完成依赖 |
| `direct_axiom`、`indirect_axiom` | 直接或间接的未认可自定义公理 |
| `native_decide_trust` | 显式依赖 `Lean.trustCompiler` 的额外信任夹具，不实际调用 native 编译器 |
| `timeout_fixture` | 固定、有限、纯 Lean 的十亿次累加，供外部墙钟超时测试 |

超时夹具没有网络或文件操作、sleep、用户参数；它在真正的 Lean `#eval` 中计算。缩小为一千次后已实际返回 `1000`，十亿次版本在 4 秒限时下被终止，不等待其完成。通过仅说明准确形式陈述的该份证明满足当前规则；不自动确认自然语言问题与形式陈述一致。错误、未完成或超时也不说明数学命题为假。
