# 15: 扩展到 10 条补丁案例并计算指标

**What to build:** 将单案例补丁验证扩展到 10 条可稳定复现的调查案例，允许每条最多两次候选补丁，并报告复现、应用、测试、回归、成本和耗时结果。

**Blocked by:** 11: 扩充并冻结正式评测数据集；14: 在一个案例中贯通补丁验证沙箱

**Status:** resolved

- [x] 10 条案例均固定修复前 commit、兼容 Python 版本、基础环境和目标失败测试。
- [x] 每条案例最多执行两次补丁尝试并保留每次 diff 与测试结果。
- [x] 替代补丁通过相同行为测试且无相关回归时可判定成功。
- [x] 报告包含 Reproduction Rate、Patch Apply Rate、Target Test Pass Rate、Regression-free Rate、Pass@1 和 Pass@2。
- [x] 每条案例均报告运行时间、工具调用、Token、费用和终止原因。
- [x] 无法重建的冻结案例不会被静默移除，必须以运行失败保留在分母中。
- [x] 补丁成功率不足 50% 时，生成的项目表述不会声称自动修复成功。

## 评论

2026-10-09 接续记录：从工单 14 的完成提交 `fc64a29` 建立独立分支，并固定主评测集前 10 条作为 `starlette-patch-v1`。已完成报告公共接缝的逐项红—绿验证：缺失运行仍留在分母，最多两次尝试分别记录，逐案保留结果、diff、成本、耗时和终止原因。现有工单 14 案例的真实 Docker 沙箱回归通过；后端检查为 229 passed、Ruff 和 mypy 通过。这些仅证明报告与单案例基础能力，尚未满足本工单的十案例验收。

九条新增案例的修复前提交在本地 Git 缓存中可解析，但缓存缺少检出所需的 blob；示例失败命令：`git -C .retrieval-cache\starlette show --format= 76cd611b507635a325a24c741da10081d62c1249 -- starlette/routing.py tests/test_routing.py`，原始错误为 `fatal: unable to read 3c11c1b0cc0152f2955680f2461fe331c0932119`。项目作者随后授权获取这些缺失对象，已据此构建每条案例的完整、固定源码快照。

2026-10-09 验收进度：十条案例在固定 Python 3.10 镜像中均完成两次相同的修复前失败复现；八条固定候选补丁通过应用、目标行为和回归验证，495 与 847 暂无候选，仍保留在十条分母中。1578 与 746 首次回归配置错误的原始记录保留为 `preflight-failure.json`，修正测试配置后的正式候选尝试为 `attempt-1.json`。报告见 `data/patch-evaluation/report.json`：六项指标分别为 100%、80%、80%、80%、80%、80%。其中候选补丁包含参考修复来源与独立替代实现，结果不能表述为 Agent 自动修复成功。后端 229 tests、Ruff、mypy 通过；前端 3 tests、ESLint、TypeScript、Vite 通过；Compose 使用已有本地镜像健康启动并通过 `/api/health`。按交接文档要求的 `docker compose up --build` 在 `RUN pip install --no-cache-dir uv==0.12.7` 阶段需要联网下载依赖，已中断并另行请求本次依赖构建授权；授权前保持 `claimed`，不创建完成提交。

## 答案

已固定主评测集前十条 Starlette 案例、Python 3.10 沙箱和目标失败测试，并逐案写入候选验证记录。报告保留无候选案例和不可重建案例的分母，限制最多两次候选尝试，记录每次 diff、测试结果及逐案耗时、工具调用、Token、费用与终止原因。十条均复现；八条固定候选通过目标与回归测试，495、847 为 `no_candidate`。六项指标依次为 100%、80%、80%、80%、80%、80%。参考修复与替代补丁均标注来源，不声称 Agent 自动修复成功。

验收：`scripts/check_backend.py` 为 229 passed，Ruff、mypy 通过；前端 Vitest 3 passed、ESLint、TypeScript、Vite build 通过；补丁沙箱集成检查 6 passed；`docker compose config --quiet`、`docker compose up --build --detach --wait`、`curl.exe --fail http://localhost:3000/api/health` 均通过，随后执行 `docker compose down`。本地报告为 `data/patch-evaluation/report.json`；1578 与 746 的预检配置失败记录仍保留，未计作正式候选失败。
