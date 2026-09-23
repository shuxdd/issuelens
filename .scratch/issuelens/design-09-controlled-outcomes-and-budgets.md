# 工单 09：公共调查接缝上的四类终态与预算恢复设计

状态：已确认并实现；实现与验收结果见工单 09 的“答案”。适用分支：
`feat/issue-09-controlled-outcomes-and-budgets`。

## 调用者与公共输入输出

- 离线固定运行器、只读 REST 产物生成器和行为测试继续只调用
  `run_investigation(...)`。现有位置参数保持兼容：`RetrievalInput`、G3
  `RerankedRun`、调查时点快照、`ReportModel`、`ReportConfig` 和统一工具后端。
- 入口只增加关键字参数：运行模式 `evaluation`（默认）或 `interactive`、一个带规格默认值的
  `InvestigationBudget`，以及独立的可替换单调运行时钟。既有 `clock` 仍只测量报告模型延迟，
  避免改变工单 07/08 的用量与延迟契约。
- 返回结构仍是 `case_id`、工单 07 英文 `report` Schema `1.0`、`outcome`、有序
  `events` 和 `statistics`。`outcome.status` 只能是 `supported`、
  `insufficient_evidence`、`budget_exhausted`、`unsafe_request`，并始终含具体
  `stop_reason`、`allowed_evidence` 和 `unresolved_questions`；不增加 `failed`。
- 不新建调查案例、候选、证据、引用或报告模型。`RetrievalInput`、G3 候选、
  `generate_report` 的 Schema/逐行引用校验和 `ToolGateway` 均直接复用。
- `GET /api/investigations/{case_id}` 继续只读版本化结果；工单 08 的
  `starlette-issue-1552` supported 产物、停止原因和现有顶层统计字段保持兼容。

## 交互模式与评测模式

- 两种模式运行相同的权限、证据校验、预算和终态逻辑；模式不能扩大工具权限或读取更多案例字段。
- `evaluation` 不在运行时等待或接受补充信息。信息不足时直接返回
  `insufficient_evidence`，把仍需回答的事项放入 `unresolved_questions`，且不输出
  `clarification_questions`。
- `interactive` 在信息不足时可额外返回 `clarification_questions`；它们来自同一组
  `unresolved_questions`，最多前三个，每个必须是非空、具体且可由贡献者回答的问题。
  本工单不实现对答案的续跑或自由对话。
- 固定信息不足案例使用三问上限：失败调用及完整错误是什么、最小复现输入是什么、问题发生在
  哪个 Starlette 版本/快照。若已有证据已回答其中一项，则不再提出该项。

## 四类终态与停止原因

| 终态 | 触发条件 | 固定停止原因 |
| --- | --- | --- |
| `supported` | 至少一个根因假设通过现有引用校验并标记为 `supported` | `verified_root_cause` |
| `insufficient_evidence` | 两种检索都没有可验证候选、没有受支持根因，或普通工具/结构化输出的有限恢复已耗尽且仍不能安全形成结论 | `no_validated_evidence`、`no_supported_root_cause`、`tool_recovery_exhausted` 或 `structured_output_recovery_exhausted` |
| `budget_exhausted` | 任一调用、改写、假设、补丁或时间预算在继续动作前达到上限 | `tool_call_budget_exhausted`、`retrieval_rewrite_budget_exhausted`、`hypothesis_budget_exhausted`、`patch_attempt_budget_exhausted` 或 `time_budget_exhausted` |
| `unsafe_request` | `ToolGateway` 因工具名、参数、快照/历史边界、测试/补丁白名单或后端权限拒绝而拒绝操作 | `tool_permission_denied` |

权限拒绝不是普通工具故障，不换另一个工具继续尝试。普通工具的无结果、无效结果或运行错误不会变成
`unsafe_request`；另一既有检索工具仍可提供一次补偿，随后安全终止。任何路径都先形成规范终态，再生成
最终 `reported`/`terminated` 事件，异常不作为公共调查结果泄漏给调用者。

## report、evidence、citations 与 unresolved questions

- 继续由 `generate_report` 校验报告字段、最多三个根因假设、候选 rank、固定 commit、快照内路径、
  行号范围和原文摘录。事实没有有效引用仍被拒绝；无引用根因只能是
  `unverified_hypothesis`，不能产生 `supported`。
- `outcome.allowed_evidence` 只能从最终报告中保留的 facts 和根因假设的已校验引用投影，按
  path/line 稳定排序并去重。模型自报路径、未被报告引用的候选、工具错误内容和未来信息均不能进入。
- 非 supported 结果也返回同一报告 Schema。若已有有效草稿，则保留其中仍在预算内的事实、引用和假设；
  若尚无有效草稿，则使用最小安全报告：低置信度、空 facts、空根因假设以及具体
  `unresolved_questions`。这是既有报告字典的安全退化，不是新报告模型。
- `supported` 的 unresolved questions 可为空；其余三种终态至少保留一个可操作的未解决问题。
  `interactive` 的澄清问题必须是 unresolved questions 的最多三个元素；`evaluation` 只记录问题，
  不索取答案。

## 预算计数、扣减时机与硬停止位置

默认限制直接来自总规格：`tool_calls=20`、`retrieval_rewrites=2`、
`root_cause_hypotheses=3`、`patch_attempts=2`、`run_time_ns=300_000_000_000`。
一个冻结 dataclass 足够，不增加配置文件或工厂。

| 维度 | 扣减/计数时机 | 阻止位置 |
| --- | --- | --- |
| 总工具调用 | `ToolGateway.invoke` 收到一次允许进入权限检查的调用时计数；被拒绝或结果无效也计入，预算拦截本身不计入 | 每次权限检查及后端调用之前；达到 20 后不再触达任何工具后端 |
| 检索改写 | 调查流程准备采用一个不同于原 query 的受控重试 query 时计数 | 改写进入 `search_code`/`semantic_search` 之前；最多 2 次。四个当前固定案例不需要改写，统计为 0，不为满足计数而虚构改写 |
| 根因假设 | 有效结构化报告通过 Schema 后，按最终允许进入报告的假设数计数 | 验证和报告前；超过剩余额度时只保留额度内假设并以 `hypothesis_budget_exhausted` 终止，不把超额假设或引用写入结果 |
| 补丁尝试 | 每次允许的 `submit_patch` 请求在调用固定后端前计数，同时计入总工具调用 | 权限校验通过后、后端前；第三次不触达后端。工单 09 不执行真实补丁 |
| 总运行时间 | 入口读取一次单调起点；每个工具、模型、恢复和终态动作前后读取 | 到期后立即停止编排且不再调用工具/模型；边界适配器仍须使用不超过剩余预算的超时。当前真实模型适配器已有 60 秒请求超时，真实补丁/测试超时留给工单 14 |

`statistics` 保留工单 08 的 `tool_calls`、分工具计数、模型/Token/费用/模型延迟与检索配置，新增
`budgets`：每个维度的 `limit`、`used`、耗时和可选 `exhausted`；另记录 `model_calls` 与
`structured_output_retries`。所有事件最终统一编号。预算拦截产生 `kind=budget` 事件，含维度、上限、
已用值与 `status=exhausted`，但不伪造工具事件。

工单 08 的静态 supported 路径没有检索改写或补丁尝试，因此两项为 0。若本工单用固定
`submit_patch` 替身验证补丁预算，只验证 `ToolGateway` 的权限、事件和第三次不触达后端；不写文件、
不执行 Shell 或测试。

## 有限恢复

- `search_code` 与 `semantic_search` 各最多调用一次。某一普通失败或空结果时继续另一工具；任一工具
  提供与 G3 交集内的已校验候选即可继续。两者均无有效证据时进入 `insufficient_evidence`，不循环。
- `PermissionError` 立即进入 `unsafe_request`；后续任何工具均不得调用。工具无效结果和普通运行错误
  记录脱敏 `invalid_result`/`error` 事件，不把原始不可信输出写入报告。
- `generate_report` 的 JSON、字段或引用校验失败最多重试一次（总模型调用最多两次），每次产生
  `kind=recovery` 事件。重试仍必须先检查时间预算。第二次仍无效时进入
  `insufficient_evidence/structured_output_recovery_exhausted`；若恢复前预算到期，则进入相应
  `budget_exhausted`。
- 不重试权限拒绝，不重试已耗尽预算，不自动调用 `run_tests`/`submit_patch`，也不把普通异常映射为
  含糊 `failed`。

## 四个固定案例与固定响应

四例复用 `test_investigation.py` 现有 `RetrievalInput`、同一临时快照、同一 G3 候选和固定模型/工具
替身，不创建新的领域案例文件。

1. **supported**：两种检索均返回既有候选；固定模型返回工单 08 的有效事实和有引用根因。
   断言 `supported/verified_root_cause`、现有引用、空或既有 unresolved questions、两次工具调用，
   所有预算 used 未超 limit；并保持版本化 Starlette supported 回归。
2. **insufficient_evidence**：两种检索固定返回空列表，模型不得被调用。evaluation 返回
   `insufficient_evidence/no_validated_evidence`、空 allowed evidence、三项以内 unresolved questions、
   无 clarification 字段；同一输入切为 interactive 时只额外返回最多三项具体问题。
3. **budget_exhausted**：检索和模型返回有效且带引用的报告；固定单调时钟在报告完成后越过 5 分钟
   截止点。结果保留已验证报告引用和未解决问题，返回
   `budget_exhausted/time_budget_exhausted`，记录时间预算事件，且测试后端确认截止后无工具调用。
4. **unsafe_request**：固定工具后端在首个检索请求上抛出 `PermissionError`，模拟统一权限边界拒绝
   来自不可信 Issue/仓库指令的越权操作。结果为 `unsafe_request/tool_permission_denied`、空引用、具体
   unresolved question；仅有一条 denied 工具事件，第二检索工具与模型均未调用。

另用固定模型响应序列（第一次无效 JSON、第二次有效；以及连续两次无效）验证一次恢复和恢复耗尽；
用固定 `submit_patch` 后端验证最多两次且第三次不触达后端。全部测试离线、零费用、无真实测试沙箱。

## 首条红测与逐项红—绿顺序

首条测试仍只调用公共 `run_investigation`：沿用现有固定证据充分输入，断言 supported 结果新增完整
`statistics.budgets`，且五个 `used` 均不超过规格上限。当前结果没有 `budgets`，因此会以缺少该公共
字段失败；这确认的边界只是“现有 supported 路径公开预算统计且行为不变”。确认真实红因后，最小
实现只加入默认预算统计并保持现有结果，再复跑转绿。

后续每轮只加一个公共行为测试并立即做最小实现，顺序为：信息不足 evaluation、interactive 三问、
unsafe 权限拒绝、工具失败恢复、总工具调用硬停止、时间硬停止、根因假设上限、补丁尝试上限、
结构化输出一次恢复与恢复耗尽、四类事件/统计完整性、版本化 supported 与 REST 回归。测试不观察
LangGraph 节点名称/数量、私有状态或提示词全文。

## 留给工单 10–14

- 工单 10：Langfuse、父子 span、完整 Token/费用/耗时观测和异步关联；本工单只保留完成终态与预算
  验收所需的本地结构化事件。
- 工单 11–13：冻结主评测集、可比较实验、稳定性运行、安全终止数据集和正式指标；本工单的四例只是
  确定性行为夹具。
- 工单 14：真实临时工作树、补丁应用、测试命令沙箱、资源/输出限制；本工单的 `run_tests` 与
  `submit_patch` 仍只允许固定替身和预批准标识。
- 不实现在线追问续跑、任意命令、真实模型评测、新配置层或新的案例/证据/引用/报告类型。
