# IssueLens 跨设备开发交接

本文供项目作者和新电脑上的 Codex 使用。它说明如何从 GitHub 接续当前项目、收口工单 01，并从工单 02 开始按统一流程继续开发。

仓库文件是最终事实来源。每次接续时先检查 Git 状态、工单状态和 CI，不要仅依赖本文记录的历史状态。

## 给新 Codex 的首条指令

在新电脑打开仓库后，先发送：

> 请先完整读取 `AGENTS.md` 和 `docs/agents/development-handoff.md`，检查 Git、工单与 CI 当前状态。先收口仍处于 `claimed` 的最低编号未阻塞工单；一次只处理一张工单，不提前实现后续工单。用户要求 TDD 时，严格在已确认的公共接缝上逐项执行红—绿循环。完成后运行全部验收检查、更新工单状态，并按仓库规则创建独立提交。未经我明确要求不要推送。

完成标准：Codex 已报告当前分支、工作区状态、当前工单、阻塞关系以及准备执行的第一项验收或红测。

## 当前交接状态

截至提交 `d9a34d1`：

- 远程仓库：`https://github.com/shuxdd/issuelens.git`
- 当前主分支：`main`
- 工单 01 的实现提交：`d9a34d1 feat: add runnable IssueLens skeleton`
- 工单 01 状态仍为 `claimed`，原因是原电脑没有可用的 Docker 引擎，动态容器冒烟尚未在本地确认。
- 已通过的检查：后端测试、Ruff、mypy、前端测试、ESLint、TypeScript、Vite 生产构建和 `docker compose config --quiet`。
- 待确认的检查：前端、后端与 PostgreSQL 三个容器能够启动，首页和 `/api/health` 能通过前端容器访问。
- 下一张工单是 `02-single-investigation-case.md`，它被工单 01 阻塞；工单 01 解决前不开始工单 02。

这次迁移是一次过渡例外：工单 01 的实现已经提交。Docker 验证通过后，可用一个仅包含工单状态和验收记录的文档提交收口。自工单 02 起，每张工单保持一个最终实现提交。

## 原电脑：确保交接文档已上传

在原电脑的仓库根目录执行：

```powershell
git status
git add AGENTS.md docs/agents/development-handoff.md
git diff --cached
git commit -m "docs: add cross-device development workflow"
git push origin main
```

完成标准：`git status -sb` 显示 `main...origin/main`，且没有未提交改动。

## 新电脑：首次准备

### 1. 安装工具

需要以下工具：

- Git
- Docker Desktop，并使用 Linux containers
- Python 3.12
- uv
- Node.js 22
- Corepack 和 pnpm 10.15.1

工单 01 的 Docker 验收不需要模型 API、Langfuse 或其他密钥。

### 2. 克隆仓库

```powershell
git clone https://github.com/shuxdd/issuelens.git
cd issuelens
git status -sb
git log -3 --oneline
```

完成标准：当前分支为 `main`，工作区干净，并且日志中能看到实现提交 `d9a34d1` 和交接文档提交。

### 3. 读取项目约束

新 Codex 必须完整读取：

1. `AGENTS.md`
2. 本文档
3. `CONTEXT.md`
4. `.scratch/issuelens/spec.md`
5. 当前工单文件
6. 当前区域相关的 `docs/adr/` 文档

问题跟踪规则、分诊标签和领域文档规则分别由 `AGENTS.md` 中的指针加载。

## 收口工单 01

### 1. 本地静态检查

在仓库根目录运行后端完整检查：

```powershell
$env:UV_CACHE_DIR = ".uv-cache"
uv sync --frozen
uv run python scripts/check_backend.py
```

运行前端完整检查：

```powershell
corepack enable
corepack prepare pnpm@10.15.1 --activate
cd frontend
pnpm install --frozen-lockfile
pnpm check
cd ..
```

完成标准：pytest、Ruff、mypy、Vitest、ESLint、TypeScript 和 Vite build 全部以退出码 0 结束。

### 2. Docker 冒烟

确认 Docker Desktop 已启动，然后执行：

```powershell
docker version
docker compose config --quiet
docker compose up --build --detach --wait
docker compose ps
curl.exe --fail http://localhost:3000/
curl.exe --fail http://localhost:3000/api/health
```

健康接口必须返回 HTTP 200，响应内容为：

```json
{"service":"issuelens-api","status":"ok"}
```

浏览器打开 `http://localhost:3000`，页面必须显示英文标题和 `All systems operational`。

如启动失败，先保留容器并检查：

```powershell
docker compose ps
docker compose logs --no-color
```

检查结束后停止服务：

```powershell
docker compose down
```

本地开发默认保留 PostgreSQL 数据卷。只有明确需要清空本地数据库时才执行 `docker compose down --volumes`。

完成标准：`postgres` 健康、`backend` 健康、`frontend` 正在运行，首页和健康接口均可通过端口 3000 访问。

### 3. 检查 GitHub Actions

打开仓库的 Actions 页面，确认最新 `CI` 工作流通过。CI 应在无 API 密钥条件下完成后端检查、前端检查和 Docker 冒烟。

完成标准：对应提交的 `CI` workflow 全部为绿色。

### 4. 更新工单 01

编辑 `.scratch/issuelens/issues/01-runnable-skeleton.md`：

- 勾选所有已经验证的验收项。
- 将 `Status:` 更新为 `resolved`。
- 在文件末尾增加 `## 答案`，记录实现摘要、运行过的命令和结果。

然后提交状态记录：

```powershell
git add .scratch/issuelens/issues/01-runnable-skeleton.md
git diff --cached
git commit -m "docs: record issue 01 acceptance"
```

只有用户明确要求后才执行：

```powershell
git push origin main
```

完成标准：工单 01 为 `resolved`，全部验收项已勾选，答案包含本地和 CI 验收证据。

## 从工单 02 开始的固定流程

### 1. 选择工单

读取 `.scratch/issuelens/issues/`，选择编号最小、状态未解决、`Blocked by` 中所有工单均已 `resolved` 的工单。用户明确指定工单时仍需先验证其未受阻。

每次只处理一张工单。

### 2. 创建分支并认领

以工单 02 为例：

```powershell
git switch main
git pull --ff-only
git switch -c feat/issue-02-single-investigation-case
```

将工单的 `Status:` 更新为 `claimed`。读取总规格、领域文档、相关 ADR 和完整工单验收项。

完成标准：分支名对应当前工单，工单已认领，阻塞条件均已解决，实现范围已经从验收项中明确。

### 3. 确认公共接缝

用户要求 TDD 时，在写测试前列出并确认本工单使用的公共接缝。测试只通过公共接口观察行为，不测试私有函数、内部调用顺序或 LangGraph 节点实现。

工单 02 已在总规格中确认的主要接缝是：

- 数据集构建接缝：固定 GitHub 响应缓存和固定仓库快照作为输入，输出版本化调查案例及筛选报告。
- REST/JSON 审查接缝：通过案例标识读取来源、调查时点、证据边界和校验状态。

完成标准：测试接缝有规格依据，且测试可以通过公共输入和输出验证行为。

### 4. 执行红—绿循环

每轮严格执行：

1. 添加一个描述可观察行为的测试。
2. 运行该测试并确认它因缺少当前行为而失败。
3. 编写使该测试通过的最小实现。
4. 重新运行并确认测试通过。
5. 再进入下一条验收行为。

预期值来自规格、固定样例或独立演算结果。只在数据库、文件系统、网络、时钟或外部 API 等系统边界使用固定替身。

实现以当前工单验收项为边界。后续工单需要的能力可以记录，但不提前编写。

### 5. 运行全部验收

每张工单至少重新运行现有全量检查：

```powershell
$env:UV_CACHE_DIR = ".uv-cache"
uv run python scripts/check_backend.py

cd frontend
pnpm check
cd ..

docker compose config --quiet
docker compose up --build --detach --wait
curl.exe --fail http://localhost:3000/api/health
docker compose down
```

同时运行当前工单列出的数据 Schema、防泄漏、检索回归、离线评测或沙箱检查。正式模型评测只能在工单明确要求时运行，不得混入普通 CI。

完成标准：当前工单的每个验收项均有已执行的检查或人工验证证据，已有功能的回归检查保持通过。

### 6. 解决工单并提交

在当前工单文件中：

- 勾选已通过的验收项。
- 在 `## 答案` 下记录实现和验收证据。
- 将 `Status:` 改为 `resolved`。

只暂存当前工单相关文件：

```powershell
git status
git add <当前工单相关文件>
git diff --cached
git commit -m "feat: <当前工单的完成结果>"
```

提交前必须确认暂存区不包含其他工单或用户的无关改动。验收失败或受外部条件阻塞时保持 `claimed`，不创建完成提交。

完成标准：工作区中当前工单的改动均已纳入一个语义明确的本地提交，且提交包含实现、测试和工单状态更新。

### 7. 推送与 CI

只有用户明确要求后才推送：

```powershell
git push -u origin HEAD
```

创建 Pull Request 后等待 CI。若 CI 发现问题，在同一工单分支修复并重新运行验收；合并前将该工单整理为一个最终提交。个人工单分支可以在确认无人依赖时使用 `git commit --amend` 和 `git push --force-with-lease`，共享分支和 `main` 不改写历史。

完成标准：Pull Request 的 CI 通过，合并到 `main` 后再开始下一张工单。

## 工单路线

按现有依赖关系推进：

- 01：可运行骨架与容器冒烟
- 02–03：版本化调查案例、真实案例构建与未来信息隔离
- 04–06：向量检索、BM25 混合检索与 Reranker 回归门禁
- 07–09：单轮调查报告、多步调查 Agent、终态和预算
- 10–13：可观测性、可比较实验、冻结评测与安全终止
- 14–15：受限补丁沙箱与补丁评测
- 16–18：受控 Demo、可复现发布与作品集交付

工单文件中的 `Blocked by` 和验收项始终优先于这份摘要。

## 安全与恢复规则

- 每次开始前运行 `git status`；现有改动默认属于用户，先识别再操作。
- 使用 `git pull --ff-only` 同步主分支，避免意外合并提交。
- 使用 `git diff` 和 `git diff --cached` 审查实际改动。
- 通过新分支保存工单工作；共享分支和 `main` 保持可追溯历史。
- 不提交 `.env`、API 密钥、访问令牌、数据库数据卷或模型凭据。
- 不使用 `git reset --hard`、`git clean -fd` 或强制推送共享分支。
- 外部环境阻塞时记录失败命令与原始错误，保持工单 `claimed`，等待环境恢复后从验收步骤继续。
