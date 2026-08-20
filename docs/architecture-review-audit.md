# RAG Testing 架构文档评审意见

> 版本：v1.0
> 日期：2026-08-20
> 状态：**有条件通过**（先补 P0，再进入 Milestone 0）
> 评审对象：[`docs/architecture-review.md`](./architecture-review.md) v0.2（2026-08-19，设计稿，未编码）
> 抽查范围：arag v0.6.5（`zhiju-rag-knowledge`）、xuanjian-agent（Starlette + hermes-agent）
> 不在范围内：集成测试、尚未存在的引擎/插件代码

---

## 0. 结论

**有条件通过。** Python 引擎 + DSH 薄壳、双目标（arag 检索 / agent 问答）、诚实 Trace、fingerprint 不可比标记，这四条建议直接采纳。卡住编码的是身份绑定、chat 超时、kb_id 注入、Protocol 拆分、生命周期枚举，以及把同步 chat 的 `messages` 当成归因主路径。

| 维度 | 数量 |
|---|---|
| 评审结论 | 有条件通过 |
| P0 必须先改 | 6 |
| P1 建议 M1 前改 | 7 |
| P2 文档债 | 4 |

### 建议保留的架构判断

- 引擎独立成 `ragtest` Python 包、CI 不绑 DSH。否决「纯 TS 内嵌插件」的理由成立。
- 被测对象拆成目标 A（arag HTTP 黑盒检索）和目标 B（xuanjian-agent `POST /api/chat`），符合源码事实：arag 没有 Chat API，生成在 agent + xj-kbase。
- MVP 先打穿检索、M4 再上 E2E，范围切分合理。
- `permission_leak` 放进 MVP：arag ACL 完整，实现便宜、回归价值高。

### Milestone 0 准入

补完下面五件事再编码：

1. 关闭 Open Question 1，锁定 Python 引擎。
2. 写下 `IdentityBinding` 与分级超时。
3. 枚举生命周期状态机（含 PARTIAL / ERROR / TIMEOUT / CANCELLED 与 CLEANUP 的 finally）。
4. Protocol 拆成 Port；`chat` 默认注入 `kb_id`。
5. 归因主路径改为解析 `POST /api/chat` 的 `session.messages`。

---

## 1. 发现项总表

| ID | 级别 | 章节 | 问题 | 建议 |
|---|---|---|---|---|
| P0-1 | P0 | §6.2 / §6.3 / identities | 双 Adapter 身份绑定未定义 | 共享 `Identity`：arag `user_id` → agent `X-USER-ID` |
| P0-2 | P0 | §6.1 横切约定 | HTTP 30s 统一超时会打死 chat | 按操作分级超时，禁止全局一把超时 |
| P0-3 | P0 | §2B.2 / §7 | kb_id 路由被当成评测噪声 | adapter 注入确定性 `kb_id` 指令 |
| P0-4 | P0 | §6.1 vs §6.3 | Protocol 是上帝接口，§6.3 方法不在契约里 | 拆成 Provisioning / Retrieval / Chat / Trace Port |
| P0-5 | P0 | §5 / §12 | 14 态生命周期从未列出 | 补状态表与合法迁移 |
| P0-6 | P0 | §2B.6 / Q6 / Risk 12 | 归因默认路径判错 | 从 `chat_sync` 响应解析 tool_calls，PG 改为可选 |
| P1-1 | P1 | §7 / §11 | E2E fingerprint 不够 | 按 target 拆 fingerprint；缺项则 incomparable |
| P1-2 | P1 | §6.3 / Risk 5 | 双目标 cleanup / 隔离不完整 | `RunLease` + 资源清单逆序清理 |
| P1-3 | P1 | Risk 11 / Q10 | 已知缺陷不该打红 Quality Gate | 独立 defect suite（`expected_fail`） |
| P1-4 | P1 | §4.3 / §9 / §13 / Risk 6 | 里程碑与范围互相打架 | 统一 MVP / M4 / M5 / later 矩阵 |
| P1-5 | P1 | §4.2 / M5 | spawn / artifacts 契约未写 | M0 定 `status.json` + 取消信号 |
| P1-6 | P1 | §6.1 / §2B.4 | `ChatResult.citations` 与真实输出不符 | citations 由 evaluator 从文本抽出 |
| P1-7 | P1 | §2.4 / Risk 2 | DocumentStatus「必须用中文」说得过死 | JSON 序列化中文，解析兼容双语 |
| P2-1 | P2 | §4.3 | Python >=3.13 对引擎不是刚需 | `requires-python >=3.11` |
| P2-2 | P2 | §8 | run.json 用 blake3，标准库没有 | checksum 改 sha256 |
| P2-3 | P2 | §2.9 / §2B.7 / §12 | 冲突编号跳号；「需求十二」无出处 | 补 #8 或重编号；附录贴 14 态或删引用 |
| P2-4 | P2 | §2.2 / §6.2 | admin / ops 密码环境变量被写成一个 | 拆成两个 `RAGTEST_ARAG_*_PASSWORD` |

---

## 2. P0（挡住 Milestone 0）

### P0-1 双 Adapter 身份绑定未定义

**章节**：§6.2 / §6.3 / identities

AragAdapter 经 `register` 得到 UUID `user_id`；知识库工具用 agent 会话 uid 签发 JWT（`sub=uid`）。XuanjianAgentAdapter 用 `X-USER-ID` 注入身份。suite 的 `identities` 只有 role，没有把 arag `user_id` 传给 agent。uid 对不上时检索会 401/空结果，E2E 归因会误判成 routing/retrieval 失败。

**改法**：把 `Identity` 做成跨 adapter 共享对象：provisioning 创建用户后写回 `user_id`，`target.login` 必须用同一个 UUID 作为 `X-USER-ID`。在 schema 里显式写：

```yaml
identity_binding:
  arag_user_id: "<uuid>"   # provisioning 回写
  agent_uid: "<same uuid>" # target.login → X-USER-ID
```

### P0-2 HTTP 30s 统一超时会打死 chat

**章节**：§6.1 横切约定

文档写所有 HTTP 调用默认 30s。`POST /api/chat` 是同步等到 `done`，真实问答常超过 30s，流超时本身是 300s。按现状实现，M4 的同步 chat 会大面积 timeout。

**改法**：按操作分级超时：

| 操作 | 默认超时 | 覆盖方式 |
|---|---|---|
| search / auth / KB CRUD | 30s | adapter config |
| ingest poll | 跟随 `wait_until_ready`（默认 300s） | suite / case |
| `chat_sync` / SSE | 300s | `case.timeout_s` |

禁止全局一把超时。

### P0-3 kb_id 路由被当成评测噪声，而不是 Adapter 职责

**章节**：§2B.2 / §7 `message_template`

kb_id 无 HTTP 字段，设计指望 `@{kb_name}` 让 LLM 自路由。这会让 `retrieval_attribution` 被 `routing_failure` 淹没，`golden_facts` 阈值无法标定。

**改法**：`XuanjianAgentAdapter.chat()` 必须注入确定性指令，例如 `MUST call knowledge_retrieve with kb_id=<uuid>`。`message_template` 只作可选对照实验，不能当默认路径。

### P0-4 Protocol 是上帝接口，§6.3 方法不在契约里

**章节**：§6.1 vs §6.3

`create_session` / `chat_stream` / `collect_agent_trace` 只写在映射表。`chat(session, kb, question)` 假定有 `KBHandle`，但 xuanjian 无 `KB_PROVISIONING`，kb_id 也不走 HTTP。双 adapter 生命周期会和这个胖 Protocol 打架。

**改法**：拆成可组合能力：

- `ProvisioningPort`（建库 / 上传 / 授权 / 删除）
- `RetrievalPort`（检索）
- `ChatPort`（会话 + 问答）
- `TracePort`（tool_calls 采集）

`chat` 入参改为 `ChatRequest`（`skill`、`kb_id` 注入、`timeout`、`stream`）。不支持的 port 不出现在 `capabilities()`。

### P0-5 14 态生命周期从未列出

**章节**：§5 / §12

§12 写「完全采用需求十二的 14 态」，本文没有状态枚举和合法迁移。§5 数据流大约 13 步，和 14 对不上。M1 runner 无法按文档实现。

**改法**：补一张状态表：名字、进入条件、失败转移、CLEANUP 的 finally 覆盖。`PARTIAL` / `ERROR` / `TIMEOUT` / `CANCELLED` 都要画进去。

### P0-6 归因默认路径判错了：同步 chat 已带完整 messages

**章节**：§2B.6 / Open Question 6 / Risk 12

已核对：`POST /api/chat` 返回 `done` 载荷，含 `session.messages`，字段包括 `tool_calls`、`content`、`tool_name`。PG 只读不是归因的前提，而是落库后的增强。

**改法**：默认从 `chat_sync` 响应解析 `agent_tool_calls`。SSE 快照作流式用例兜底。PG collector 标成可选，从 M4 关键路径拿掉。据此改写 Q6 和 Risk 12。

---

## 3. P1（建议 Milestone 1 前改）

### P1-1 E2E fingerprint 不够，基线会假可比

**章节**：§7 / §11

检索指纹有 embedding/rerank，但缺 `MAX_CHUNK_SIZE`、query-expand。生成指纹缺 LLM `model`/`base_url`、skill 名与 checksum、agent 版本、`ZJ_KNOWLEDGE_BASE_URL`、`reasoning_effort`。

**改法**：按 target 拆 fingerprint schema。E2E 至少锁 model、skill checksum、agent 版本、知识库 URL。缺项则 `incomparable`。

### P1-2 双目标 cleanup / 隔离未设计完整

**章节**：§6.3 cleanup / Risk 5

一次 E2E run 会留下 arag KB/用户、agent session、`~/.xj_ws`、PG messages。两个 adapter 各自 cleanup，并发 run 和失败中断没有 IsolationContext。

**改法**：引入 `RunLease`（`run_id` 前缀 + 资源清单）。CLEANUP 按清单逆序：agent session → arag docs/KB/users。启动时清上一 run 残留。工作区路径写入报告，不默默 `rm -rf`。

### P1-3 已知缺陷不该直接打红 Quality Gate

**章节**：Risk 11 / Q10

JWT 过期校验被注释、密钥硬编码、`vector_similarity_weight` 被忽略，都是产品缺陷。若做成会失败的 security case，RAG 质量门会被已知 bug 长期挡住。

**改法**：单独 defect suite，标记 `expected_fail` / `known_issue`。质量门只看 retrieval/generation；缺陷套件出证据，不挡 MVP CI。

### P1-4 里程碑与范围互相打架

**章节**：§4.3 / §9 / §13 / Risk 6

engine 布局把 `generation/` 写成 post-MVP 占位，§9 又把 `golden_facts` 标 M4。Risk 6 写 UI 在 M4 再评估，§15 里 DSH 插件是 M5。YAML 示例用了 `e2e_latency`，§9 却标它为 M4/M5 空心。

**改法**：统一一张范围矩阵：MVP / M4 / M5 / later。示例 YAML 只出现该里程碑已承诺的 evaluator。

### P1-5 spawn / artifacts 契约未写，M5 会返工

**章节**：§4.2 / M5

host 只写 spawn `python -m ragtest` 再扫目录。没有 `status.json`、取消信号、进度协议。GUI 触发后无法显示进行中状态，也无法干净 kill。

**改法**：M0 就定 `artifacts/runs/<id>/{status.json,run.json,heartbeat}`。`SIGINT`/`SIGTERM` → `CANCELLED` → `CLEANUP`。插件只读这个目录契约。

### P1-6 ChatResult.citations 与真实输出形状不符

**章节**：§6.1 / §2B.4

citation 只有提示词约束，没有结构化字段。`ChatResult` 却预留 `citations`。实现时容易伪造列表，和「诚实 Trace」原则冲突。

**改法**：`ChatResult` 只保留 `answer` / `usage` / `latency` / `raw`。`citations_found` 由 `citation_format` evaluator 从文本抽出，不要假装 adapter 返回了结构。

### P1-7 DocumentStatus「必须用中文」说得过死

**章节**：§2.4 / Risk 2

serde rename 序列化确是中文（就绪/失败）。`FromStr` 同时接受英文与中文，`Display` 是英文。retrieval 测试已经双写 `ready|就绪`。

**改法**：adapter 映射表同时收中英；契约测试锁 JSON 响应是中文。表述改成「API JSON 序列化为中文，解析要兼容双语」。

---

## 4. P2（文档债）

### P2-1 Python >=3.13 对引擎本身不是刚需

**章节**：§4.3

3.13 来自被测仓 `retrieval/` 的 pyproject，`ragtest` 只用 httpx/pydantic/pytest，3.11+ 更利于 CI 镜像。

**改法**：引擎 `requires-python >=3.11`；文档注明与 `retrieval/` 对齐 3.13 是可选而非阻塞。

### P2-2 run.json 用 blake3，标准库没有

**章节**：§8

Python `hashlib` 无 blake3，会多一个本地依赖。

**改法**：资产 checksum 用 sha256，足够做可比性校验。

### P2-3 冲突编号从 7 跳到 9，§12 引用「需求十二」无出处

**章节**：§2.9 / §2B.7 / §12

读者无法核对 14 态和冲突 #8。

**改法**：补冲突 #8 或重编号；把需求十二条目标贴进附录，或删掉这个外部引用。

### P2-4 admin / ops 密码环境变量被写成一个

**章节**：§2.2 / §6.2

代码是 `ADMIN_PASSWORD` 种子 `admin`、`OPS_PASSWORD` 种子 `ops@internal`。文档容易让人用错密码。

**改法**：映射表拆成两个 env：`RAGTEST_ARAG_ADMIN_PASSWORD` / `RAGTEST_ARAG_OPS_PASSWORD`。

---

## 5. 源码抽查

抽查了文档里会直接影响 adapter 契约的断言。结论列「属实」表示可以写进实现；「文档低估」表示实现应比文档更大胆。

| 断言 | 出处 | 结论 |
|---|---|---|
| `SearchKbRequest` 仅 `query` / `top_k` / `score_threshold` | `arag-app/src/search/schema.rs:14-23` | 属实 |
| `vector_similarity_weight` 会被 serde 忽略 | `knowledge.py:446-452` vs `SearchKbRequest` | 属实，疑似产品 bug |
| JWT 过期校验被注释 | `system/auth.py:82-85` | 属实 |
| `JWT_SECRET` 硬编码 | `system/auth.py:15` | 属实 |
| `skill` 改写成 `MUST first load…` 前缀 | `stream_agent.py:223-232` | 属实 |
| `X-USER-ID` / `?user_id=` 可注入身份 | `middleware/http.py:89-95` | 属实 |
| `POST /api/chat` 同步返回 `done`（含 `session.messages`） | `chat.py:210-259`、`stream_agent.py:1457-1505` | 属实；文档低估了归因能力 |
| `DocumentStatus` JSON 序列化为中文 | `document.rs:47-75` serde rename | 属实；`FromStr` 同时吃英文 |
| query 上限 app 1000 / engine 1024 | `constants.rs` / `search/mod.rs:280` | 属实 |
| `POST /api/session/delete` 存在 | `session.py:729` | 属实 |
| `top_k` clamp `[1,20]` | `arag-engine-api/src/search.rs` | 属实 |
| Python 3.13 来自 `retrieval/` 而非 DSH | `zhiju-rag-knowledge/retrieval/pyproject.toml` | 属实 |

---

## 6. Open Questions：建议拍板

十个问题里，真正阻塞 M0 的是 Q1（建议直接关）和身份/超时这类文档缺口。Q5/Q7 阻塞的是 M2/M4，不是冒烟脚本。

| 问题 | 建议决策 |
|---|---|
| Q1 引擎形态 | **采纳并关闭**：Python 引擎 + DSH 薄壳。这是本文最该拍板的决定。 |
| Q2 代码归属 | 留在 `deepseek_harness_plugins/rag-testing`。被测仓继续演进 `retrieval/`；测试平台独立版本化。 |
| Q3 DSH UI | 接受 `settings.section`。不要为顶层导航去改 DSH。 |
| Q4 X-Request-Id | 向 arag 提可观测性需求，但不阻塞 M0。Trace 按 §10 诚实分层。 |
| Q5 E2E 拓扑 | MVP 只打 9011/9013。M4 起 agent 的 `ZJ_KNOWLEDGE_BASE_URL` 直连 9013；9360 网关当作环境差异记入 fingerprint。CI 的 LLM 用固定模型，禁止随机采样。 |
| Q6 agent PG | **默认不需要。** 从 `POST /api/chat` 的 `session.messages` 归因。PG 降级为可选。 |
| Q7 CI 中的 arag | M2 前必须有一份 compose：auth/engine/app + Postgres/Qdrant + embedder。M0 允许本地实例。 |
| Q8 Baseline 存放 | 随 git 提交 `artifacts/baselines/`。小团队够用，也利于 PR diff。 |
| Q9 Golden 首批 | 先用 `test_documents/` 做 smoke；业务问答对作为 M1 扩充，不挡 M0。 |
| Q10 疑似 bug | 提 issue，并放入 defect suite（`expected_fail`）。不要让它们挡住检索质量门。 |

---

*文档结束。对应评审对象修订后，将本意见中的 P0 勾销即可进入 Milestone 0。*
