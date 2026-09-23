# 09: 实现受控预算与四类调查终态

**What to build:** 让调查系统在证据不足、工具失败、预算耗尽或危险请求下给出明确且可操作的终态，而不是无限循环或笼统失败；交互模式还能提出有限的澄清问题。

**Blocked by:** 08: 贯通完整 Agent 的 supported 调查路径

**Status:** resolved

- [x] `supported`、`insufficient_evidence`、`budget_exhausted` 和 `unsafe_request` 均可由固定案例触发。
- [x] 每种终态都包含停止原因、已收集证据和未解决问题。
- [x] 交互模式最多输出三个具体澄清问题。
- [x] 评测模式不接受运行中补充信息，证据不足时安全终止。
- [x] 总工具调用、检索改写、根因假设、补丁尝试和运行时间均受预算限制。
- [x] 达到预算后不再调用工具，结构化输出失败只执行有限恢复。
- [x] 不使用笼统 `failed` 代替领域终态。

## 答案

2026-09-23：在工单 08 的 `run_investigation`、`ToolGateway`、G3 候选和工单 07
报告 Schema 上完成四类规范终态。`evaluation` 在无证据、无受支持根因、工具恢复耗尽或结构化
输出恢复耗尽时直接返回 `insufficient_evidence`；`interactive` 只在信息不足时额外返回最多三个
具体澄清问题。权限拒绝立即返回 `unsafe_request/tool_permission_denied`，普通工具失败只允许另一
既有检索工具补偿一次。每个结果均包含具体停止原因、最终报告实际引用的允许证据、未解决问题、
有序终态/工具/预算/恢复事件及可核对统计，没有 `failed` 终态。

新增冻结 `InvestigationBudget`，规格上限固定为总工具调用 20、检索改写 2、根因假设 3、补丁尝试
2、总运行 300 秒；调用者只能收紧额度。统一网关在工具后端前检查时间与总调用额度，在白名单
补丁后端前检查补丁额度；模型每次调用和恢复前检查时间。超额根因假设不会进入最终报告，预算
终止保留额度内已验证事实与引用。当前 supported 静态路径不执行检索改写或补丁，二者统计为 0；
补丁上限只用固定替身验证，未写文件、未执行 Shell 或真实测试。结构化报告最多恢复一次，总模型
调用最多两次，恢复耗尽进入 `insufficient_evidence`。

逐项红—绿均先确认目标公共行为缺失：预算统计缺失、零检索结果抛异常、入口无 mode、权限拒绝
泄漏异常、工具故障无恢复、工具/时间/假设/补丁预算未阻止后续动作、无效结构化输出直接抛错、
终态事件缺少 outcome，以及未验证根因仍抛异常；随后分别以最小实现转绿。测试只观察公共输入、
报告、终态、停止原因、未解决问题、事件和统计，不依赖 LangGraph 节点数量或私有状态。

验收结果：工单 09 定向调查测试 56 passed；全量后端 169 passed，Ruff 与 mypy 通过；检索、
Reranker、报告和 supported 路径均包含在全量回归中。离线检索回归 `status=passed`，manifest
SHA-256 `0662c5d7eb5b9b633f6a4b1fc0cfca8a481245be27079343d859c42fb79e7b6c`。
`pnpm check` 在项目脚本前因 `[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts:
esbuild@0.28.2` 失败；未改依赖清单，直接运行 Vitest（3 passed）、ESLint、TypeScript 和 Vite
build 均通过，并删除包装器生成的 `.pnpm-store/` 与 `frontend/pnpm-workspace.yaml`。

`docker compose config --quiet` 通过，仅有读取用户 Docker config 的权限警告。经用户明确允许联网后，
全新 `docker compose up --build --detach --wait` 成功，PostgreSQL、后端和前端健康；首页、health、
案例、三条检索、报告和调查共 8 条 HTTP 路径均返回 200。调查 REST 保持
`supported/verified_root_cause`，含五维预算统计和 `reported` 终态事件。已执行
`docker compose down`，并确认 `issuelens_postgres-data` 数据卷仍存在。固定 supported 产物使用
零值注入时钟离线重放；本工单未调用真实模型 API，费用为 0，也未读取 gold、closing PR、修复提交、
修复后文件或 `repair_evidence`。
