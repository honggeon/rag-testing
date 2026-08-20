# rag-testing

可扩展、可回归、可评测、可接入 CI 的 RAG / 知识库系统自动化测试平台。

- **引擎**：`engine/`（Python 包 `ragtest`，CLI 优先，CI 裸跑，exit code 语义）
- **DSH 插件**：`plugin/`（M5，DeepSeek Harness 侧边栏菜单 + 全屏页薄壳）
- **测试资产**：`suites/`（datasets + golden sets，YAML 配置驱动）
- **设计文档**：[`docs/architecture-review.md`](docs/architecture-review.md)（v0.3，评审通过版）

## 当前状态

设计冻结，待进入 Milestone 0（引擎骨架 + AragAdapter 冒烟）。里程碑计划见设计文档 §15。

## 快速链接

| 文档 | 说明 |
|---|---|
| [docs/architecture-review.md](docs/architecture-review.md) | 架构评审主文档（决策记录 / Adapter 契约 / Schema / 里程碑） |
| [docs/architecture-review-audit.md](docs/architecture-review-audit.md) | 架构评审意见（P0/P1/P2，已全部合入 v0.3） |
| [docs/dsh-architecture-analysis.md](docs/dsh-architecture-analysis.md) | DeepSeek Harness 插件机制分析 |
| [docs/xuanjian-agent-analysis.md](docs/xuanjian-agent-analysis.md) | 被测 Agent 问答链路分析 |
