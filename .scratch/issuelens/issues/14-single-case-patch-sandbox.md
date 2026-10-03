# 14: 在一个案例中贯通补丁验证沙箱

**What to build:** 让 Agent 在一个预先验证可复现的 Starlette 案例中，将候选补丁写入临时工作树，并在断网、限时、限资源容器内执行白名单目标测试和相关回归测试。

**Blocked by:** 09: 实现受控预算与四类调查终态

**Status:** resolved

- [x] 修复前快照能够稳定运行预验证失败测试。
- [x] 候选补丁只修改临时工作树，数据源仓库保持不变。
- [x] 测试命令只能来自案例白名单，模型不能执行任意 Shell 命令。
- [x] 容器默认断网且无法读取宿主机密钥或工作区外路径。
- [x] CPU、内存、输出和五分钟超时限制均有自动化验证。
- [x] 结果分别记录补丁应用、目标测试和相关回归测试状态。
- [x] Langfuse 不可用时仍能保存完整本地验证记录。

## 答案

以固定的 Starlette issue 1552、修复前提交 `e086fc2da361767b532cf690e5203619bbae98aa` 贯通 `run_investigation` 的补丁验证公共接缝。案例清单仅允许 `route-name-patch`、目标测试和路由回归测试；修复前目标测试在两个独立容器内得到相同的预期 `AssertionError`。补丁仅应用到当前 managed worktree 缓存下的临时本地克隆，结束后清理；源仓库 HEAD、状态和索引指纹前后一致。

真实 Docker 沙箱检查断网、单一临时工作树挂载、只读根文件系统、无宿主机密钥路径，以及 1 CPU、512 MiB 内存及相同的 swap 上限、256 PIDs、1 MiB 输出和 300 秒总期限。输出超限和超时探针均得到独立状态。补丁应用、目标测试、回归测试分别为 `applied`、`passed`、`passed`；结果中的四次执行、工具事件、统计和源仓库证明保存于 `data/patch-validation/starlette-issue-1552/run.json`，生成时 `LANGFUSE_ENABLED=false`。

验收：`uv run --frozen --offline python scripts/check_backend.py`：217 passed，Ruff 与 mypy 通过；`uv run --frozen --offline pytest backend/tests/patch_sandbox_integration.py -q --basetemp .retrieval-cache/pytest-temp`：5 passed；新文件单独 Ruff 与 mypy 通过；前端 `pnpm check` 脚本内容（Vitest 3 passed、ESLint、TypeScript、Vite build）通过；`docker compose -p issuelens-issue14 config --quiet`、`up --build --detach --wait` 通过，三服务健康，首页和 `/api/health` 返回成功，随后 `down`（保留数据卷）。Compose 首次构建因 `files.pythonhosted.org` 下载读取超时失败，原命令不变重试后通过；前端包装器首次安装依赖因 esbuild 构建脚本批准闸门失败，改由已安装 pnpm 直接执行同一 `check` 脚本通过，未修改前端配置。
