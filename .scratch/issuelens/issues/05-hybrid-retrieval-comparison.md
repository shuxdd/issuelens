# 05: 加入 BM25 混合检索并完成首次对比

**What to build:** 在完全相同的数据和查询上比较仅向量检索与 BM25 加向量的混合检索，让项目作者看到检索质量、延迟和索引成本的实际变化。

**Blocked by:** 04: 用向量检索定位第一个根因文件

**Status:** resolved

- [x] BM25 能检索代码符号、错误文本和文档术语。
- [x] 混合检索通过可配置融合策略合并关键词和向量结果。
- [x] 两组实验使用相同案例、切分、Top-k 和 gold 标注。
- [x] 报告包含 Recall@5、Recall@10、MRR、nDCG@10、延迟和索引规模。
- [x] 逐案例结果可以追溯到原始候选及各阶段分数。
- [x] 融合策略和参数被记录在机器可读实验元数据中。

## 答案

完成时间：`2026-09-20T15:21:16Z`

### 实现摘要

- 直接复用工单 04 的 `RetrievalInput`、`Corpus`、`Chunk`、结构化切分、向量检索、三类 Recall、引用与 REST 数据目录；没有建立第二套案例、chunk 或 gold 模型。
- 新增标准库内存 BM25 倒排索引、配置化 RRF、G1/G2 比较运行器、MRR/nDCG@10、稳定索引规模与构建/查询计时。
- 新增 `GET /retrieval/comparisons/{case_id}`，返回版本化的共享输入、G1/G2 指标、逐候选阶段分数、延迟、索引规模及完整实验元数据。
- 配置、gold 与实验结果继续以 Git 版本化 JSON 为事实源；内存索引只保存可重建派生数据。没有实现 Reranker、阈值门禁或第三组实验。

### BM25 tokenizer、公式与参数

- tokenizer `code-text-v1`：对 path 与 content 做 `casefold`，用 `[a-z0-9]+(?:_[a-z0-9]+)*` 取词；snake_case 同时保留完整符号和各组成词，不做 stemming 或 stop-word 删除。因此 `get_name`、`get name`、错误文本和 Markdown 术语都可检索。
- 对文档 `d` 中词项 `t`：`TF=f(t,d)`；`DF=n(t)`；`IDF=log(1 + (N-df+0.5)/(df+0.5))`；`dl` 是该 chunk 的 token 数，`avgdl` 是语料平均 token 数。
- 评分：`sum(IDF * TF*(k1+1) / (TF + k1*(1-b+b*dl/avgdl)))`，`k1=1.2`、`b=0.75`。
- BM25 分数保存 6 位小数字符串；并列按 `path, start_line, end_line, source_type` 排序。手算小语料验证分数为 `1.518488` 与 `0.470004`。

### 融合策略与公平对比

- 使用 `rrf-v1`：`score(d)=1/(60+vector_rank)+1/(60+bm25_rank)`，缺失通道贡献 0；`rank_constant=60`，向量和 BM25 权重均为 `1.0`，最终分数保存 8 位小数，并使用同一稳定引用 tie-break。
- 未直接相加 cosine 与 BM25 原始分数，因为二者尺度、分布和上界不同，原始相加会让某一通道因数值尺度而非排名证据支配结果。
- G1 与 G2 共用案例 `starlette-issue-1552`、数据版本 `cases-v1`、snapshot `e086fc2da361767b532cf690e5203619bbae98aa`、同一 850 chunks、同一 issue-title query、同一固定 384 维 BGE embedding、Top-10 与同一 gold；唯一实验变量是 G2 增加 BM25 与 RRF。

### 指标

| 组别 | Root Recall@5/@10 | Changed Recall@5/@10 | Test Recall@5/@10 | MRR | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| G1 vector | 1.0 / 1.0 | 1.0 / 1.0 | 0.0 / 0.0 | 0.5 | 0.63093 |
| G2 hybrid | 1.0 / 1.0 | 1.0 / 1.0 | 0.0 / 0.0 | 1.0 | 1.0 |

- Recall@k 是各 gold 类别在前 k 个候选中命中的唯一文件数除以该类别 gold 文件数，三类分别计算。
- MRR 是首个 root-cause 相关候选排名的倒数；nDCG@10 使用 `DCG=sum((2^rel-1)/log2(rank+1))` 再除以理想 DCG。二者只读取 `root_cause_files[].relevance`；本例人工 relevance 为 2，changed/test 条目没有 relevance 字段，不能参与排序指标。
- G2 将 `starlette/routing.py` 根因 chunk 从第 2 位提升到第 1 位；测试文件进入 G2 第 4 位，但三类 Recall 仍按 gold 语义分离，没有用 changed/test 文件计算 MRR 或 nDCG。

### 延迟与索引规模

- 使用 `time.perf_counter_ns()`；1 次 warm-up 后重复 5 次，记录样本、min、median、max。build 包含相应索引构建，query 包含检索；G2 query 还包含 RRF。单元测试注入假时钟，只断言测量边界和非负结果，不断言易波动的真实墙钟。
- G1 build 中位数 `260,899,863,400 ns`，query 中位数 `61,898,700 ns`；G2 build 中位数 `260,952,169,200 ns`，query 中位数 `64,878,400 ns`。环境：Windows 11、Python 3.12.3、AMD64，模型线程数 1。
- 向量索引：850 documents、384 dimensions、326,400 float32 values、1,305,600 payload bytes。
- BM25 索引：850 documents、69,542 tokens、3,835 terms、32,523 postings、315,274 deterministic serialized bytes。
- G1 总口径 1,305,600 bytes；G2 总口径 1,620,874 bytes。没有使用不稳定的 Python 对象地址或运行时内存估算；当前单案例继续用内存索引，多案例规模或实测延迟超过预算时再迁移持久化搜索存储。

### G2 Top-10 与阶段追踪

所有候选 commit 均为 `e086fc2da361767b532cf690e5203619bbae98aa`。`v`/`b` 分别为 `rank / raw score / RRF contribution`；`—` 表示未进入该通道 Top-10。

| Rank | Final | Path:lines | v | b |
| ---: | ---: | --- | --- | --- |
| 1 | 0.03062179 | `starlette/routing.py:189-274` | 2 / 0.734856 / 0.01612903 | 9 / 10.165772 / 0.01449275 |
| 2 | 0.01639344 | `docs/release-notes.md:449-452` | 1 / 0.744283 / 0.01639344 | — |
| 3 | 0.01639344 | `starlette/schemas.py:31-111` | — | 1 / 20.661785 / 0.01639344 |
| 4 | 0.01612903 | `tests/test_endpoints.py:39-43` | — | 2 / 15.379299 / 0.01612903 |
| 5 | 0.01587302 | `starlette/endpoints.py:14-54` | — | 3 / 13.706103 / 0.01587302 |
| 6 | 0.01587302 | `starlette/schemas.py:25-28` | 3 / 0.727545 / 0.01587302 | — |
| 7 | 0.01562500 | `docs/endpoints.md:5-50` | — | 4 / 13.157524 / 0.01562500 |
| 8 | 0.01562500 | `starlette/routing.py:86-89` | 4 / 0.724352 / 0.01562500 | — |
| 9 | 0.01538462 | `docs/release-notes.md:382-386` | 5 / 0.710456 / 0.01538462 | — |
| 10 | 0.01538462 | `starlette/schemas.py:114-135` | — | 5 / 12.684385 / 0.01538462 |

G1 Top-10 与工单 04 的版本化结果逐字段一致。完整候选内容、commit/path/行号引用以及 G1/G2 阶段数据保存在 `data/retrieval/starlette-issue-1552/comparison.json`。

### 确定性与防泄漏

- 每个 benchmark repetition 都检查 G1 与 G2 排名和分数逐字段一致；测试再以同一输入运行两次，移除真实 latency 后机器可读结果完全相等。BM25、向量与 RRF 都使用显式稳定 tie-break。
- `REPAIR_ONLY_SENTINEL_04` 只存在于 `repair_evidence`。测试封锁 `socket.connect` 并捕获 query、BM25 corpus 与 fake embedder 输入，确认四处都不可见；评测在检索完成后才读取 gold，且 API 只读取已版本化结果。
- 普通测试不加载生产模型，使用确定性 fake embedder；CI 设置 `HF_HUB_OFFLINE=1`。真实实验使用工单 04 已校验哈希的本地 snapshot 和模型缓存，以 `--frozen --offline` 运行。
- 20 个 G1/G2 候选均验证：commit 正确、path 存在、行号合法、content 与 snapshot 源码逐字一致、GitHub 引用格式可解析。

### 命令结果

- TDD 逐项完成 tokenizer/BM25、符号与文本、固定精度/tie-break、RRF、阶段追踪、共享输入、手算指标、三类 Recall、元数据/计时/索引规模、REST、防泄漏、双跑确定性和版本化结果契约。每轮先确认目标行为缺失导致红测，再用最小实现转绿。
- 工单 05 定向测试：`29 passed`；完整后端检查：`53 passed`，Ruff 与 mypy 通过，仅 2 个既存 TestClient 弃用警告。
- `$env:UV_CACHE_DIR='.uv-cache'; uv run python scripts/check_backend.py`：通过。
- 首次直接执行真实脚本因未设置源码路径失败：`ModuleNotFoundError: No module named 'issuelens'`；这是入口路径问题，不是红测、模型或网络故障。设置 `PYTHONPATH=backend/src` 后，`uv run --frozen --offline --extra retrieval python scripts/run_retrieval_comparison.py` 成功生成上述结果。
- 真实输入检查：snapshot archive 和 5 个模型文件哈希均与配置一致；`G1_MATCHES_ISSUE_04=True`；`REAL_REFERENCES_OK=20`。
- `pnpm check`：在进入项目脚本前失败，原始错误为 `[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: esbuild@0.28.2`；未修改依赖清单或审批策略。直接运行项目 Vitest `3 passed`、ESLint exit 0、TypeScript exit 0、Vite build exit 0。
- `docker compose config --quiet`：exit 0；仅沙箱读取用户 Docker 配置出现权限警告。
- `docker compose up --build --detach --wait`：沙箱内首次因 Docker named pipe 权限失败；批准后在宿主成功，三个服务均 Healthy。
- 容器冒烟：首页 200；`/api/health` 返回 `ok`；案例审查有效；工单 04 检索返回 10 个候选/root Recall 1.0；新增比较接口返回 `g1_vector`、`g2_hybrid`、G1 MRR 0.5、G2 MRR 1.0 和 10 个混合候选。
- `docker compose down`：exit 0；未使用 `--volumes`，本地 PostgreSQL 数据卷保留。
- `git diff --check`：通过；范围审查未发现 Reranker、回归阈值、调查 Agent、LangGraph、Langfuse、报告生成或补丁沙箱实现。
