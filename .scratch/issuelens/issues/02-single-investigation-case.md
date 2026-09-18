# 02: 导入并审查一个固定调查案例

**What to build:** 让项目作者能够从固定的 GitHub 响应和 Starlette 仓库快照生成一个版本化调查案例，并通过 CLI 或 API 审查 Issue、调查时点、允许证据、修复证据和来源信息。

**Blocked by:** 01: 建立可运行的 IssueLens 纵向骨架

**Status:** resolved

- [x] 固定输入可以生成一个符合版本化 Schema 的调查案例。
- [x] 案例包含 Issue 标识、调查时点、快照 commit、允许证据和修复证据引用。
- [x] 允许证据与修复证据在序列化结果中具有不同字段和读取权限。
- [x] CLI 或 API 能显示案例来源、时间边界和当前校验状态。
- [x] 同一固定输入连续构建两次产生等价结果，不重复创建逻辑案例。
- [x] 无网络、无模型 API 和无 Langfuse 时测试仍可运行。

## 答案

使用固定 GitHub 响应缓存和固定 Starlette 快照元数据构建了真实案例
`starlette-issue-3497`。调查时点固定为修复 PR #3498 的创建时间，仓库快照固定为
此前默认分支最新 commit `39fd0ffac25593fce39466320c9a666957ce8b8c`。案例和筛选报告
均以 Git 版本化 JSON 保存。

案例中的 `allowed_evidence` 使用 `investigation` 读取权限，`repair_evidence` 使用
`evaluation_only` 读取权限。`GET /cases/starlette-issue-3497` 提供来源、调查时点、
快照、证据边界和 Schema 校验状态。构建器使用稳定案例 ID 和确定性 JSON 序列化，重复
构建覆盖同一路径并产生等价结果。

验收记录（2026-09-18）：

- 三轮 TDD 红—绿循环覆盖固定案例构建、REST/JSON 审查和幂等构建；部署冒烟另发现并修复镜像未包含 `data/` 的问题。
- `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_backend.py`：pytest 4 项、Ruff 和 mypy 通过。
- 直接运行仓库现有的 Vitest、ESLint、TypeScript 和 Vite 可执行文件：前端测试 3 项、lint、类型检查和生产构建通过。
- `docker compose config --quiet`：通过。
- `docker compose up --build --detach --wait`：PostgreSQL、后端和前端均成功启动，健康检查通过。
- `curl.exe --fail http://localhost:3000/`：返回英文首页。
- `curl.exe --fail http://localhost:3000/api/health`：返回 HTTP 200 和健康状态。
- `curl.exe --fail http://localhost:3000/api/cases/starlette-issue-3497`：返回 HTTP 200、固定来源、时间边界、证据权限和 `valid` 校验状态。
- 全部测试只读取本地固定文件，不调用 GitHub、模型 API 或 Langfuse。
- `docker compose down`：验收容器已停止，PostgreSQL 数据卷保留。
