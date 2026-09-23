# 工单 10：公共调查接缝上的本地事件与可选 Langfuse 设计

状态：已确认；按逐项红—绿实现。适用分支：
`feat/issue-10-observable-investigations`。

## 调用者及公共输入输出

- 离线固定运行器、REST 产物生成器和行为测试继续调用
  `run_investigation(...)`。既有位置参数和工单 09 的 `mode`、`budget`、
  `clock`、`run_clock` 保持兼容；不新增第二条调查运行路径。
- 入口只增加一个关键字 `observation`。它是本次运行的轻量观测上下文，允许固定
  `run_id`、UTC 起点、独立单调观测时钟和可选 Langfuse 出口；省略时由标准库生成
  一次性标识并只记录本地事件。观测时钟不参与预算判断，避免观测读时钟改变工单 09
  的终态或硬停止位置。
- 返回值仍是 `case_id`、工单 07 的英文 `report` Schema `1.0`、工单 09 的四类
  `outcome`、`events` 和 `statistics`。增加顶层 `run_id`、`trace_id`，并丰富既有事件；
  不新建案例、证据、引用、报告、预算或调查终态模型，不增加 `failed` 终态。
- `GET /api/investigations/{case_id}` 继续原样读取版本化结果。固定重放脚本传入固定
  `run_id`、UTC 起点和观测时钟，使 `run.json` 仍可离线、可审查、可重复生成。

## 本地事件与 Langfuse 出口的职责边界

- 本地 `events` 是权威记录，先在内存中完整形成，再写版本化 JSON。状态、检索、工具、
  模型、预算、恢复和终态都只产生这一套事件；Langfuse 不创建第二套领域事件。
- 统一观测出口只读取完成的本地事件，将安全投影翻译成 Langfuse trace/span/generation/
  event/score。远程初始化、发送和刷新均发生在调查语义终态确定之后。
- 出口异常只追加一条本地 `kind=observation`、`status=degraded` 事件，记录失败阶段和异常
  类型，不记录异常消息、凭据或请求体；`report`、`outcome`、预算、工具调用和此前事件
  均不改变。出口不得触发任何调查工具或模型重试。
- 不引入供应商通用框架。只有一个 Langfuse 适配器，以及为外部系统边界定义的最小结构
  协议，固定客户端替身实现同一组被调用方法。

## run、trace、span、event 与 experiment 的关联

- `run_id` 标识一次调查调用。默认使用 `uuid4().hex`；固定重放和测试显式传入固定值。
  同一次运行的返回值、全部事件、远程 trace 和后置评分共用该值。
- `trace_id` 由 `sha256("issuelens:" + run_id)` 截取固定长度生成，因而同一运行稳定、不同
  运行隔离，也不把案例标题或其他不可信文本放入标识。
- 每个本地事件含 `event_id`、`run_id`、`trace_id`、`parent_id`。`event_id` 由
  `trace_id + 逻辑事件键 + 同类序号` 确定性派生；父事件即使稍后完成也可预先确定标识。
- Langfuse 的调查 trace 使用 `trace_id`。当前 Python SDK v4 不允许自定义 observation ID，
  因此本地 `event_id/parent_id` 作为 metadata 保留关联，远程节点 span、工具 span 和模型
  generation 通过 SDK 返回的父 observation 对象显式创建，不依赖隐式当前 span。
- 工单 10 只提供后置 `record_evaluation_score(...)` 接缝：接收已完成的调查结果、调用者提供的
  `experiment_run_id`、分数名、标量值和评测器版本，追加关联同一 `run_id/trace_id` 的
  `kind=score` 本地事件，并由同一出口发送 Langfuse score。它不读取 gold、不计算指标，
  也不创建数据集或实验运行器。

## 调查、状态、检索、工具、模型和终态层级

```text
investigation trace (run_id / trace_id)
├─ issue_parsed span
├─ planned span
├─ retrieved span
│  ├─ search_code tool span
│  └─ semantic_search tool span
├─ evidence_summarized span
├─ hypotheses_formed span
│  └─ report model generation (每次尝试一条)
├─ verified span
├─ budget / recovery event（发生在哪个阶段就挂在哪个阶段）
└─ reported terminal span
   └─ evaluation score event（仅报告完成后的评测调用）
```

- 继续使用已有 `stage` 名称表达状态转换；每条阶段事件增加 `from_stage`、`to_stage`，首条
  的 `from_stage` 为 `null`。工具事件继续来自统一 `ToolGateway`，检索工具事件额外带候选
  数量、rank/reference 安全摘要，不保存候选正文。
- 每次 `ReportModel.generate` 都产生模型事件，包括结构化输出恢复中的失败尝试。模型事件不含
  完整提示词、完整输出或供应商原始响应，只含模型/提示词版本、调用状态和使用量。
- `reported` 继续携带四类规范终态和具体 `stop_reason`；观测降级不是调查终态。

## 顺序和时间字段

- `sequence` 是单次调查内从 1 开始、无间隙递增的完成记录顺序。后置评分若存在，继续使用
  下一序号；不同运行不共享计数器。
- 观测上下文在入口读取一次 UTC 起点和一次独立单调起点。事件的 `started_at`、`finished_at`
  由 UTC 起点加单调偏移生成，均为带 `Z` 的 ISO 8601；`duration_ns` 是同一单调时钟的非负差。
  这样不受墙钟回拨影响，也不改变预算时钟读取次数。
- 阶段、工具和模型调用记录开始与结束；瞬时预算/恢复/评分事件的 `duration_ns=0` 且两个时间
  相同。最终 `statistics.run_latency_ns` 取入口到本地终态的单调差，不包含远程刷新耗时。

## Token、费用和延迟的来源与聚合

- 每次模型事件的 Token 来自已有 `ModelResponse`：输入、缓存输入、未缓存输入、输出和总量；
  费用继续按 `ReportConfig` 的固定价格与币种计算。无供应商用量或不一致用量继续按现有规则
  拒绝，不伪造估算值。
- 模型恢复的每次真实调用分别记录；`statistics` 的 Token、费用和模型调用数改为所有模型事件
  求和，因此不会漏掉返回了有效用量但结构化报告无效的尝试。单次成功路径与工单 09 数值兼容。
- 工具与阶段延迟来自独立观测时钟；模型延迟仍覆盖实际 `generate` 调用。保留既有
  `statistics.latency_ns` 的模型延迟含义，新增总运行 `run_latency_ns`，不偷换旧字段口径。
- 检索候选统计来自两次已有工具结果的安全摘要；预算统计继续由工单 09 的计数器产生，观测
  出口不重新计数。

## `LANGFUSE_ENABLED=false` 与失败降级

- 环境装配函数只读取明确命名的 `LANGFUSE_ENABLED`、base URL（兼容 host）、public key 和
  secret key。
  值为 `false` 或未启用时立即返回无远程出口：不导入 Langfuse SDK、不构造客户端、不启动
  后台线程、不发网络请求，也不要求凭据。
- 启用时才惰性初始化客户端。初始化、trace/span/generation/score 发送或 `flush` 任一步失败，
  统一捕获并按上述 `observation/degraded` 本地事件记录；不会抛回调查图、改写终态、绕过预算、
  重跑模型或工具，也不会删除本地事件。
- 普通测试、CI、导入、固定运行器和 Docker 冒烟默认显式关闭并使用固定客户端替身；不会连接
  Langfuse Cloud。真实层级查看若验收仍要求人工外部验证，而本地没有服务/凭据，则保持工单
  `claimed` 并记录缺失条件，不创建完成提交。

## 统一敏感数据清洗

- 清洗只在一个远程出口实现。出口使用字段白名单投影，而不是递归上传整个运行结果：允许标识、
  阶段/工具/模型名、状态、停止原因、候选数量与允许引用、Token、费用、延迟、预算和版本字段。
- 永不上传完整环境变量、HTTP headers、`Authorization`、API key、cookie、完整提示词、模型原始
  输出、工具原始结果、候选正文、补丁、命令或容器原始输出。工具参数只沿用 `ToolGateway`
  的白名单字段或查询 SHA-256。
- 白名单值再经过统一递归清洗：键名匹配 key/token/secret/password/authorization/cookie 时替换为
  `[REDACTED]`；字符串中的 Bearer 值、常见密钥赋值和运行时显式提供的敏感值也替换。清洗函数
  纯函数化，用固定嵌套载荷验证；调用者不得自行实现另一份清洗。
- closing PR、修复提交、修复后文件、root-cause gold 和 `repair_evidence` 不在允许投影中。
  分数接缝只接收计算完成的分数标量及评测器版本，不能接收 gold 或修复证据正文。

## 异步上下文传播

- 不依赖全局“当前 trace/span”或线程局部状态。每个事件和待发送记录都显式携带不可变的
  `run_id`、`trace_id`、`event_id`、`parent_id`；Langfuse 批量发送按本地父标识查找显式父
  observation 对象来建层级，后台线程或异步任务不读取环境式当前 span。
- 固定异步测试并发地通过 `asyncio.to_thread(run_investigation, ...)` 运行两次调查，断言每个结果
  内父标识闭合、两个 trace 不串线、序号各自从 1 开始。测试只观察公共返回值和固定出口收到的
  安全载荷，不观察 LangGraph 节点数量、私有状态或 SDK 上下文变量。

## 固定观测客户端、模型和工具响应

- 继续复用 `test_investigation.py` 的固定 `RetrievalInput`、G3 `RerankedRun`、快照、模型响应和
  工具响应。固定观测客户端只收集 trace/span/generation/event/score 载荷，可在指定阶段抛出
  初始化、发送或刷新错误；它不访问网络。
- 固定时间由观测上下文注入，固定 `run_id` 产生可独立手算的标识、时间、Token、费用和层级。
  不模拟 LangGraph 内部节点；模型和工具仍只在系统边界替换。

## 首条红测及逐项红—绿

- 首条测试仍只调用 `run_investigation`：给现有 supported 固定输入和固定观测上下文，断言返回
  顶层稳定 `run_id/trace_id`，每条本地事件具有连续 `sequence`、同一关联标识、唯一
  `event_id` 和 UTC/单调时间字段。当前结果没有这些公共字段，因此会因缺少 `run_id` 失败；
  这轮只确认“单次调查具有稳定标识和有序本地事件”，不接 Langfuse。
- 后续严格一次一个公共行为：父子层级；检索/工具/模型事件与 Token/费用/延迟聚合；
  `LANGFUSE_ENABLED=false` 完全离线；固定客户端层级；初始化/发送/刷新失败降级；统一清洗；
  后置分数/实验关联；并发异步隔离；工单 09 四终态及 supported REST 回归。

## 依赖与联网边界

- 当前 `pyproject.toml`/`uv.lock` 没有 Langfuse，离线环境中 `find_spec("langfuse")` 为 `None`。
  真正实现 Langfuse 适配器预计需要新增官方 Python SDK；在用户确认依赖和允许下载前，不修改
  依赖清单、不下载包、不访问文档站或 Langfuse Cloud。
- 自动验收全部使用固定客户端，零凭据、零模型费用、零 Langfuse Cloud 费用。真实 Cloud 仅在
  另行批准后手工验证，上传范围严格限于上述清洗后的事件投影。

## 留给工单 11–14

- 工单 11：100–150 候选、约 80 人工复核、30 条主评测集、10 条稳定性子集、冻结与分组协议。
- 工单 12：完整四组实验运行器、实验生命周期、聚合/逐案例指标、重复运行和盲评流程。
- 工单 13：15 条安全终止集及正式安全指标。
- 工单 14：真实临时工作树、补丁执行、测试容器、资源/输出限制和完整沙箱观测。
- 本工单不做真实模型评测、真实补丁/任意 Shell、前端时间线、供应商通用框架或新的领域模型。
