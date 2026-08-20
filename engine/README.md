# ragtest 引擎（Milestone 0）

RAG / 知识库系统自动化测试引擎。CLI 优先、CI 裸跑、Adapter 可插拔。
设计依据：`../docs/architecture-review.md` v0.3。

## 环境要求

- [uv](https://docs.astral.sh/uv/)（自动管理 Python ≥3.11 与依赖）

## M0 内容

- `ragtest/config.py`：环境变量配置（前缀 `RAGTEST_`）
- `ragtest/adapters/base.py`：四 Port 契约 + 归一化类型 + 分级超时约定
- `ragtest/adapters/arag/`：AragAdapter（login / 建库 / 上传 / 轮询就绪 / 检索 / 删除 / 清理）
- `ragtest/runner/polling.py`：指数 backoff 轮询（禁止固定 sleep）
- `ragtest/artifacts.py`：artifacts 目录契约（status.json 原子写 + 心跳）
- `scripts/smoke.py`：对真实 arag 的端到端冒烟
- `scripts/spike_chat.py`：对 xuanjian-agent 同步 chat 的响应 fixture 采集（M4 备料）

## 运行

```bash
cd engine
uv sync                          # 安装依赖（首次会拉取托管 Python）

# 单元测试（不需要被测系统）
uv run pytest

# 冒烟（需要本地 arag：auth :9011 / app :9013）
export RAGTEST_ARAG_ADMIN_PASSWORD=<ADMIN_PASSWORD>
uv run python scripts/smoke.py

# agent chat fixture 采集（需要 xuanjian-agent :8788）
export RAGTEST_AGENT_UID=<arag user uuid>
uv run python scripts/spike_chat.py
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `RAGTEST_ARAG_BASE_URL` | `http://127.0.0.1:9013` | arag-app 地址（或 nginx :9360） |
| `RAGTEST_ARAG_AUTH_URL` | `http://127.0.0.1:9011` | arag-auth 地址 |
| `RAGTEST_ARAG_ADMIN_PASSWORD` | — | admin 密码（对应被测 `ADMIN_PASSWORD`） |
| `RAGTEST_ARAG_OPS_PASSWORD` | — | ops@internal 密码（对应 `OPS_PASSWORD`） |
| `RAGTEST_AGENT_BASE_URL` | `http://127.0.0.1:8788` | xuanjian-agent 地址 |
| `RAGTEST_AGENT_UID` | — | spike 用的用户 UUID（= arag user_id） |
| `RAGTEST_ARTIFACTS_DIR` | `artifacts` | artifacts 输出根目录 |
