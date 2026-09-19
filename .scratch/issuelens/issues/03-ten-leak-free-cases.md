# 03: 构建 10 条无泄漏 Starlette 调查案例

**What to build:** 从真实 GitHub 公开数据生成首批 10 条可人工审查的 Starlette 调查案例，可靠恢复 Issue 与 closing PR 的关系，记录筛选漏斗，并阻止调查时点之后的信息进入允许证据。

**Blocked by:** 02: 导入并审查一个固定调查案例

**Status:** resolved

- [x] 数据采集能够缓存 Issue、timeline、交叉引用、PR、提交和文件变更元数据。
- [x] Issue–PR 关系通过 timeline 或交叉引用恢复，而不只依赖正文正则。
- [x] 构建结果包含 10 条人工复核通过的调查案例。
- [x] 每条案例都有可追溯来源、调查时点、快照 commit 和测试变更引用。
- [x] 安全漏洞、纯文档、依赖机器人、跨仓根因和不可分析案例被排除并记录原因。
- [x] 人工注入未来证据时，泄漏检查返回失败。
- [x] 重复采集使用缓存并保持幂等，且能输出各筛选阶段的数量。

## 答案

实现结果：

- 已使用 GitHub Issue、timeline/cross-reference、closing PR、commit、file 元数据固定缓存构建案例；正文、PR 讨论和 diff patch 未进入缓存。
- 10 条人工复核通过案例：`1552`、`2298`、`2306`、`2516`、`2625`、`2646`、`2692`、`2785`、`3357`、`3388`。
- 筛选漏斗：collected `17` → relationship_recovered `14` → category_eligible `14` → analyzable `10` → time_slice_valid `10` → manual_reviewed `10` → accepted `10`。
- 排除原因覆盖：安全漏洞、纯文档、依赖机器人、跨仓根因、不可分析；另将旧候选 `3497` 因 closing PR 无测试文件变更排除。
- 每条案例记录 closing PR 创建时间作为调查时点、此前默认分支最新 commit、修复/测试文件分类、来源引用和完整人工复核依据；允许证据与修复证据分别使用 `investigation` / `evaluation_only` 权限。
- 泄漏检查会拒绝调查时点当时或之后的允许证据；人工注入未来证据测试通过。批量重复构建的案例与筛选报告字节一致，缓存命中测试使用会在网络调用时立即失败的 opener。

验收结果：

- `uv run pytest backend/tests/test_case_builder.py backend/tests/test_case_validation.py backend/tests/test_case_review.py -q`：23 passed。
- 真实数据定向检查：10 条案例全部通过 Schema、UTC、来源、权限、人工复核和泄漏验证；筛选漏斗与两次离线构建幂等测试通过。
- `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_backend.py`：27 passed；ruff passed；mypy passed（2 条既有依赖弃用警告）。
- 前端原生命令：Vitest 3 passed；ESLint passed；TypeScript passed；Vite production build passed。
- `docker compose config --quiet`：通过。
- `docker compose up --build --detach --wait`：frontend、backend、postgres 均构建并达到 Healthy。
- 容器冒烟：首页 `200`；`/api/health` 返回 `issuelens-api / ok`；`/api/cases/starlette-issue-1552` 返回正确 case id 且 `validation_status=valid`。
- `docker compose down`：通过；未使用 `--volumes`，并确认 `issuelens_postgres-data` 卷仍存在。

备注：Codex 的 pnpm 包装器曾在运行项目前因非 TTY 和 esbuild 构建审批策略中止；未进入项目检查。已直接执行 `vitest run`、`eslint .`、`tsc --noEmit`、`vite build` 完成等价的前端完整检查，并删除包装器生成的临时 `.pnpm-store/` 与 `frontend/pnpm-workspace.yaml`。Docker Desktop 首次启动也因失效的 `dockerInference` socket 崩溃；重启系统后已恢复并完成上述容器验收，期间未执行 factory reset 或删除数据卷。
