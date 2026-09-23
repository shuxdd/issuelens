# 08: 贯通完整 Agent 的 supported 调查路径

**What to build:** 让完整 LangGraph 调查 Agent 对证据充分的案例制定计划、调用受控工具、形成并验证根因假设，最终输出带证据的 `supported` 调查报告。

**Blocked by:** 06: 加入 Reranker 和检索回归门槛；07: 从检索结果生成可引用的单轮调查报告

**Status:** resolved

- [x] 调查状态覆盖解析、计划、检索、证据汇总、根因假设、验证和报告。
- [x] Agent 复用 G3 的混合检索与 Reranker 配置。
- [x] 六类工具通过统一权限接口调用并产生结构化事件。
- [x] 一个固定证据充分案例能够以 `supported` 终态完成。
- [x] 最终报告中的关键结论均可追溯到允许证据。
- [x] 测试通过调查运行接缝验证外部行为，不依赖内部节点数量或私有类。

## 答案

2026-09-23：新增基于 LangGraph 的公共 `run_investigation` 接缝。它接收既有
`RetrievalInput`、G3 `RerankedRun`、调查时点快照、模型与统一工具后端，返回工单 07
Schema `1.0` 英文报告、`supported` 终态、停止原因、实际引用的允许证据、完整有序事件和
工具/模型/检索统计。状态轨迹覆盖 Issue 解析、计划、检索、证据汇总、最多三个根因假设、
验证与报告；无已验证根因时不提前实现工单 09 的其他终态。

六类工具 `search_code`、`semantic_search`、`read_file`、`inspect_history`、`run_tests`、
`submit_patch` 共用 `ToolGateway`。网关限制查询字段和长度、快照路径及行范围、仓库与调查时点、
预批准测试/补丁标识，并验证候选 commit/路径/内容/引用及固定替身返回结构；拒绝与成功均产生
脱敏结构化事件。`run_tests`/`submit_patch` 未运行 Shell、未写补丁，真实沙箱仍留给工单 14。

G4 从工单 06 的冻结 G3 安全投影 embedding、RRF、Reranker、Top-k 与精度配置，继续使用工单
07 的报告模型、Schema 和逐行引用校验。`scripts/run_supported_investigation.py` 以固定响应离线
重放 `starlette-issue-1552`，生成 `data/investigations/.../run.json`：终态 `supported`，停止原因
`verified_root_cause`，2 个根因假设、6 条去重允许证据、9 个事件；只读 REST
`GET /api/investigations/starlette-issue-1552` 可审查结果。产物及提示输入不含 closing PR、修复提交、
修复后文件、gold 或 `repair_evidence`；本工单未调用真实模型 API，费用为 0。

验收结果：工单定向测试 30 passed；全量后端 143 passed，Ruff 与 mypy 通过；现有检索门槛
`status=passed`、manifest SHA-256 `0662c5d7eb5b9b633f6a4b1fc0cfca8a481245be27079343d859c42fb79e7b6c`；
检索与报告回归 76 passed。`pnpm check` 及 `pnpm exec` 在脚本前因
`[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: esbuild@0.28.2` 失败；按约定直接运行 Vitest
（3 passed）、ESLint、TypeScript 与 Vite build，均通过，未修改前端依赖清单并清理包装器临时文件。
`docker compose config --quiet` 通过；获用户联网确认后，全新
`docker compose up --build --detach --wait` 成功，三个服务健康，首页、health、案例、三条检索、报告及 investigation 共 8 条路径均
HTTP 200，investigation 响应为 Schema `1.0` 的 `supported/verified_root_cause`。已执行
`docker compose down`，并确认 `issuelens_postgres-data` 数据卷仍在。
