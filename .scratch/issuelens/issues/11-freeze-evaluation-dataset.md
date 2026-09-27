# 11: 扩充并冻结正式评测数据集

**What to build:** 将数据管线扩展为可公开审查的正式评测数据集，按时间和修复系列划分开发与测试数据，冻结 30 条主评测案例和 10 条稳定性子集。

**Blocked by:** 03: 构建 10 条无泄漏 Starlette 调查案例；06: 加入 Reranker 和检索回归门槛

**Status:** resolved

- [x] 自动清洗产生 100–150 条候选并保存筛选漏斗。
- [x] 约 80 条候选完成人工复核并记录复核状态。
- [x] 主评测集恰好包含 30 条冻结案例。
- [x] 稳定性子集恰好包含预先固定的 10 条主评测案例。
- [x] 数据按调查时点排序并按相关修复系列分组，开发集与测试集不存在系列交叉。
- [x] gold 分别记录根因文件、修复修改文件、测试文件、根因机制和证据位置。
- [x] 调查时点后编辑且无法恢复原文的 Issue 不进入主评测集。
- [x] 冻结版本、预注册指标和防泄漏检查结果可由 Git 审查。

## 正式评测数据集构建与冻结设计（待项目作者确认）

本节只确定工单 11 的公共行为和版本化产物；确认前不写测试、不改实现、不联网。

### 复用范围与唯一事实来源

- 继续以 `backend/src/issuelens/cases.py` 的 `collect_github_data`、`build_cases`、
  `validate_case` 和 `check_cases` 为唯一采集、构建和校验接缝。扩展现有函数，不建立第二套
  案例、证据、引用或 `repair_evidence` 模型。
- 保留 `data/github-cache/`、`data/repository-snapshots/starlette/snapshot.json`、
  `data/reviews.json`、`data/cases/` 和 `data/screening-report.json` 的职责。
- 复用 `data/retrieval/starlette-issue-1552/gold.json` 及
  `backend/src/issuelens/retrieval.py` 对三类文件 gold、相关性等级和证据位置的现有口径。
- 新增的冻结清单和 gold 仍是 Git 版本化 JSON；Langfuse、PostgreSQL 和运行结果都不能反向
  修改这些文件。

### 候选来源、采集范围与缓存边界

- 数据源固定为 `Kludex/starlette`。发现范围固定为截至采集高水位时间已经关闭的 Issue，
  Issue 编号不高于采集清单记录的 `high_water_issue_number`；只接受 GitHub 同仓 timeline 或
  `closedByPullRequestsReferences` 指向的已合并 closing PR。
- 发现结果按 `(issue_number, closing_pr_number)` 排序并写入版本化候选清单；随后复用现有逐
  Issue 缓存格式采集 Issue、timeline/cross-reference、closing PR、提交和文件列表。达到
  120 条通过自动规则的候选即停止，目标始终位于要求的 100–150 条范围内。若完整固定范围
  不足 100 条则构建失败，不以其他仓库补足。
- 每个缓存对象增加来源 URL、ETag（若服务提供）、采集时间、请求参数和响应内容 SHA-256；
  输出构建不使用采集时间，因此相同版本化输入仍产生字节一致结果。
- 缓存仅保存完成筛选和审查所需字段：Issue 标题、正文、创建/关闭/最后编辑时间、作者和标签；
  timeline 关系元数据；closing PR 元数据；提交 SHA/时间；变更文件路径和状态。评论、PR 正文、
  review discussion、diff/patch、修复后文件内容、Authorization、Cookie、Token、完整响应头和
  环境变量不缓存。Issue 正文是调查输入所需的最小公开内容，不进入 repair evidence。
- Git 数据只用于确认默认分支时间点、commit 可取得、文件存在和 Python 静态解析；不运行
  候选补丁、仓库脚本、安装命令或真实模型 API。版本库只提交 commit、路径、哈希、检查结果
  和必要证据位置，不镜像完整 Starlette 仓库。

### 稳定筛选漏斗与排除代码

`data/screening-report.json` 保留每一阶段的输入数、通过数和逐项排除记录。顺序固定为：

1. `discovered`：固定查询范围内的关闭 Issue。
2. `relationship_recovered`：存在同仓已合并 closing PR；否则
   `missing_closing_pr_relationship`。
3. `repair_metadata_complete`：closing PR、提交和文件元数据完整；否则
   `incomplete_repair_metadata`。
4. `behavior_test_present`：至少一个 `tests/` 文件；否则 `missing_test_change`。
5. `category_eligible`：排除 `security_sensitive`、`documentation_only`、
   `dependency_bot` 和 `cross_repository_root_cause`。
6. `time_slice_valid`：按下述编辑历史规则排除
   `issue_body_edited_after_cutoff_unrecoverable`。
7. `snapshot_available`：能确定并读取调查时点前最新默认分支 commit；否则
   `snapshot_unavailable`。
8. `buildable`：能从固定元数据构造完整案例、gold 输入和来源引用；否则
   `case_construction_failed`。
9. `statically_analyzable`：引用的根因候选/修改/测试路径存在，所需 Python 文件能以标准库
   `ast` 解析；否则 `static_analysis_failed`。
10. `cleaned_candidates`：取上述全部通过项按固定排序的前 120 条；完整漏斗仍保留未入选项及
    `candidate_cap_reached` 原因。

分类只使用明确元数据和固定代码，不用 LLM 或自由文本猜测；无法确定时进入人工复核队列，
不自动判定为通过。

### 调查时点、快照和 Issue 编辑历史

- 调查时点继续使用 closing PR 的 `created_at`。
- 快照继续使用默认分支中严格早于调查时点的最新 commit；并记录 commit 时间、来源 URL 和
  选择依据。
- `lastEditedAt` 为空或严格早于调查时点时，当前正文可作为调查输入。若编辑发生在调查时点
  当时或之后，只在版本化缓存中存在一份采集时间严格早于调查时点、来源可追溯且哈希匹配的
  正文时才视为已恢复；本次新采集不会倒推伪造历史正文，因此通常稳定排除并记录
  `issue_body_edited_after_cutoff_unrecoverable`。

### 修复系列、时间排序和开发/测试划分

- 以候选 Issue、closing PR、merge commit 和同仓显式 cross-reference 构图；共享 closing PR、
  merge commit，或 Issue/PR 彼此显式引用时连边。连通分量就是相关修复系列。
- 分组键为分量内最小 closing PR 编号形成的 `starlette-repair-series-<number>`；成员按
  `(investigation_cutoff, case_id)` 排序。仅修改同一文件不会自动视为同一系列。
- 作者确认通过的系列按 `(最早调查时点, 分组键)` 排序。开发集是较早前缀，测试集是较新
  后缀；构建器只接受使测试后缀恰好为 30 条的系列边界。没有这样的边界就失败并继续按固定
  顺序补充待复核系列，绝不拆分系列或事后手选案例。
- 主评测集就是上述 30 条测试案例，并按 `(investigation_cutoff, case_id)` 固定顺序写入清单。
  稳定性子集在任何正式运行前固定为主评测顺序中索引 `0, 3, 6, ..., 27` 的 10 条案例。

### 人工复核公共工作流与作者确认接缝

- 从 120 条清洗候选按系列和时间稳定排序生成恰好 80 条初始复核清单；若无法形成恰好 30 条
  的系列后缀，只能按同一顺序整系列扩展清单，因此最终数量可以略高于 80。
- Agent 生成来源链接、关系依据、时间检查、文件分类、静态检查、编辑风险和建议理由，但只能
  写 `review_status: pending`。
- 项目作者在 `data/reviews.json` 明确填写 `review_status`（`accepted` 或 `rejected`）、
  `reason`、`reviewer` 和 UTC `reviewed_at`。四项缺一，或状态仍为 `pending`，均不得进入开发集、
  主评测集或稳定性子集。
- 现有 10 条案例只作为历史数据和兼容性回归输入；冻结前同样需要项目作者补齐本次确认字段，
  Agent 不把旧 `manual_review.verdict` 自动升级为作者确认。

### 案例、gold 与权限隔离

- 每条案例继续保留 Issue、调查时点、快照 commit、来源、Schema 版本、`allowed_evidence`、
  `repair_evidence` 和人工复核；增加数据集版本、修复系列、开发/测试归属、主评测/稳定性归属
  和作者复核状态。
- 每条 gold 在现有格式上明确记录 `root_cause_files`、`changed_files`、`test_files`、
  `root_cause_mechanism` 和 `evidence_locations`，顶层固定
  `read_access: evaluation_only`。根因文件相关性仍用 0/1/2 等级；证据位置只引用调查时点快照。
- closing PR、修复提交、修改文件、测试文件、修复后 URL/哈希、根因机制和全部 gold 都保持
  `evaluation_only`，与 `allowed_evidence` 物理分字段保存。`load_retrieval_input`、调查提示词、
  检索语料和工具结果只能读取调查侧白名单字段；校验器会用人工注入标记验证未来信息不可达。

### 冻结版本、清单、哈希和预注册指标

- 冻结目录只保存一个 `dataset-manifest.json` 及每案例 gold；案例本身仍在 `data/cases/`，不复制。
- 清单记录：数据集版本、构建器版本、应用 commit、候选高水位、按顺序的开发集/主评测集/
  稳定性子集、系列成员、筛选漏斗、作者确认计数、所有案例/gold/复核/筛选/配置文件的
  SHA-256、泄漏检查和系列交叉检查结果。清单不对自身取哈希。
- 预注册指标固定为：三类文件 Recall@5/10 分开报告；主阈值为 root-cause Recall@10
  `>= 0.70`；MRR 和 nDCG@10 使用 root-cause 相关性；引用有效率 `>= 0.90`；根因评分 2 的
  比例 `>= 0.65`；G4 相比 G1 的根因正确率至少增加 10 个百分点，且 root-cause Recall@10
  或引用有效率至少增加 5 个百分点。运行次数、成本和延迟属于工单 12。
- 冻结后，同一版本禁止改变案例、系列、切分、稳定性成员、gold、提示词、模型、融合、
  Reranker 或 Top-k。任何必要更改必须产生新数据集版本，旧版本及失败结果保留；工单 12 的
  冻结运行失败仍留在指标分母。

### 校验与离线重建

- `validate_case/check_cases` 增加作者确认、五类 gold、权限、数量、包含关系、系列不交叉、
  时间泄漏、文件哈希和冻结不可变字段检查。
- 构建器仅从固定 `data/github-cache/`、snapshot 元数据和 `data/reviews.json` 读取；缓存存在时
  不创建 HTTP 请求。JSON 继续使用 `indent=2, sort_keys=True` 和单个结尾换行。
- 两次输出到独立临时目录后逐文件比较字节；另人工注入未来证据、未确认复核、系列交叉、
  29/31 主案例、9/11 稳定性案例和哈希漂移，均必须返回稳定错误。
- 保留现有 10 条案例及检索 gold 格式兼容测试；不改变已冻结的工单 06 回归阈值。

### 首条红测与后续工单边界

- 首条红测加在公共 `build_cases(...)` 接缝：固定候选输入同时包含可接受项和每种稳定排除项，
  断言输出完整筛选漏斗及上述固定 `reason_code`。当前实现缺少正式漏斗阶段和代码，预期因
  输出不匹配而失败；最小绿实现只扩展现有筛选逻辑。
- 后续再严格一次一个公共行为测试，顺序沿用用户给出的 12 步红—绿建议。
- 工单 12 才实现 G1–G4 正式运行、30 条盲评计分、三次稳定性运行、聚合报告和 LLM Judge；
  工单 13 才实现 15 条安全终止集及安全指标；工单 14 才实现补丁沙箱。本工单不运行正式
  主评测，不调整检索或模型参数，不扩展前端。

### 待确认的联网执行说明

- 服务：GitHub 公共 GraphQL/REST API，以及 `Kludex/starlette` 的只读 Git 对象。
- Token：GraphQL 和足量请求需要只读 GitHub Token；Token 只从进程环境读取，绝不写入缓存、
  日志或 Git。无 Token 的 REST 限额不足以可靠完成约 120 条候选采集。
- 费用：GitHub 公共 API 和公开 Git 数据预计人民币 0 元；不调用付费模型。
- 数据范围：截至清单高水位的关闭 Issue；缓存上述必要 Issue 正文和元数据、timeline 关系、
  closing PR/commit/文件路径元数据；不取评论、PR 正文、discussion、diff、patch 或修复后文件
  内容。Git 只读目标 snapshot 的源文件用于路径/AST 检查。
- 预期产物：120 条清洗候选、完整漏斗、约 80 条 `pending` 复核材料；作者确认前不生成 30/10
  冻结清单。Git 修改限于工单 11、`cases.py`、相关测试/薄 CLI、上述 `data/` JSON 和必要文档。
- 离线复现：所有 API 规范化响应、候选清单、snapshot commit 元数据、复核和检查结果版本化；
  后续 `--offline` 构建只命中缓存，网络调用即失败，两次输出逐字节相同。
- 限流与恢复：按 GitHub 返回的 rate-limit/Retry-After 停止，不忙等；每个对象原子写入独立
  缓存文件并保留 ETag，重启时跳过已校验缓存，从首个缺失对象续传。不会并发突发请求。
- 依赖：不新增依赖；使用 Python 标准库 `urllib`、`json`、`hashlib`、`ast` 和现有 Git。

## 当前进度

2026-09-24：设计已由项目作者确认，固定高水位为 Issue #3572。完整范围得到 775 条关闭
Issue；自动漏斗得到 109 条含 `test_*.py` 行为测试的类别候选，编辑时点检查后剩 101 条，满足
100–150 条候选要求。101 条均已绑定严格早于调查时点的 `main` 快照并可构建；其中 95 条通过路径与
Python AST 静态检查，6 条因 closing PR 只修改测试、没有可分析的 Starlette 修复源文件而以
`static_analysis_failed` 排除。完整漏斗、775 份规范化 GitHub 缓存、92 个去重快照 commit
以及来源均已写入本工单待提交产物；缓存不含 Token、评论、PR 正文、diff、patch 或修复文件内容。

80 条 v2 复核材料已由项目作者确认并记录理由、复核人和 UTC 时间。最终时间闸门另排除 #1107
和 #957；正式数据包含 51 条接受案例、21 条开发集、30 条主评测集和主评测索引
`0, 3, ..., 27` 的 10 条稳定性子集。51 份 gold、修复系列、预注册指标、107 个输入哈希、
防泄漏结果和系列交叉检查均写入 `data/evaluation/`；`check_cases(data)` 通过。

采集期间使用过本机 `gh` 的只读认证；认证随后失效且匿名 REST 配额耗尽。所需 Git 对象已在
授权范围内补入忽略的 `.uv-cache`，静态分析强制 `--no-lazy-fetch`，后续复核与构建均可离线
完成。

## 答案

已冻结 `starlette-eval-v1`：固定高水位 Issue #3572，109 条自动候选，80 条项目作者复核，
51 条正式案例，按修复系列无交叉地划分为 21 条开发集和 30 条主评测集，并预先固定 10 条
稳定性子集。每条案例均有 `evaluation_only` gold；清单记录预注册指标、来源哈希、防泄漏与
系列交叉检查。最终验收：207 tests passed，Ruff passed，mypy passed，`check_cases(data)` passed，
重复冻结逐字节一致。
