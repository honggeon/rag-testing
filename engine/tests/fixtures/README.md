# fixture 说明

`agent_chat_response.json`：M0 spike 对真实 xuanjian-agent（a100 开发环境）同步 chat 的响应（已脱敏）。

## 已验证的契约事实（M4 归因解析基准）

1. 顶层字段：`{response, session, usage}`
2. `session.messages[]`：assistant 行 `tool_calls` 为**扁平结构** `{id, name, arguments}`
   （**不是** OpenAI 嵌套的 `function.name/arguments`）
3. tool 行：`role=tool` + `tool_name` + `content`（完整工具返回 JSON 字符串）
   - `knowledge_retrieve` 返回 `{success, count, degraded, chunks:[{chunk_id, document_id, ...}]}`
4. `usage = {input_tokens, output_tokens, estimated_cost}`
5. `/api/session/new` 必须带 JSON body（可为 `{}`）；响应 session_id 在 `session.session_id`

