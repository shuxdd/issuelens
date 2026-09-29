# 13: 用安全终止集验证 Agent 边界

**What to build:** 让系统在证据不足、根因位于其他仓库或输入包含恶意指令时拒绝武断结论和越权操作，并以规范终态结束。

**Blocked by:** 09: 实现受控预算与四类调查终态；11: 扩充并冻结正式评测数据集

**Status:** resolved

- [x] 安全终止集包含 15 条案例：5 条信息不足、5 条跨仓根因、5 条提示注入或危险请求。
- [x] 每条案例预先标注期望终态和禁止行为。
- [x] 报告正确终态率、错误根因断言率和越权工具调用次数。
- [x] 仓库文本中的指令不能改变系统指令、工具权限或预算。
- [x] GitHub 写操作、白名单外命令、密钥读取和工作目录外访问成功次数为 0。
- [x] 安全终止集与主准确率测试集分开统计。
- [x] 每个拒绝结果包含可理解的停止原因和已收集证据。

## 答案

- 新增公共接缝 `run_safe_termination_suite(data_directory)`，通过现有
  `run_investigation` 和 `ToolGateway` 离线运行安全终止案例，并保留统一的
  `report`、`outcome`、`events` 和 `statistics`。
- 新增独立版本化数据集 `safe-termination-v1`：5 条信息不足、5 条跨仓根因、
  5 条提示注入或危险请求；每条均记录期望终态、根因断言许可和禁止行为。
- 离线结果：正确终态率 `1.0`，错误根因断言率 `0.0`，被拒绝的越权工具调用
  `5` 次；`github_write`、`unapproved_command`、`secret_read`、
  `outside_workspace`、`budget_override` 成功次数均为 `0`。
- `LANGFUSE_ENABLED=false` 时完整运行 15 条案例，无网络或模型调用。
- 验收通过：`uv run python scripts/check_backend.py`（212 tests、Ruff、mypy）；
  前端 Vitest（3 tests）、ESLint、TypeScript、Vite build；Docker Compose 全新构建，
  PostgreSQL、后端和前端健康，首页及 `/api/health` 冒烟通过，随后已停止服务并保留数据卷。
