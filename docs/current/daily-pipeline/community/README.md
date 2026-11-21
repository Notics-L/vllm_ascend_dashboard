# 第一部分：社区 Daily 流水线系统 UML 设计基线

基线日期：2026-11-13。状态：按本轮对话最终业务决策归档，作为后续实现与验收依据。本文描述目标设计，不表示 Issue 上报与更新能力已经落地。

[返回 Daily 看板设计基线](../README.md)。第一部分位于 vLLM Ascend 社区侧，第二部分为看板系统，第三部分为独立通知系统。

## 图纸目录

| 图纸 | PlantUML 可编辑源文件 | Mermaid 源文件 |
| --- | --- | --- |
| UML 组件图 | [组件图](uml/community-daily-components.puml) | [组件图](mermaid/community-daily-components.mmd) |
| 业务流程 / UML 活动图 | [活动图](uml/community-daily-activity.puml) | [业务流程图](mermaid/community-daily-activity.mmd) |
| UML 交互时序图 | [时序图](uml/community-daily-sequence.puml) | [时序图](mermaid/community-daily-sequence.mmd) |

原对话最后确认的业务流程与时序 Mermaid 原文保存在本目录；PlantUML 是对应的可编辑整理版。组件图原先按 Issue 编号上报，现已同步后续确认的“首个失败创建 Issue”规则，改为传递本轮标识。未增加用户已删除的结束汇总、调度开始预建 Issue、成功后关闭空 Issue 等环节。

## 设计思路与软件层次

复用社区现有 workflow、Job 和测试 Step。测试执行与 Issue 更新分开：测试路径并行上报，独立更新 workflow 串行修改本轮 Issue。并发控制是 workflow / Job 配置，不单独拆分服务。

| 层次 | 软件单元 | 职责 |
| --- | --- | --- |
| 流水线执行层 | Daily 主流水线 | 复用现有调度，确定本轮标识，并传递给所有相关测试路径 |
| 流水线执行层 | 现有测试 Job / 子工作流 | 执行社区测试；图中的 A/B 仅作路径示意 |
| 流水线执行层 | 测试 Step 中的失败上报脚本 | 每出现一个失败用例就触发 Issue 更新 workflow，携带本轮标识和失败信息 |
| 流水线执行层 | Issue 更新 workflow 与 Job | 运行更新脚本，对同一本轮标识的更新执行并发控制 |
| 流水线执行层 | Issue 更新脚本 | 在同一个串行范围中查找、首次创建，或读取、合并、写回本轮 Issue |
| 结果记录层 | 社区 Daily Issue | 持续记录本轮失败，供第二部分看板采集和关联 |

不新增虚构的“用例执行组件”。Issue 创建和更新都在 vLLM Ascend 社区完成，不归看板前端执行。

## 最终业务规则

1. 每轮 Daily 使用统一、唯一的本轮标识。所有测试路径和更新任务传递同一个值，不能用子工作流自己的 Run ID 代替主轮次标识。
2. 每个失败用例单独上报，通过测试 Step 中的脚本调用 `workflow_dispatch`，触发独立 Issue 更新 workflow；不是在运行中的测试 Step 动态添加 Job。
3. 上报触发成功后测试继续执行，Issue 更新异步进行。
4. 同一本轮标识的 Issue 写入任务串行执行，查找、首次创建、读取、合并、写回均受同一并发控制。
5. 第一个失败增量到来时查找本轮 Issue；不存在则创建，已存在则合并更新。并发组必须使用本轮标识，因为首次上报时尚无 Issue 编号。
6. 整轮全部成功时，不触发失败更新、不创建 Issue。因此不再保留预建 Issue 后全部成功再关闭的旧流程。
7. 不额外增加“执行结束汇总本轮结果”的步骤。失败信息已经增量写入。
8. 保留现有主流水线及其测试路径，不因本设计增加测试执行服务。图中 A/B 不表示实际仅有两个子 workflow。
9. Issue 是第二部分的采集对象；失败分析、PR 回退、镜像重建和通知不属于第一部分。
10. 实现应对上报重试去重，对失败上报和更新失败保留可恢复的信息，避免重复条目或静默丢失。

## 与当前社区工作流的对应

当前参考仓库为 `C:/project/tmp/vllm-ascend`，主工作流是 `.github/workflows/schedule_daily_test.yaml`。

主流程包含 `run-all-tests`（UT 与 PR E2E），以及 `daily-select-tests` 后按硬件展开的多个 `daily-dispatch-*` Job。相关选择、缓存等 Job 同样可能失败，但 Job 失败不能直接伪装为某个用例失败。此处的 A/B 表达并行上报关系，真实扇出数量应保持与现有工作流一致。

Issue 更新 workflow 与脚本的文件名、失败事件载荷、可靠关联标识及失败重试策略在实现时确定；本轮仅保存设计，不修改社区工作流。

## 并发实现约束

同轮串行必须同时满足“互斥写入”和“保留待处理更新”。GitHub 默认并发队列只保留一个 pending 任务，后来的请求会替换已有 pending 任务；不能仅设置 `cancel-in-progress: false` 就假定所有增量都会执行。官方支持 `queue: max`，可排队至多 100 个待执行任务，超出容量仍需补偿；不要与 `cancel-in-progress: true` 组合。实现时应依据实际部署平台确认支持情况并补齐溢出恢复机制。[GitHub 并发控制文档](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)

## 验收要点

- 两个测试路径同时产生首个失败：本轮只创建一个 Issue，两个失败均保留。
- 同轮持续产生多个失败：正文增量合并，不互相覆盖，不因并发排队而丢失。
- 同一失败重试上报：不重复创建 Issue、不重复添加失败条目。
- 不同轮次同时运行：本轮标识隔离，不能写入彼此的 Issue。
- 全部成功：不触发失败更新，不创建 Issue。
- 更新请求失败或排队超限：有可查询的错误和恢复路径，不静默忽略。
- 不增加已删除的最终汇总动作，不改变现有测试执行范围。

## 来源追溯

- 最终业务流程：原对话“无需立刻创建 Issue，在第一个增量中创建”后的版本，turn `01a1cdcc-a186-74e0-93ac-f8d5c7f77512`。
- 最终交互图：原对话“根据我们的业务和现有的内容”后的版本，turn `01a1cdd4-eeab-7923-becc-13979af6a7e5`，已删除额外汇总。
- 组件图基础：turn `01a1cdc4-a9eb-70a3-8f4b-990a06bbe239`，并同步上述后续业务规则。
- Mermaid 业务流程与时序文件保留原文；组件图与 PlantUML 图为按最终规则整理的版本。
- `baseline-manifest.json` 记录归档文件 SHA-256；后续变更时同步更新设计和校验清单。
