# 第二评审子集：AI 评分建议（仅供项目作者参考）

> 本文件不是第二评审者的评分，也不构成独立人工盲评。它只是从 `author-score-suggestions.md` 中筛选出的 10 个稳定性案例建议；建议与作者参考稿来自同一分析，因此不独立。没有读取 `review-key.json`，也没有推测或记录 G1–G4 组别。
>
> 不要把本文件交给第二评审者，也不要把这些建议当作正式评分写入 `second-reviewer-blind.json`。正式评审必须由第二评审者独立完成；失败项保持未评分。

## 阅读方式

- 根因建议：2 = 根因机制和代码区域都正确；1 = 方向正确但机制不完整或范围偏宽；0 = 结论错误、无支持或依赖未来信息。
- 引用建议按盲评 JSON 顺序：先 `facts`，再 `root_cause_hypotheses`，每项按 `evidence` 顺序。`T×N` / `F×N` 分别表示 N 项均建议 true / false；录入 JSON 时要展开为逐项布尔值；`—` 表示空数组。
- 引用建议仅为已有建议稿中的初步判断，未联网确认网页可达性；人工评分仍需核对允许证据、位置和对应陈述。

## 逐提交建议

| case_id | submission_id | 根因建议 | 引用建议（原顺序） | 参考理由 |
|---|---|---:|---|---|
| starlette-issue-1355 | 2200954c027b | 1 | F | 指向 partial async endpoint 的方向对，但证据只是普通 WebSocketRoute 测试，未支持该机制。 |
| starlette-issue-1355 | 7fea5799077e | 2 | T×2 | 精确指出 inspect.isfunction/ismethod 将 partial 当作 ASGI app；代码证据直接。 |
| starlette-issue-1355 | a86668e3d1b3 | 1 | T×6 | 找到 partial 检测及 HTTP 路由旁证，但未证明 WebSocketRoute 实际走错检测分支。 |
| starlette-issue-1433 | 49e6143ff66d | 0 | — | 没有可评分的根因陈述或引用。 |
| starlette-issue-1433 | b6ca8ea99162 | 1 | T×6 | 看到 mounted sub-app 的异常传播方向，但未定位 BaseHTTPMiddleware 中响应先返回、异常后保存的时序。 |
| starlette-issue-1578 | 108e401a0b0f | 1 | T×9 | 讨论断开码默认值，但未定位 status.py 中 1005/1006 的状态码定义/描述问题。 |
| starlette-issue-1578 | cf1e0404172e | 1 | T×6 | 接近缺省断开码行为，但报告也承认无法验证 1005 描述，未到核心代码区域。 |
| starlette-issue-847 | 3ae335c4b790 | 1 | T×2 | 指到 BaseHTTPMiddleware 的流/任务组，但挂起因果链仍是未验证假设。 |
| starlette-issue-847 | 3ee383c228ba | 1 | T×3 | 看到 call_next 等待 response stream 的机制，但没有具体解释 body parsing 如何导致永远等待。 |
| starlette-issue-847 | 5ba13e6c9691 | 0 | T×5 | 主要是假设内层 app 不发送响应/stream 不关闭，未抓到请求体解析触发的实际根因。 |
| starlette-issue-1952 | 0458d774af55 | 1 | T×8 | 怀疑 requests→httpx 迁移影响 query 编码，方向合理但缺少具体编码差异及 testclient.py 根因。 |
| starlette-issue-1952 | 20980652347c | 1 | T×3 | 找到迁移和参数编码可能性，但没有精确机制。 |
| starlette-issue-1952 | 5ac0f4489d13 | 1 | T×5 | 同样指向 requests→httpx 的编码差异，但证据只说明迁移和普通参数测试。 |
| starlette-issue-1952 | a078801ca682 | 1 | T×8 | 找到 httpx/URL 参数处理方向，仍未解释具体错误编码路径。 |
| starlette-issue-493 | 514b82d6cdf3 | 1 | T×7 | 描述 Request.body/stream 缓存，但没抓到 exception handler 收到重建 Request、缓存丢失这一关键点。 |
| starlette-issue-493 | a515a58eaf7e | 1 | T×14 | 详细解释单个 Request 的 body 缓存；仍遗漏异常处理器中的 Request 重建。 |
| starlette-issue-493 | b9786a252884 | 0 | — | 没有可评分的根因陈述或引用。 |
| starlette-issue-2227 | 20fc9e225cac | 1 | T×6 | 识别 Python <3.11 与 exceptiongroup 缺失的关联，但把运行时导入误指向测试模块。 |
| starlette-issue-2227 | 71d6370646f0 | 1 | T×2 | exceptiongroup 是 Python <3.11 的缺失运行时依赖这一方向正确，但未定位 BaseHTTPMiddleware 导入路径。 |
| starlette-issue-2227 | 818f23031aec | 1 | T×5 | 发现旧 Python 下 exceptiongroup 依赖，但把根因限定在测试导入而非库导入。 |
| starlette-issue-2227 | e71c79537018 | 1 | T×3 | 识别旧 Python/exceptiongroup 关联，但未定位 Starlette 运行时的无条件 import。 |
| starlette-issue-2306 | 09a96d25a673 | 2 | T×4 | 准确指出 TestClient 自己 join base_url 后再委托 httpx 造成语义差异。 |
| starlette-issue-2306 | 5f6a31adb04b | 2 | T×7 | 精确定位 TestClient.request 的 URL join 与 httpx base_url 合并差异。 |
| starlette-issue-2306 | f0fcd8e67eb6 | 2 | T×12 | 同时找到 request 覆盖与各 HTTP verb wrapper 路径不一致，机制和代码范围充分。 |
| starlette-issue-2646 | 148e932b9893 | 2 | T×11 | 直接围绕 directory symlink、realpath 和 commonpath 检查解释失败。 |
| starlette-issue-2646 | e1290d0ec89f | 2 | T×7 | 定位 follow_symlink=False 下的 realpath 与目录 symlink 行为，代码区域正确。 |
| starlette-issue-2783 | 4105c1a60d12 | 0 | T×4 | 只找到启动异常测试，没有找到 _choose_redirect_arg 分支。 |
| starlette-issue-2783 | 43aca86f8e40 | 2 | T×3 | 正确证明 elif 的 follow_redirects 条件被前面的 if 排除，解释分支不可达。 |
| starlette-issue-2783 | b895f79891a8 | 2 | T×4 | 精确指出条件逻辑矛盾及未覆盖分支。 |
| starlette-issue-2783 | f926a2770b82 | 2 | T×12, F | unreachable 分支机制准确；最后一条 pragma: no cover 只能说明未覆盖，不能证明开发者已知它不可达。 |
