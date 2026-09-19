# 04: 用向量检索定位第一个根因文件

**What to build:** 让贡献者针对一个调查案例执行最小检索流程，从调查时点的代码和文档中获得带 commit、路径、行号和分数的候选证据，并看到根因文件 Recall 指标。

**Blocked by:** 02: 导入并审查一个固定调查案例

**Status:** resolved

- [x] Python 代码、测试和 Markdown 文档可按各自结构解析与切分。
- [x] 每个检索片段保留来源类型、commit、路径、行号范围和索引版本。
- [x] 单个案例可以建立向量索引并返回排序后的 Top-k 结果。
- [x] CLI 或 API 能展示查询、候选片段、分数和可解析引用。
- [x] 评测能够分别识别根因文件、修复修改文件和测试文件。
- [x] 使用固定模型和输入连续运行两次得到一致排名。

## 答案

完成时间：`2026-09-19T14:37:08Z`

### 实现摘要

- 复用工单 03 的 `data/cases/*.json`、调查时间切片和 REST 应用；检索输入只投影 `case_id`、数据版本、仓库、snapshot commit、issue 标题和 issue URL。
- 只从案例固定快照构建语料：Python 生产代码和测试使用标准库 `ast` 顶层结构及真实源码行号，Markdown 按围栏代码块之外的标题切分；超过上限的结构块按带重叠的行窗口继续切分。
- 使用 FastEmbed 的固定本地 BGE 模型生成真实 384 维向量，在内存中做余弦相似度线性扫描；分数固定为 6 位小数，按 `score desc, path, start_line, end_line, source_type` 稳定排序。
- 新增 `GET /retrieval/cases/{case_id}`，直接展示 Git 版本化的 query、Top-k 候选、分数、commit、路径、行号、source type、index version、引用和三类 Recall。
- 案例、gold、索引配置和评测结果仍以版本化 JSON 为事实源。向量只在内存中派生，不引入 pgvector。单案例 850 chunks 适合线性扫描；多案例规模或实测延迟超出预算时再迁移。

### 第一个案例与时间切片

- 案例：`starlette-issue-1552`
- 查询：`Route naming introspection always return "method" for method endpoints`
- 查询来源：仅 `issue.title`，未使用 PR 标题、修复文件名、测试文件名或人工答案。
- snapshot commit：`e086fc2da361767b532cf690e5203619bbae98aa`
- 固定源码归档 SHA-256：`5d5d68f0ea4c6a77389d1195340e58e81321546f3b55ce2b3b76bc81558d9c04`
- `#3497` 保持排除，没有恢复。

### Chunk 与索引配置

- 总数：850
- `python_source`：166
- `python_test`：445
- `markdown`：239
- chunk：`max_lines=120`、`overlap_lines=20`、`python_parser=ast-v1`、`markdown_parser=headings-v1`
- index version：`starlette-1552-vector-v1`
- embedding：`fastembed==0.8.0`，`BAAI/bge-small-en-v1.5@52398278842ec682c6f32300af41344b1c0b0bb2`，384 维，`threads=1`，`local_files_only=true`
- 模型缓存：67,179,163 bytes；文件哈希记录在 `data/retrieval/starlette-issue-1552/config.json`。模型权重和仓库快照只在忽略的 `.retrieval-cache/`，不进入 Git。
- 索引：内存线性扫描、cosine、Top-10、6 位小数、`retrieval-v1`。

### Top-10

| Rank | Score | Path | Lines | Type |
| ---: | ---: | --- | ---: | --- |
| 1 | 0.744283 | `docs/release-notes.md` | 449-452 | markdown |
| 2 | 0.734856 | `starlette/routing.py` | 189-274 | python_source |
| 3 | 0.727545 | `starlette/schemas.py` | 25-28 | python_source |
| 4 | 0.724352 | `starlette/routing.py` | 86-89 | python_source |
| 5 | 0.710456 | `docs/release-notes.md` | 382-386 | markdown |
| 6 | 0.705086 | `starlette/routing.py` | 277-332 | python_source |
| 7 | 0.703661 | `docs/release-notes.md` | 484-491 | markdown |
| 8 | 0.699479 | `starlette/routing.py` | 737-828 | python_source |
| 9 | 0.697739 | `starlette/routing.py` | 335-425 | python_source |
| 10 | 0.697208 | `starlette/routing.py` | 28-36 | python_source |

### Gold 与 Recall@10

- `root_cause_files`：1/1，Recall=1.0。人工复核依据是 snapshot 中 `starlette/routing.py:86-89` 的 `get_name` 未识别 bound method，以及 `:202` 的调用点。
- `changed_files`：1/1，Recall=1.0。
- `test_files`：0/1，Recall=0.0。
- 三类标注分别保存在 `gold.json`，分别计算；`starlette/routing.py` 同时出现在 root-cause 和 changed 集合并不意味着二者语义合并。测试文件未命中如实保留为 0，没有向查询泄漏测试文件名来抬高指标。

### 确定性与防泄漏

- 使用同一固定模型和输入连续完整构建、检索两次，`Corpus` 与 `RetrievalRun` 均逐字段相等；固定序列化测试还验证相同输入得到相同 JSON 字节。
- 并列分数测试故意以 `z, b, a, c` 顺序输入，结果稳定为 `a, z, b`。
- `REPAIR_ONLY_SENTINEL_04` 只放在 `repair_evidence`。测试封锁 `socket.connect`，捕获 fake embedder 收到的 query 和全部文档，确认该标记不可见且没有真实网络或模型 API 调用。
- FastEmbed 适配器固定模型、revision、维度和单线程，并强制 `local_files_only=True`；普通测试和 CI 只用确定性 fake，CI 另设 `HF_HUB_OFFLINE=1`。
- 真实 Top-10 的 10 个路径均存在于固定快照，行号范围合法，候选 `content` 与对应源码行逐字一致，引用可解析。

### 命令结果

- 工单 04 定向测试：`13 passed`；加入版本化结果契约后全量后端共 `41 passed`，仅 2 个既存 TestClient 弃用警告。
- `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_backend.py`：测试、Ruff、mypy 全部通过。
- 真实离线检索：`HF_HUB_OFFLINE=1` 且 FastEmbed `local_files_only=True`，两次结果一致；850 chunks，Recall 如上。
- 真实引用检查：`REAL_REFERENCES_OK 10`。
- `pnpm check`：在进入项目脚本前由 Codex 包装器失败，原始错误为 `[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: esbuild@0.28.2`；未修改依赖清单或审批策略。
- 直接项目命令：Vitest `3 passed`；ESLint exit 0；TypeScript `tsc --noEmit` exit 0；Vite build exit 0。
- `docker compose config --quiet`：exit 0；仅沙箱内读取用户 Docker 配置时出现权限警告。
- 首次沙箱内 `docker compose up --build --detach --wait` 无法访问 Docker named pipe；宿主重试确认 daemon 未启动。启动已安装的 Docker Desktop 后再次运行成功，三个服务均 Healthy。
- 容器冒烟：首页 200；`/api/health` 返回 `ok`；现有案例接口返回 `starlette-issue-1552`；新增检索入口返回 10 个候选和 root-cause Recall 1.0。
- `docker compose down`：exit 0；没有使用 `--volumes`，本地 PostgreSQL 数据卷保留。
- `git diff --check`：通过；范围审查未发现 BM25、混合融合、Reranker、调查 Agent 或 Langfuse。
