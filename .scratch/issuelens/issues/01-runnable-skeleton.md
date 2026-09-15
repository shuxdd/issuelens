# 01: 建立可运行的 IssueLens 纵向骨架

**What to build:** 建立一个从浏览器到 API 再到数据库的最小可运行系统。开发者使用一条 Docker Compose 命令即可启动服务，用户能打开英文页面并看到后端健康状态，持续集成能验证后端测试、前端构建和容器启动。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 一条 Docker Compose 命令能够启动前端、后端和 PostgreSQL。
- [ ] 后端健康接口返回 HTTP 200 和机器可读服务状态。
- [ ] 英文首页能调用健康接口并显示成功或失败状态。
- [ ] 后端测试、类型检查和 Ruff 检查可通过单一命令运行。
- [ ] 前端 lint、类型检查和生产构建可通过单一命令运行。
- [ ] GitHub Actions 能在无 API 密钥条件下完成上述检查和容器冒烟测试。
