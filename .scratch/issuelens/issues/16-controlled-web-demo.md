# 16: 交付五条调查轨迹的受控 Web Demo

**What to build:** 提供一个英文 Web Demo，让访问者选择五条预置 Starlette 案例，查看调查状态、轨迹时间线、英文报告、证据侧栏和补丁验证结果，同时阻止任意代码与命令输入。

**Blocked by:** 10: 让每次调查可追踪、可计算成本；12: 运行 G1–G4 可比较实验与人工评审；13: 用安全终止集验证 Agent 边界；15: 扩展到 10 条补丁案例并计算指标

**Status:** resolved

- [x] Demo 固定展示 2 条 `supported`、1 条 `insufficient_evidence`、1 条 `budget_exhausted` 和 1 条 `unsafe_request`。
- [x] 至少一条 `supported` 案例展示候选补丁和测试验证结果。
- [x] 用户能够从报告结论跳转到相应 commit、路径、行号和证据内容。
- [x] 默认回放预生成轨迹，不在公开环境实时执行补丁测试。
- [x] API 只接受白名单案例 ID，不接受仓库 URL、代码、补丁或命令。
- [x] IP 级速率限制、全局并发限制和 HTTP 429 行为可验证。
- [x] SSE 不可用时页面能够使用轮询恢复进度或轨迹加载。
- [x] 服务错误和无效案例显示可操作的英文错误信息。

## 答案

英文 Demo 固定展示五条调查轨迹及四种终止结果。1552 和 886 为已有 `supported` 记录，1355 为已有 `insufficient_evidence` 记录；预算与不安全请求轨迹由固定输入离线生成并冻结。页面提供轨迹时间线、英文报告、可追溯到固定 commit 与行号的证据侧栏，以及标明来源的候选补丁和离线测试结果。公开模式仅暴露白名单只读接口，限制请求速率、并发和 SSE 流数量；流失败时回退轮询。快速切换案例时忽略过期响应。

验收：后端 269 passed，Ruff、mypy（30 个文件）和检索回归通过；前端 Vitest 15 passed、ESLint、TypeScript、Vite 构建通过。独立 Compose 项目重建后三项服务健康，HTTP 冒烟覆盖五种结果、补丁、轮询、SSE、白名单和只读边界；已执行 `docker compose down` 并保留数据卷。完整后端测试所需 Starlette 快照经用户授权，从本机 Git 对象库离线重建于当前 worktree。
