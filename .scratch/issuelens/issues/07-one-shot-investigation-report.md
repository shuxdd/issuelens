# 07: 从检索结果生成可引用的单轮调查报告

**What to build:** 让贡献者获得第一份英文结构化调查报告。G1、G2 和 G3 使用完全相同的单轮报告生成器，使不同检索方案可以在根因、引用和成本上公平比较。

**Blocked by:** 04: 用向量检索定位第一个根因文件

**Status:** resolved

- [x] 报告包含问题分类、事实、最多三个根因假设、证据、置信度和未解决问题。
- [x] 报告符合稳定 Schema，结构化输出无效时返回可诊断错误。
- [x] 每条事实或根因结论引用调查时点内的 commit、路径和行号，或标记为未验证假设。
- [x] G1、G2 和 G3 共用同一提示词、模型配置和报告生成逻辑。
- [x] 报告生成记录 Token、费用、延迟、模型和提示词版本。
- [x] 固定响应测试能覆盖有效报告、无效引用和结构化输出失败。

## 评论

2026-09-22 接续进展（保持 `claimed`，未提交）：报告公共接缝、引用校验、G1/G2/G3 共用生成器、固定响应测试和只读 REST 审查已实现。离线后端检查 103 passed，Ruff/mypy、检索回归、前端 Vitest/ESLint/TypeScript/Vite build、`docker compose config --quiet` 均通过。最新远端 CI 成功，但只覆盖 `f1486d7`，未覆盖本地工单 06/07。当前沙箱直接访问 Docker 的原始错误为 `permission denied while trying to connect to the docker API at npipe:////./pipe/docker_engine`；提升权限后的只读 `docker version` 确认引擎为 29.3.1。Docker 构建可能联网，已请求确认，未启动。真实生成模型及单案例三组费用上限尚待指定，因此未调用模型 API、未生成真实实验结果；验收未完成，不创建完成提交。

## 答案

2026-09-22：实现英文报告 Schema `1.0`、候选与证据的调查时点快照校验、逐字段结构化输出诊断、未验证根因标记、G1/G2/G3 共用单轮提示词/生成器、DeepSeek JSON 模式适配器及只读 `/api/reports/three-way/{case_id}`。从工单 04–06 的版本化 `RetrievalInput` 和 `three-way.json` 还原现有候选类型；生成器仅接收 issue query 和候选文本/引用，不读取 gold、repair_evidence 或修复后文件。固定响应测试覆盖有效报告、无效引用、未验证假设、输出失败、共享生成器、真实用量计算及未来信息不可见。Windows 的检索回归清单通过 `.gitattributes` 固定 LF，避免 Git 自动 CRLF 导致 SHA 不一致。

用户确认后仅调用 DeepSeek API 三次：`deepseek-flash`（DeepSeek-V4.1-Flash），非思考 JSON 模式，`one-shot-v1`，每次输出上限 2048 Token。版本化价格来自 `https://api-docs.deepseek.com/quick_start/pricing`；当时为非高峰价，按服务返回的真实 Token 与缓存命中数计算费用，非账单回执。G1：5071 输入 / 1461 输出 / 6532 合计，0 缓存，US$0.00163725，5.178 秒。G2：4192 / 948 / 5140，128 缓存，US$0.001178784，3.899 秒。G3：4192 / 905 / 5097，128 缓存，US$0.001152984，3.963 秒。三组合计 16769 Token、US$0.003969018；调用前按峰时估计上界 US$0.0298215，低于批准的 ¥5 上限。产物为 `data/reports/starlette-issue-1552/report.json`，包含逐组报告、模型、提示词版本、Token、计算费用及延迟。密钥只在 Git 忽略的根 `.env` 中，未提交。

最终验收：`uv run --frozen --offline python scripts/check_backend.py` 为 113 passed，Ruff 与 mypy 通过；检索回归 `status=passed`，清单 SHA `0662c5d7eb5b9b633f6a4b1fc0cfca8a481245be27079343d859c42fb79e7b6c`；前端 Vitest 3 passed、ESLint、TypeScript、Vite build 均通过；`docker compose config --quiet` 通过；全新 `docker compose up --build --detach --wait` 后三个服务健康，首页、health、案例、三条检索 REST 和报告 REST 共 7 条路径均 HTTP 200；`docker compose down` 后 `issuelens_postgres-data` 数据卷仍在。

`pnpm check` 包装器在进入脚本前的原始错误：`[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: esbuild@0.28.2`，按要求从 `frontend/` 直接执行四项检查；未改依赖清单，并清理包装器生成的临时文件。最新 GitHub Actions 成功运行只覆盖远端 `f1486d7`，不声称已验证本地 07 提交。普通 CI、测试、导入及 HTTP 冒烟无需真实模型密钥或付费调用。
