# 问题跟踪器：本地 Markdown

本仓库的问题和规格说明以 Markdown 文件形式保存在 `.scratch/` 中。

## 约定

- 每项功能使用一个目录：`.scratch/<feature-slug>/`
- 规格说明文件为 `.scratch/<feature-slug>/spec.md`
- 实现任务按工单分别保存为 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`
- 工单从 `01` 开始编号，不使用单个合并工单文件
- 分诊状态记录在每个问题文件顶部附近的 `Status:` 行中，角色字符串见 `triage-labels.md`
- 评论和对话历史追加在文件底部的 `## 评论` 标题下

## 当技能要求“发布到问题跟踪器”时

在 `.scratch/<feature-slug>/` 下创建新文件；目录不存在时一并创建。

## 当技能要求“获取相关工单”时

读取所引用路径中的文件。用户通常会直接提供文件路径或问题编号。

## 路径探索操作

供 `/wayfinder` 使用。一个“地图”文件对应每张工单的一个“子文件”。

- **地图**：`.scratch/<effort>/map.md`，正文记录备注、截至目前的决定以及未知事项。
- **子工单**：`.scratch/<effort>/issues/NN-<slug>.md`，从 `01` 开始编号，正文包含待解决的问题。`Type:` 行记录工单类型（`research`、`prototype`、`grilling` 或 `task`）；`Status:` 行记录 `claimed` 或 `resolved`。
- **阻塞关系**：在文件顶部附近使用 `Blocked by: NN, NN`。列出的全部工单均为 `resolved` 后，该工单才解除阻塞。
- **待办前沿**：扫描 `.scratch/<effort>/issues/`，查找尚未解决、未受阻且无人认领的文件；编号最小者优先。
- **认领**：开始工作前将 `Status:` 设置为 `claimed` 并保存。
- **解决**：在 `## 答案` 标题下追加答案，将 `Status:` 设置为 `resolved`，然后在 `map.md` 的“截至目前的决定”中追加上下文指针（摘要及链接）。
