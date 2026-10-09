# 作者盲评：AI 建议评分（仅供参考）

> 本文件是 AI 辅助建议，不是项目作者的评分，不得传给 score 命令，也不得复制为正式评分或仲裁结果。正式评分仍须由项目作者本人判断并填写 reviews/author-blind.json。
>
> 只针对作者材料中 report 非空的 87 份报告；失败项不评分。没有读取 review-key.json，也没有推测或记录 G1–G4 组别。请不要把本文件交给第二评审者，以免影响其独立盲评。

## 使用说明

- 根因建议使用工单评分尺度：2 = 根因机制和代码区域都正确；1 = 方向正确但机制不完整或范围偏宽；0 = 结论错误、无支持或依赖未来信息。
- 引用建议按盲评 JSON 中的顺序：先 facts，再 root_cause_hypotheses，每项按 evidence 顺序。T×N 表示 N 个位置都建议 true，F×N 表示 N 个位置都建议 false；例如 T×6, F 表示前六项建议 true、最后一项建议 false；— 表示空数组。填写时要展开为逐项布尔值。
- 引用建议仅根据报告随附的摘录、路径、行号、提交和其对应陈述作初步判断；没有联网重新确认网页可达性。尤其对标为 F 的项，请人工复核允许证据、行号和陈述是否相符。
- 我将报告与冻结评测案例的预期机制作了对照，因此建议可能影响你的独立判断。最好先自行判断，再把本文件当作复核提示；允许不同意任何建议，并在正式文件的 notes 写明理由。

## 逐报告建议

| case_id | submission_id | 根因建议 | 引用建议（原顺序） | 参考理由 |
|---|---|---:|---|---|
| starlette-issue-1355 | 2200954c027b | 1 | F | 指向 partial async endpoint 的方向对，但证据只是普通 WebSocketRoute 测试，未支持该机制。 |
| starlette-issue-1355 | 7fea5799077e | 2 | T×2 | 精确指出 inspect.isfunction/ismethod 将 partial 当作 ASGI app；代码证据直接。 |
| starlette-issue-1355 | a86668e3d1b3 | 1 | T×6 | 找到 partial 检测及 HTTP 路由旁证，但未证明 WebSocketRoute 实际走错检测分支。 |
| starlette-issue-1099 | 7aa72eaee7cf | 2 | T×6, F | 空 body 时 if body 不添加 Content-Length 与问题吻合；最后一项把 os.stat 失败说成可能缺 header，但代码路径实际抛错。 |
| starlette-issue-886 | 0d6b0514c3bd | 1 | T×3, F, F | startup handler 的 async 检测方向对但没有定位 dispatch；BackgroundTask 证据不是 startup 分发证据。 |
| starlette-issue-886 | 0db41738678f | 2 | T×6 | 精确定位 Router startup 对 callable 的 coroutine 检查和未 await 分支。 |
| starlette-issue-886 | b425b3d0663e | 2 | T×7 | 对 startup/shutdown 的调用分支解释完整，区域也吻合。 |
| starlette-issue-1433 | 49e6143ff66d | 0 | — | 没有可评分的根因陈述或引用。 |
| starlette-issue-1433 | b6ca8ea99162 | 1 | T×6 | 看到 mounted sub-app 的异常传播方向，但未定位 BaseHTTPMiddleware 中响应先返回、异常后保存的时序。 |
| starlette-issue-1471 | 712eff0efeb9 | 0 | T×3 | 只引用测试中的 AssertionError 和通用 RuntimeError 示例，没有找到生产代码中的 ASGI 校验 assert。 |
| starlette-issue-1471 | cec67200900c | 0 | T×1 | 单条引用准确描述测试 assert，但没有给出生产根因。 |
| starlette-issue-1471 | e3f3c9d58855 | 1 | T×9 | 认出要把生产 ASGI 消息校验 assert 换成 RuntimeError，但没有定位实际校验代码和文件。 |
| starlette-issue-1552 | 1bfc09322992 | 0 | T×5 | 转向 OpenAPI EndpointInfo，未解释 bound method 的 get_name() 返回类名 method。 |
| starlette-issue-1552 | 2115d34cda11 | 2 | T×4 | 精确指出 get_name() 没有处理 bound method，落点和现象吻合。 |
| starlette-issue-1552 | 452a2a3ee348 | 1 | T×9 | 找到 routing/schema 周边，但把 http_method 与 route name 混为主因；机制不够准确。 |
| starlette-issue-1578 | 108e401a0b0f | 1 | T×9 | 讨论断开码默认值，但未定位 status.py 中 1005/1006 的状态码定义/描述问题。 |
| starlette-issue-1578 | cf1e0404172e | 1 | T×6 | 接近缺省断开码行为，但报告也承认无法验证 1005 描述，未到核心代码区域。 |
| starlette-issue-746 | 0190b5349a5c | 1 | T×5 | 指出 Starlette endpoint 签名不携带类型信息，方向对；未解释 schema 生成器如何遗漏 path convertor 类型。 |
| starlette-issue-746 | 0fc8d09c633c | 2 | T×6 | 把签名缺少 path 参数类型与自动 OpenAPI schema 推断联系起来，并引用 schemas.py。 |
| starlette-issue-746 | c249c9366376 | 1 | T×4 | path_params 字典与类型元数据有关，但把运行时参数字典当成主要 schema 根因，范围偏宽。 |
| starlette-issue-495 | 9b928778b14b | 1 | T×7 | 说明 stream 消费后 body 不可重读，但没充分说明 middleware 与下游共享 receive 的冲突。 |
| starlette-issue-495 | ddce478b03c9 | 2 | T×12 | 明确指出 BaseHTTPMiddleware 中 dispatch 消费 body 后下游收到已消费的 receive 状态。 |
| starlette-issue-847 | 3ae335c4b790 | 1 | T×2 | 指到 BaseHTTPMiddleware 的流/任务组，但挂起因果链仍是未验证假设。 |
| starlette-issue-847 | 3ee383c228ba | 1 | T×3 | 看到 call_next 等待 response stream 的机制，但没有具体解释 body parsing 如何导致永远等待。 |
| starlette-issue-847 | 5ba13e6c9691 | 0 | T×5 | 主要是假设内层 app 不发送响应/stream 不关闭，未抓到请求体解析触发的实际根因。 |
| starlette-issue-1438 | 169bdf7ca7c1 | 0 | — | 没有可评分的根因陈述或引用。 |
| starlette-issue-1438 | 2d4085ea1612 | 0 | T×2 | 只说 BackgroundTasks 顺序执行及异常行为，与客户端断开导致取消无关。 |
| starlette-issue-1438 | 592d0287eb90 | 0 | T×1 | 只说明 background task 附着 response 的常规行为，没有根因。 |
| starlette-issue-1438 | 7753a5a0fe4e | 1 | T×5 | 提到客户端断开时任务可能被取消，方向对；没有定位 BaseHTTPMiddleware 的取消/任务组生命周期。 |
| starlette-issue-1878 | 21edfaa417df | 1 | T×8 | 认出 expires 类型标注有误，但只建议 datetime，遗漏 issue 提出的 str 或 str/datetime 兼容类型。 |
| starlette-issue-1878 | 7f6cb5df536a | 1 | T×10 | 指向 expires 类型标注但把秒数文档也当成错误；未精确覆盖实际 API 类型。 |
| starlette-issue-1878 | c13244479760 | 0 | T×6 | 主要结论是文档秒数描述错误，与 issue 要求修正代码类型标注不符。 |
| starlette-issue-1952 | 0458d774af55 | 1 | T×8 | 怀疑 requests→httpx 迁移影响 query 编码，方向合理但缺少具体编码差异及 testclient.py 根因。 |
| starlette-issue-1952 | 20980652347c | 1 | T×3 | 找到迁移和参数编码可能性，但没有精确机制。 |
| starlette-issue-1952 | 5ac0f4489d13 | 1 | T×5 | 同样指向 requests→httpx 的编码差异，但证据只说明迁移和普通参数测试。 |
| starlette-issue-1952 | a078801ca682 | 1 | T×8 | 找到 httpx/URL 参数处理方向，仍未解释具体错误编码路径。 |
| starlette-issue-472 | 2791a676ecb7 | 0 | — | 没有可评分的根因陈述或引用。 |
| starlette-issue-472 | 6aaf8b313a86 | 0 | — | 没有可评分的根因陈述或引用。 |
| starlette-issue-2002 | a31bdf8c675a | 0 | — | 没有可评分的根因陈述或引用。 |
| starlette-issue-2002 | cba91e991b87 | 1 | T×11, F | 找到 middleware stack 重建方向，但未说明递归初始化的精确触发；最后一条文档只建议状态约束，不能证明递归机制。 |
| starlette-issue-493 | 514b82d6cdf3 | 1 | T×7 | 描述 Request.body/stream 缓存，但没抓到 exception handler 收到重建 Request、缓存丢失这一关键点。 |
| starlette-issue-493 | a515a58eaf7e | 1 | T×14 | 详细解释单个 Request 的 body 缓存；仍遗漏异常处理器中的 Request 重建。 |
| starlette-issue-493 | b9786a252884 | 0 | — | 没有可评分的根因陈述或引用。 |
| starlette-issue-2134 | b0e225a7ab5f | 2 | T×6 | 精确指出自定义 loader 存在时 directory 仍是必填参数，证据落在 templating.py。 |
| starlette-issue-2134 | e1ff3931c930 | 2 | T×3 | 同样准确说明构造器签名和默认 FileSystemLoader 关系。 |
| starlette-issue-2093 | 15546507380a | 1 | T×4 | 抓到 BaseHTTPMiddleware/client disconnect/BackgroundTasks 的关联，但主要依据 release note，未给出代码级生命周期。 |
| starlette-issue-2093 | ab510823ec28 | 1 | T×5 | 指向断连时任务取消及流响应，但没有精确到同连接问题的机制。 |
| starlette-issue-2093 | e821a363597d | 1 | T×3 | 识别出取消主题，代码证据仅说明任务顺序执行。 |
| starlette-issue-2093 | f922e1eec23e | 1 | T×6 | 找到 BaseHTTPMiddleware 生命周期方向，但解释仍是未验证猜测。 |
| starlette-issue-2227 | 20fc9e225cac | 1 | T×6 | 识别 Python <3.11 与 exceptiongroup 缺失的关联，但把运行时导入误指向测试模块。 |
| starlette-issue-2227 | 71d6370646f0 | 1 | T×2 | exceptiongroup 是 Python <3.11 的缺失运行时依赖这一方向正确，但未定位 BaseHTTPMiddleware 导入路径。 |
| starlette-issue-2227 | 818f23031aec | 1 | T×5 | 发现旧 Python 下 exceptiongroup 依赖，但把根因限定在测试导入而非库导入。 |
| starlette-issue-2227 | e71c79537018 | 1 | T×3 | 识别旧 Python/exceptiongroup 关联，但未定位 Starlette 运行时的无条件 import。 |
| starlette-issue-2298 | 7e14a91d454c | 0 | T×3 | 只描述 ETag 与 304 的既有行为，没有判断 strong/weak ETag。 |
| starlette-issue-2298 | 8178cedabe2d | 0 | T×1 | 只复述匹配 ETag 返回 304 的测试，没有提出根因。 |
| starlette-issue-2298 | 959003bc2b89 | 1 | T×3 | 注意到测试未区分 ETag 强弱，方向对但没有给出 StaticFiles/Response 的生成或比较机制。 |
| starlette-issue-2298 | c7fc2eabe000 | 1 | T×2 | 提出 weak/strong ETag 比较问题，但没有定位生成/匹配实现，且两种假设互相冲突。 |
| starlette-issue-1336 | 8c9ba1a99cd8 | 1 | T×5 | 找到 Mount 修改 root_path/path 的方向，但把 ASGI spec 偏差与 WSGI/url_for 混在一起，未覆盖完整根因区域。 |
| starlette-issue-1336 | c48e45dafe8b | 1 | T×2 | 指向 Mount 的 scope 变换，但没有完整说明 ASGI root_path/path 语义差异。 |
| starlette-issue-1336 | e53db50f1ae2 | 1 | T×6 | 找到 routing/staticfiles 相关路径操作，但部分是假设，未完整串起请求路径处理。 |
| starlette-issue-2306 | 09a96d25a673 | 2 | T×4 | 准确指出 TestClient 自己 join base_url 后再委托 httpx 造成语义差异。 |
| starlette-issue-2306 | 5f6a31adb04b | 2 | T×7 | 精确定位 TestClient.request 的 URL join 与 httpx base_url 合并差异。 |
| starlette-issue-2306 | f0fcd8e67eb6 | 2 | T×12 | 同时找到 request 覆盖与各 HTTP verb wrapper 路径不一致，机制和代码范围充分。 |
| starlette-issue-947 | 3eee208f1dbe | 1 | T×4 | 找到 close 只发 disconnect、receive 可能阻塞，但没有证明线程/portal 的最终退出路径。 |
| starlette-issue-947 | 7fdb4483164b | 2 | T×6 | 解释 close 仅投递 disconnect，任务/线程要等 app 返回或退出栈清理。 |
| starlette-issue-947 | 9f5865a983e3 | 2 | T×10 | 精确关联 portal task、close、app 返回和 context manager 清理。 |
| starlette-issue-2516 | 022e1794086a | 1 | T×8 | 定位 No response returned 路径，但根因仍是假设，没有区分具体触发场景。 |
| starlette-issue-2516 | 04a167a37804 | 1 | T×5 | 解释 EndOfStream/app_exc 分支，但未说明为何该 case 会走到无 response 分支。 |
| starlette-issue-2516 | 7d083d0d552e | 1 | T×6 | 代码路径正确，但把可能触发条件泛化为无 response/cancel，缺少具体根因。 |
| starlette-issue-2516 | bd9315697182 | 1 | T×6 | 定位流关闭产生 RuntimeError，但提出的触发机制不够具体。 |
| starlette-issue-2646 | 148e932b9893 | 2 | T×11 | 直接围绕 directory symlink、realpath 和 commonpath 检查解释失败。 |
| starlette-issue-2646 | e1290d0ec89f | 2 | T×7 | 定位 follow_symlink=False 下的 realpath 与目录 symlink 行为，代码区域正确。 |
| starlette-issue-2692 | 24d9d7365464 | 2 | T×2, F | 精确找到 TestClient 把 httpx raw_path 直接写入 ASGI scope；最后一条引用没有证明 ASGI 对 raw_path 的定义差异。 |
| starlette-issue-2692 | 3ffe0e344eed | 1 | T×4 | 指向 raw_path 直传和编码边界，但对 ASGI 与 httpx 的具体差异仍不精确。 |
| starlette-issue-2692 | a39a14f22b12 | 2 | T×18, F×3, T×2 | 主体机制准确；根因假设中关于 ASGI/httpx 语义不一致的三条引用只证明使用 raw_path，没有证明规范差异。 |
| starlette-issue-2692 | a761eae35de0 | 2 | T×3, F | 代码直传 raw_path 及编码路径测试吻合；最后一条引用没有直接支持 ASGI 规范差异。 |
| starlette-issue-2766 | 1545765383b2 | 2 | T×2 | 精确指出断开后再次 receive 仍抛 RuntimeError，而 issue 要求 WebSocketDisconnect。 |
| starlette-issue-2766 | f2b43e14d9ca | 2 | T×8 | receive 的根因准确；另外扩展到 send/其他状态的范围略宽，但不改变核心结论。 |
| starlette-issue-2766 | f96b2fd6f97d | 2 | T×5 | 同样准确定位 post-disconnect 状态异常类型。 |
| starlette-issue-2783 | 4105c1a60d12 | 0 | T×4 | 只找到启动异常测试，没有找到 _choose_redirect_arg 分支。 |
| starlette-issue-2783 | 43aca86f8e40 | 2 | T×3 | 正确证明 elif 的 follow_redirects 条件被前面的 if 排除，解释分支不可达。 |
| starlette-issue-2783 | b895f79891a8 | 2 | T×4 | 精确指出条件逻辑矛盾及未覆盖分支。 |
| starlette-issue-2783 | f926a2770b82 | 2 | T×12, F | unreachable 分支机制准确；最后一条 pragma: no cover 只能说明未覆盖，不能证明开发者已知它不可达。 |
| starlette-issue-2295 | 24569f70cdde | 2 | T×2 | 指向 scope.client 的固定 testclient，与 ASGI client host 必须是 IP 格式的 issue 相符。 |
| starlette-issue-2295 | 258894eba661 | 2 | T×8 | 指出固定 client host 与请求 Host/base_url 无关，命中根因方向。 |
| starlette-issue-2295 | 8904c4282aeb | 2 | T×10 | 同时查看固定 client tuple 与 Host 解析，根因区域正确。 |
| starlette-issue-2625 | 8a607ccae1a0 | 0 | T×9 | 讨论 ExceptionMiddleware/handler 如何处理异常，但 issue 的问题是 BaseHTTPMiddleware 对 Exception 与 BaseException 的捕获差异；根因结论偏离。 |

## 评分完成边界

这是项目作者评分的参考材料，不是另一名评审者的建议，也不是仲裁记录。作者应独立核对报告原文、citation excerpt 与冻结案例后再填写正式盲评；第二评审者应只收到自己的 second-reviewer-blind.json 和原始评审说明。任何人工分歧与仲裁仍须由真实评审者/仲裁者作出。
