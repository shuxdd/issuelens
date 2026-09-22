# 06: 加入 Reranker 和检索回归门槛

**What to build:** 为混合检索增加可替换、可关闭的 Reranker，形成第三组检索方案，并用小型离线回归集防止后续修改悄悄降低根因文件召回。

**Blocked by:** 05: 加入 BM25 混合检索并完成首次对比

**Status:** resolved

- [x] Reranker 通过稳定接口接收候选并返回重排结果和分数。
- [x] 配置关闭 Reranker 后，系统退回混合检索且无需修改业务代码。
- [x] 三组检索实验使用相同数据版本并生成可比较报告。
- [x] embedding、Reranker、切分、融合和 Top-k 参数全部进入实验元数据。
- [x] 小型检索回归集在 CI 中无网络、无生成模型费用运行。
- [x] 回归指标低于固定阈值时 CI 返回失败。

## 答案

验收记录（2026-09-22）：

- 已实现 `Reranker.score(query, G2 candidates)`、本地 FastEmbed cross encoder、关闭开关、G1/G2/G3 同输入运行器。关闭时逐字段返回原 G2 对象。G3 对 G2 Top-10 排序，保留 commit/path/行号/引用、向量分数、BM25 分数、RRF 原始分数与 G2 rank；最终按 6 位小数的重排分数及稳定 tie-break 排序。REST `GET /retrieval/three-way/{case_id}` 审查版本化结果。
- 模型 `Xenova/ms-marco-MiniLM-L-6-v2` 固定 revision `a09144355adeed5f58c8ed011d209bf8ee5a1fec`，ONNX SHA-256 `c623d0bcb99f4622beb413eaef00cfbe5db20df9f1dd982da4b4f26022881870`，原始模型卡 Apache-2.0；全部七个本地文件哈希记在 `reranker-config.json`，总计 91,938,993 字节，输入上限 512 token，CPU 单线程，运行时 `HF_HUB_OFFLINE=1`。模型文件仅在忽略的 `.retrieval-cache/models`。新增按 embedding identifier、维度、完整文档输入键控的 `.retrieval-cache/indexes` 持久向量缓存；刚完成的真实实验在该缓存加入前运行，故其构建延迟仍为未缓存实测；下次首次运行需建一次索引并写入缓存。
- G1/G2/G3 共用 Starlette issue 1552、snapshot `e086fc2...`、同一 query、850 个 chunk、embedding `BAAI/bge-small-en-v1.5@523982...`、BM25/RRF 参数、Top-10 和同一 gold。脚本确认 G1/G2 候选与工单 05 的版本化结果逐字段相同后才写 `three-way.json`；30 个候选的 commit/path/行号/引用均可解析。真实模型：G1 root Recall@5/@10 = 1/1、MRR = 0.5、nDCG@10 = 0.63093；G2 = 1/1、1、1；G3 = 1/1、0.5、0.63093。G3 达到预注册门槛，但在这一个真实案例上低于 G2，不宣称整体提升。
- 真实实验 5 次测量中位数：G1 构建 326.639 秒、查询 83.420 毫秒；G2 构建 326.713 秒、查询 87.653 毫秒；G3 共用 G2 构建，重排 1.410 秒、总查询 1.504 秒。向量载荷 1,305,600 字节，BM25 序列化 315,274 字节，Reranker 模型 91,938,993 字节；模型校验及加载 0.949 秒。结果 JSON 包含版本、参数、环境、原始阶段分数和墙钟样本。
- 版本化回归集只有一个真实 snapshot 案例与一个手工固定小语料，不能视作两个独立真实案例。预注册阈值：root Recall@5/@10 均为 1、MRR 至少 0.5、nDCG@10 至少 0.63；changed/test Recall 单独报告。CI 用无网络确定性替身，真实案例结果为 1/1、1、1；手工语料为 1/1、1、0.944848。负例移除根因候选后 CLI 返回 1；双次运行、手算指标、repair_evidence 唯一标记不可见、网络封锁、同输入和阶段追踪均由测试覆盖。真实模型实验与 CI 门槛分离。
- 验证：`$env:UV_CACHE_DIR='.uv-cache'; $env:UV_OFFLINE='1'; $env:HF_HUB_OFFLINE='1'; uv run --frozen --offline python scripts/check_backend.py`：75 passed，Ruff/mypy 通过；同环境下 `scripts/check_retrieval_regression.py`：退出 0；`scripts/run_reranker_comparison.py`：退出 0。现有 10 案例 Schema、来源和泄漏测试包含在后端全测。前端 `pnpm check` 包装器原始错误 `[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: esbuild@0.28.2`；从 `frontend/` 直接运行 Vitest（3 passed）、ESLint、TypeScript、Vite build 均通过。`docker compose config --quiet` 与 `git diff --check` 退出 0。
- 2026-09-21 `docker compose up --build --detach --wait` 曾成功；2026-09-22 Docker Desktop 重启后，配置中的 21 个 registry mirror 导致基础镜像清单和层返回 HTML，原始错误为 `encountered unknown type text/html; children may not be fetched` 与 `unexpected media type text/html`。备份本机 `daemon.json` 后临时清空 mirror 列表并重启 Docker Desktop，`docker pull nginx:1.27-alpine` 与完整 `docker compose up --build --detach --wait` 均成功。新构建的容器中首页、`/api/health`、案例审查、工单 04、工单 05、工单 06 六个 HTTP 路径均返回 200；`docker compose down` 已执行，未删除 PostgreSQL 数据卷。最终复跑后端 75 passed、Ruff/mypy、离线回归门槛、前端四项、`docker compose config --quiet` 均通过。Docker daemon 目前为直连 Docker Hub，原配置备份在忽略的 `.retrieval-cache/diagnostics/daemon.json.before-mirror-test`。
