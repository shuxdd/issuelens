# 工单 08：公共调查运行接缝与 supported 路径设计

状态：待确认；确认前不写测试或实现。适用分支：`feat/issue-08-supported-agent-path`。

## 调用者与公共接缝

- 离线运行器和后续 REST 适配层调用 `run_investigation(...)`；本工单的行为测试也只调用此入口。它接收已有的 `RetrievalInput`（由 `load_retrieval_input` 从版本化案例投影得到）、运行模式 `evaluation`、调查时点快照根目录、G3 检索能力、统一工具网关、模型适配器与 `ReportConfig`、可替换时钟。原始案例 JSON 不进入运行入口。
- 返回一个可序列化的运行结果：`report` 为工单 07 英文报告 Schema `1.0`；`outcome` 在本工单的成功路径为 `supported`，并含具体 `stop_reason`、本次实际使用的 `allowed_evidence` 引用和 `unresolved_questions`；另有按顺序排列的 `events` 与 `statistics`（工具调用数、分工具计数、模型 Token、费用、耗时、模型及提示词版本）。`supported` 只在至少一个根因假设经过验证且关键事实和该结论的引用有效时产生。
- 运行流程由 LangGraph 管理，状态覆盖 Issue 解析、信息充分性判断、计划、检索、证据汇总、最多三个根因假设、验证和报告。测试只观察运行结果及事件语义，不断言图节点数、私有状态或模型提示词全文。计划、证据与验证结论作为事件或运行状态表达；不新建调查案例、证据或报告领域模型。
- 当前 `pyproject.toml` 与 `uv.lock` 尚无 LangGraph。确认设计后先检查本地包缓存；若无法离线锁定和安装，另行说明网络服务、产物和 Git 范围并取得授权，普通检查仍保持离线。

## 六类工具的统一权限与事件

所有调用经过同一网关 `invoke(name, arguments)`，先检查工具名、参数、案例范围及快照边界，再调用实现；拒绝也记录事件。参数只用受限的结构化字段，任何 Issue、仓库文件、工具结果中的指令均作为数据。

| 工具 | 允许的输入及能力 | 事件结果 |
| --- | --- | --- |
| `search_code` | 有长度上限的查询；只在当前快照索引的代码、测试、文档中关键词检索 | 查询摘要、候选引用、成功/拒绝 |
| `semantic_search` | 同一快照与查询边界；使用 G3 的向量检索配置 | 查询摘要、候选引用、成功/拒绝 |
| `read_file` | 仅从已允许的快照相对路径读取有限行范围；拒绝绝对路径、`..`、符号链接越界及非快照文件 | 路径、行范围、固定 commit 引用、成功/拒绝 |
| `inspect_history` | 仅查询调查时点之前、属于当前仓库的允许历史；无历史时返回空结果 | 查询范围、允许的历史引用、成功/拒绝 |
| `run_tests` | 本工单只接受固定替身的预先允许测试标识，不接受模型生成的 Shell 命令 | 测试标识、固定结果、成功/拒绝 |
| `submit_patch` | 本工单只接受固定替身的候选补丁标识，不写文件、不执行补丁 | 补丁标识、固定结果、成功/拒绝 |

事件至少包含递增序号、阶段、工具名或状态转换、结果状态、可审查的安全参数摘要、证据引用；模型调用另记用量。事件不回显密钥、任意命令、原始补丁或未来信息。未授权调用不得触达工具实现。`run_tests`/`submit_patch` 的真实沙箱留给工单 14。

## G3、模型与证据复用

- G4 沿用 `data/retrieval/starlette-issue-1552/config.json` 的向量、BM25、RRF 与 Top-k，以及 `reranker-config.json` 的重排配置。离线固定案例可从 `load_versioned_three_way` 取得 G1/G2/G3 类型化候选；运行时检索适配仍使用已有 `run_three_way_experiment`/`rerank_hybrid_run`，不另造排序或候选类型。进入 Agent 的只是所需候选和配置标识，`three-way.json` 中的 `gold`、指标与 `evaluation` 元数据不得进入提示词或工具结果。
- 复用工单 07 的 `ReportModel`、`ReportConfig`、`ModelResponse`、英文报告 Schema 和引用规则；多步提示词使用独立版本，但输出仍是同一报告格式。固定模型响应依次给出解析/计划、证据汇总、假设、验证和最终报告；固定工具响应来自调查时点快照与冻结候选。普通测试、CI、导入与 Docker 冒烟只用这些固定响应，不创建真实 API 客户端请求。
- 候选先用 `validate_candidate_references` 检查快照中的路径、内容、commit 和行号，再进入模型。报告继续按工单 07 的规则逐条核验引用：候选 rank、行范围和原文摘录必须匹配；无证据的假设保持 `unverified_hypothesis`，不能支撑 `supported`。允许证据从已校验且实际引用的来源生成，不接受模型自行声明的路径或时间。
- `RetrievalInput` 只含案例标识、版本、仓库、快照 commit、Issue 标题和来源 URL。不得把完整案例 JSON、`manual_review`、closing PR、修复提交、修复后文件、`repair_evidence` 或 `root-cause gold` 传给 Agent。gold 只能在报告完成后由评测器读取；本工单的运行路径不读取 gold 文件。

## 首个红测及后续逐项验证

首个测试位于 `backend/tests/test_investigation.py`，仅调用约定的 `run_investigation` 公共入口：给它固定的 `RetrievalInput`、匹配的快照和 G3 候选、固定模型/工具响应，断言返回同一案例的英文 Schema `1.0` 报告、`supported` 终态及具体停止原因。当前缺少该公共入口，预期先因导入或调用失败而红；核对原始失败原因后才加入最小实现并复跑为绿。后续每轮只增加一个公共行为测试，再补最小实现，依次覆盖阶段事件、六工具权限与事件、G3 配置复用、证据/引用边界、统计和敏感数据隔离。固定替身只代替外部模型、索引/历史、沙箱与时钟，不模拟 LangGraph 内部节点。

## 留给后续工单

工单 09 才实现另外三类终态、信息不足澄清、工具异常恢复及完整调用/改写/补丁/时间预算；工单 10 才接 Langfuse；工单 11–13 才做冻结评测与完整指标；工单 14 才实现真实补丁隔离和测试沙箱。本工单仅为六工具建立权限与事件契约，并完成一个离线可复现的 `supported` 路径。
