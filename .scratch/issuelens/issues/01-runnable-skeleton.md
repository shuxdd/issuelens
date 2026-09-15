# 01: 建立可运行的 IssueLens 纵向骨架

**What to build:** 建立一个从浏览器到 API 再到数据库的最小可运行系统。开发者使用一条 Docker Compose 命令即可启动服务，用户能打开英文页面并看到后端健康状态，持续集成能验证后端测试、前端构建和容器启动。

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] 一条 Docker Compose 命令能够启动前端、后端和 PostgreSQL。
- [x] 后端健康接口返回 HTTP 200 和机器可读服务状态。
- [x] 英文首页能调用健康接口并显示成功或失败状态。
- [x] 后端测试、类型检查和 Ruff 检查可通过单一命令运行。
- [x] 前端 lint、类型检查和生产构建可通过单一命令运行。
- [x] GitHub Actions 能在无 API 密钥条件下完成上述检查和容器冒烟测试。

## 答案

已在提交 `d9a34d1` 中建立 FastAPI、React 和 PostgreSQL 的最小纵向骨架，并通过 Nginx 将首页和 `/api/health` 统一暴露在端口 3000。

验收记录（2026-09-15）：

- `uv sync --python 3.12 --frozen`：使用 Python 3.12.3 完成依赖同步。
- `uv run python scripts/check_backend.py`：pytest 1 项通过，Ruff 和 mypy 通过。
- `corepack pnpm@10.15.1 install --frozen-lockfile`：依赖安装成功。
- `corepack pnpm@10.15.1 check`：Vitest 3 项、ESLint、TypeScript 和 Vite 生产构建通过。
- `docker compose config --quiet`：通过。
- `docker compose up --build --detach --wait`：`postgres`、`backend` 和 `frontend` 成功启动；前两者报告 healthy，前端可从端口 3000 访问。
- `curl.exe --fail http://localhost:3000/`：返回英文 IssueLens 首页；前端测试确认健康响应显示 `All systems operational`。
- `curl.exe --fail http://localhost:3000/api/health`：返回 HTTP 200 和 `{"service":"issuelens-api","status":"ok"}`。
- GitHub Actions：提交 `d9a34d1` 与交接提交 `adedec1` 的 `CI` workflow 均完成且结论为 `success`。
- `docker compose down`：测试服务已停止，PostgreSQL 数据卷保留。
