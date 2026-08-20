# xuanjian-agent RAG 问答链路黑盒测试事实依据

> 项目：/Users/chen/xuanjian-code/xuanjian-agent（Starlette + Uvicorn + PostgreSQL，hermes-agent 框架在 `framework/` 子目录）
> 本文为子代理源码分析报告存档，关键结论已被架构师本人二次核验（chat 路由、KBClient.retrieve 请求体、AuthMiddleware、checkLogin）。

## 1. Chat / 对话 API

**对话核心端点（两段式：先 start 拿 stream_id，再 GET 收 SSE）**，路由注册于 `app/routes/chat/chat.py:262-268`：

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/chat/start` | 发起流式对话，返回 `{"stream_id","session_id"}`（chat.py:92-157） |
| GET | `/api/chat/stream?stream_id=` | SSE 流，`text/event-stream`（chat.py:160-177） |
| GET | `/api/chat/stream/status?stream_id=` | 流是否活跃（chat.py:180-192） |
| GET | `/api/chat/cancel?stream_id=` | 取消（chat.py:195-207） |
| POST | `/api/chat` | 同步一次性 JSON（内部复用 start + 消费队列直到 done，chat.py:210-259、stream_agent.py:1457-1507） |

**请求体**（chat.py:102-130）：`session_id`✅、`message`✅、`model/base_url/api_key/provider`、`project_id`、`ref_id`、`attachments`（数组，元素可含 `filename/path/file_id/kb_file_id`）、`skill`（预加载技能名）、`chat_template_kwargs`、`max_turns`（默认90）、`reasoning_effort`（默认"minimal"）。文档：`docs/02-接口API文档.md:219-241`。

**SSE 事件序列**（事件在 `app/agent/streaming.py:135-175` 生成，生产者 `app/agent/stream_agent.py`）：
`thinking` → (`reasoning` / `token` / `tool` / `tool_complete` / `approval` / `clarify` / `suggestion` / `file`)* → `done` → `stream_end`；异常路径 `error`/`cancelled`。事件表文档 `docs/02-接口API文档.md:247-261`。
- `tool`：`{"event_type":"tool.started","name","preview","args"}`，args 只取前 4 个 key、每值截断 120 字符（stream_agent.py:740-762）
- `tool_complete`：`{"event_type":"tool.completed","name","duration","is_error"}`（stream_agent.py:764-784）
- `done`：`{"session":{...含完整 messages},"usage":{"input_tokens","output_tokens","estimated_cost"},"response"}`（stream_agent.py:1299-1307）
- 空闲 15s 发 `: heartbeat` 注释行（streaming.py:157-165）；流超时 300s（streaming.py:13）

**会话管理**（`app/routes/chat/session.py:724-741`）：`POST /api/session/new`（服务端生成 uuid4 作 session_id，session.py:76）、`GET /api/session?session_id=`（含 messages 历史）、`GET /api/sessions`、rename/delete/retry/undo/clear/truncate/pin/archive/export/import 等 16 个端点。多用户隔离：内存会话 key = `f"{user_id}_{session_id}"`（`app/agent/chat_session.py:192`），DB 按 user_id 过滤。

**指定 skill / 知识库的传参**：
- `skill` 字段：服务端把它改写成指令前缀 `[IMPORTANT: ... MUST first load the skill '{skill}' using skill_view(...)]` 拼到用户消息前（stream_agent.py:223-232）。即黑盒测试传 `"skill":"xj-kbase"` 即可强制走知识库技能。
- **kb_id 没有独立的 HTTP 传参字段**。SKILL.md 称"@知识库 的 kb_id 由平台解析传入"，但服务端代码中未发现任何解析 `@xxx`→kb_id 的逻辑；attachments 里的 `kb_file_id` 仅作为提示文本拼进上下文（stream_agent.py:1093-1120）。实际链路是 LLM 按 SKILL.md 意图路由先调 `knowledge_list` 自行解析 kb_id。**未确认**：前端（www/ 为编译产物）是否在 message 文本中注入 kb_id。

## 2. 知识库工具实现

- 实现文件：`system/tools/knowledge_tool.py`（5 个工具 schema KB_LIST_SCHEMA 等在 46-224 行，handler 258-583 行，注册 596-644 行，toolset 名 `"knowledge"`，已在 `config/bootstrap.py:6-8` 的 ENABLED_TOOLSETS 中启用）。
- HTTP 客户端：`system/libs/knowledge.py` 的 `KBClient`。**base URL = 环境变量 `ZJ_KNOWLEDGE_BASE_URL`，默认 `http://127.0.0.1:9360`**（knowledge.py:31,47-48；`.env:29` 同为 9360）。
- 身份映射：每个工具先取当前登录用户 `uid = Bootstrap.get_current_session_uid()`（knowledge_tool.py:29-41，来自 context var `HERMES_SESSION_USER_ID`，在流启动时绑定，stream_agent.py:860-866），然后**用共享密钥现场为 uid 签发 JWT**（`sub=uid, iss=zhiju, aud=zhiju-auth`，HS256，1h）作 Bearer 头（knowledge.py:91-93；`system/auth.py:13-15,108-144`）。即 agent 用户 1:1 映射为 arag 身份，密钥硬编码在 `system/auth.py:15`。
- **knowledge_retrieve → `POST {base}/api/v1/knowledge-bases/{kb_id}/search`**（knowledge.py:446-458），body：`{query, score_threshold, top_k, vector_similarity_weight, document_id?}`。注意：`vector_weight` 是 skill 侧参数名（默认 0.3，knowledge_tool.py:123-126,359-362），发送时**改名透传**为 `vector_similarity_weight`——不是 arag 的 `/search/text` 端点。
- 其他端点：list_knowledge_bases `GET /api/v1/knowledge-bases`（knowledge.py:420）；list/search 文档 `GET /api/v1/knowledge-bases/{kb_id}/documents`（knowledge.py:479）；read/download 走 `GET /api/v1/documents/{doc_id}/download-url?type=&host=` 再按 Range 取字节（knowledge.py:192,492-533）。
- 工具返回给 LLM 的结构（knowledge_tool.py:388-395）：`{"success":true,"count":N,"degraded":bool,"chunks":[...arag items...]}`（chunks 内 download_url/preview_url 的 127.0.0.1 被替换为宿主机 IP，383-387 行）。错误时返回 `tool_error("知识检索失败: ...")`。未登录时返回固定文案"未授权：无法确定当前登录用户…"（38-40 行）。

## 3. RAG 生成链路

1. `POST /api/chat/start` → 消息入库 + 建 stream（stream_agent.py:395-486）
2. `_run_chat_worker` 恢复历史（DB 中 role=tool 的消息**不带入**上下文，stream_agent.py:574-586）→ 构造 `XJAgent`（`system/agent/agent_base.py`，继承 framework AIAgent）→ `agent.run_conversation` 在线程池跑 ReAct 循环（stream_agent.py:1128-1145；循环体 `system/agent/conversation_loop.py`）
3. **query 改写/拆分不在代码侧**：仅由 SKILL.md 工作流A 的提示词规则驱动 LLM 自行"提炼关键词 5-20 字 / 拆子问题"（SKILL.md 工作流A 步骤1）。代码不做任何改写。
4. 检索结果以标准 tool 消息（`role=tool`，内容为上述 JSON）注入对话历史，由 LLM 生成回答——无独立的"检索结果拼接进 prompt"代码路径。
5. **System prompt**：稳定层 + 引用规则在 `system/agent/custom_prompt_builder.py`（第 38 行明确"提及知识库/@xxx 时先查看知识库技能"；46-63 行规定检索类来源用 `[^n]` 脚注角标 + 文末可点击脚注）；workspace 片段 `build_workspace_suffix`（custom_prompt_builder.py:184-208）经 `app/functions.py:1-15` 注入为 `agent._workspace_suffix`（stream_agent.py:1125-1126）。
6. **LLM 配置**：`.env` → `config/env.py:195-217`：`LLM_MODE=local,service`；local 用 `OPENAI_BASE_URL/OPENAI_API_KEY/LLM_MODEL`（当前 `http://127.0.0.1:7500/v1`、`xuanjian-lite`）；service 模式经 AI Gateway（`system/libs/ai_gateway.py`，`GET {cm_host}/api/v1/litellm/me/access`，cm_host 默认 `http://127.0.0.1:9388`）按用户取模型/key；VIP 覆盖逻辑 `app/routes/chat/chat.py:22-79`。请求体里的 model/base_url/api_key 可被客户端覆盖。
7. **citation**：代码侧**无结构化 citation 输出**。参考来源有两处提示词约束：SKILL.md 的"📚 参考来源"回答模板（SKILL.md:180 附近）+ custom_prompt_builder 的 `[^n]` 脚注铁律。黑盒断言只能对最终文本做格式匹配。

## 4. Authentication & 多用户

- **两种认证方式**（`middleware/http.py:63-120` AuthMiddleware）：① `Authorization: Bearer <JWT>`，用与 arag 同一套 JwtService/密钥校验（**同一套签发体系**，iss=zhiju/aud=zhiju-auth）；注意 **`validate_token` 的过期校验被注释掉了**（`system/auth.py:83-85`），过期 token 仍有效。② 直接传 `X-USER-ID` 头或 `?user_id=` query（http.py:89-95）——**黑盒测试最简方式：只带 `X-USER-ID: <uid>` 即可**，无需 JWT。`checkLogin` 仅校验 uid 非空（`system/request.py:14-18`）。
- 多用户隔离：内存会话 key 含 uid（chat_session.py:192）；DB sessions/messages 按 user_id；工作区 `~/.xj_ws/{uid}/{session_id}`（`system/bootstrap.py:130-142`）；知识库工具按 context var 的 uid 独立签 JWT，实现 per-user 的 arag 权限隔离。

## 5. Observability（测试平台判定"检索错 vs 生成错"的关键）

- **结构化 tool_call 记录——有，且完整**：PostgreSQL `messages` 表（`db/migrations/v1_init.sql:44-63`）：assistant 行存 `tool_calls`（JSON，含完整 arguments，即 knowledge_retrieve 的真实 query/top_k/score_threshold/vector_weight），tool 行存 `tool_name`/`tool_call_id`/`content`（完整工具返回 JSON，含 chunks 数量与内容）。落库逻辑 `system/agent/agent_base.py:82-178`（`_flush_messages_to_session_db`）。**查 `SELECT tool_name, tool_calls, content FROM messages WHERE session_id=? AND tool_name='knowledge_retrieve'` 即可精确判定检索入参与返回 chunk 数**。
- SSE 侧：`tool`/`tool_complete` 事件有工具名 + args 快照（截断 120 字符）+ `duration` + `is_error`；`done` 事件含 `usage{input_tokens,output_tokens,estimated_cost}`。**注意 `knowledge_download` 是静默工具不发 SSE 事件**（`config/bootstrap.py:20` SILENT_TOOLS），`knowledge_retrieve` 非静默。
- DB `sessions` 表：input/output/cache/reasoning tokens、tool_call_count、api_call_count、estimated_cost_usd（v1_init.sql:11-43）；`state_meta` 表存 `chat:session:{sid}:state`（status running/done/error + usage，stream_agent.py:545-565,1249-1264）。
- 日志：`~/.xj_agent/logs/agent.log`（INFO+）与 `errors.log`（`system/hermes_logging.py:198-224`）；每次 LLM API 调用记录 `API call #%d: model=… in=… out=… latency=%.1fs`（`system/agent/conversation_loop.py:2185`）；KBClient 每次 HTTP 调用 INFO 级记录 URL（knowledge.py:380）。
- 轨迹快照：`.env SAVE_TRAJECTORIES_UIDS=uid1,uid2` 开启后（`config/env.py:347-354`），写 `logs_dir/session_{sid}.json` 完整消息快照（`framework/run_agent.py:2525-2585`；stream_agent.py:1022-1023）。
- **没有**的东西：无 trace_id/request_id 贯穿（只有内部 `turn_id:api:N` 形式的 api_request_id，conversation_loop.py:1148）、无 TTFT 指标、无 OpenTelemetry、无 message_id 暴露到 API（message_uuid 仅在 DB/内部）。TTFT 只能由测试端在 SSE 上自测（首个 `token` 事件时间 - start 时间）。

## 6. 部署与配置

- 启动：`python main.py server [--host --port]`（`main.py:108-119` → `cli/server.py:10-68`），**默认 0.0.0.0:8788**；启动时自动 init PG。Docker：`docker-compose.agent.yml`（network_mode: host，挂载 /data/xj_agent→~/.xj_agent、/data/xj_ws→~/.xj_ws，健康检查 `GET /health`）。
- 依赖外部服务：PostgreSQL（`.env` POSTGRES_*，当前 127.0.0.1:5433，库 zhiju-insight）；LLM（OPENAI_BASE_URL 127.0.0.1:7500/v1，模型 xuanjian-lite）；知识库 arag（ZJ_KNOWLEDGE_BASE_URL=127.0.0.1:9360）；AI Gateway/技能商店（ZJ_CENTER_EDGE_HOST=127.0.0.1:9388，service 模式与 skill 同步用）。技能包 xj-kbase 从技能商店同步到 `~/.xj_agent/skills/`（`system/bootstrap.py:145-169,454-462`）。
- 关键配置项：`LLM_MODE`、`OPENAI_BASE_URL/API_KEY/LLM_MODEL`、`ZJ_KNOWLEDGE_BASE_URL`、`CHAT_SSE_HEARTBEAT_INTERVAL`、`SAVE_TRAJECTORIES_UIDS`、`COMPRESSION_*`、`CHAT_SUGGESTION_ENABLED`。健康检查 `GET /health`（`app/routes/health.py:275`）。

## 7. 冲突与未确认

1. **端口冲突**：背景称 arag 在 :9013、认证在 :9011，但代码与 .env 中知识库 base URL 均为 **127.0.0.1:9360**（knowledge.py:31、.env:29），且 `/auth/v1/me` 也走同一 base URL（knowledge.py:111）——代码里不存在 9013/9011。以部署环境实际 `ZJ_KNOWLEDGE_BASE_URL` 为准，**未确认**生产是否覆盖。
2. **端点不一致**：背景猜测 retrieve 调 `/api/v1/knowledge-bases/search/text`，实际代码为 `POST /api/v1/knowledge-bases/{kb_id}/search`（knowledge.py:446）。
3. **kb_id 传入方式未确认**：SKILL.md 称"kb_id 由平台解析传入"，服务端无此逻辑；可能由前端在 message 中携带或全靠 LLM 调 knowledge_list 自解析。
4. JWT 过期校验被注释（system/auth.py:83-85），与"JWT 认证"的常规预期不一致，测试时可利用（旧 token 不过期）。
5. docs 称 chat/start body `attachments` 为 string[]（02-接口API文档.md:236），代码实际按 dict 数组处理（stream_agent.py:422-431）。
6. JWT_SECRET 硬编码提交在仓库（system/auth.py:15），测试环境可直接自行签发任意 uid 的 token。
7. **vector_similarity_weight 失效**：knowledge.py:446-458 发送的 `vector_similarity_weight`/`document_id` 不在 arag `SearchKbRequest` schema（仅 query/top_k/score_threshold，`arag-app/src/search/schema.rs:13-25`）内，serde 默认忽略未知字段——skill 的 vector_weight 调优参数实际不生效（疑似真实 bug）。
