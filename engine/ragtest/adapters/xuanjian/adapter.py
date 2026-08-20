"""XuanjianAgentAdapter：xuanjian-agent 的 ChatPort + TracePort 实现（架构 §6.3）。

事实依据（已核验 + M0 spike fixture）：
- `POST /api/session/new`（必须带 JSON body，可为 {}）→ 响应 session_id 在 `session.session_id`
- `POST /api/chat`（同步，chat 档超时 300s）→ `{response: str, session: {...messages}, usage}`
- `POST /api/session/delete`
- 认证：`X-USER-ID: <uid>` 头注入（uid 必须 = arag user_id，IdentityBinding P0-1）
- skill 强制路由：请求体 `skill: "xj-kbase"`
- kb_id 无 HTTP 字段 → 注入确定性检索指令（P0-3）
- tool_calls 扁平结构 {id, name, arguments}（非 OpenAI 嵌套）；tool 消息带 tool_call_id
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from ragtest.adapters.base import (
    AdapterError,
    AgentToolCall,
    Capability,
    ChatRequest,
    ChatResult,
    ChatSession,
    ErrorKind,
    Identity,
)

_CHAT_TIMEOUT_S = 300.0  # chat 档（分级超时 P0-2）


class XuanjianAgentAdapter:
    """用法：`async with XuanjianAgentAdapter(base_url) as ad: ...`"""

    name = "xuanjian"

    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=_CHAT_TIMEOUT_S, trust_env=False)

    async def __aenter__(self) -> XuanjianAgentAdapter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def capabilities(self) -> set[Capability]:
        return {Capability.CHAT, Capability.AGENT_TOOL_TRACE}

    # ── HTTP 基础（agent 无统一信封，直接返回 JSON）────────────────────────

    def _headers(self, identity: Identity) -> dict[str, str]:
        uid = identity.agent_uid or identity.arag_user_id
        if not uid:
            raise AdapterError(
                ErrorKind.AUTH,
                f"identity {identity.logical_name} 缺少 agent_uid（IdentityBinding 未回写）",
            )
        return {"X-USER-ID": uid}

    async def _post(self, path: str, identity: Identity, body: dict) -> dict:
        try:
            resp = await self._client.post(
                f"{self._base}{path}", headers=self._headers(identity), json=body
            )
        except httpx.TimeoutException as e:
            raise AdapterError(ErrorKind.TIMEOUT, f"请求超时 POST {path}") from e
        except httpx.TransportError as e:
            raise AdapterError(ErrorKind.SERVER, f"连接失败 POST {path}: {e}") from e
        if resp.status_code >= 400:
            kind = ErrorKind.AUTH if resp.status_code in (401, 403) else ErrorKind.SERVER
            raise AdapterError(kind, f"POST {path} HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    # ── ChatPort ─────────────────────────────────────────────────────────

    async def create_session(self, identity: Identity) -> ChatSession:
        body = await self._post("/api/session/new", identity, {})
        session_id = (
            body.get("session_id")
            or (body.get("data") or {}).get("session_id")
            or (body.get("session") or {}).get("session_id")
        )
        if not session_id:
            raise AdapterError(ErrorKind.SERVER, f"无法解析 session_id: {str(body)[:200]}")
        return ChatSession(session_id=session_id, identity=identity)

    async def chat(self, session: ChatSession, request: ChatRequest) -> ChatResult:
        started = time.perf_counter()
        body: dict[str, Any] = {
            "session_id": session.session_id,
            "message": _build_message(request),
        }
        if request.skill:
            body["skill"] = request.skill
        if request.model:
            body["model"] = request.model
        result = await self._post("/api/chat", session.identity, body)
        latency_ms = int((time.perf_counter() - started) * 1000)
        if "error" in result and "response" not in result:
            raise AdapterError(ErrorKind.SERVER, f"chat 失败: {str(result.get('error'))[:200]}",
                               raw=result)
        return ChatResult(
            answer=str(result.get("response") or ""),
            usage=result.get("usage"),
            latency_ms=latency_ms,
            raw=result,
        )

    async def delete_session(self, session: ChatSession) -> None:
        try:
            await self._post("/api/session/delete", session.identity,
                             {"session_id": session.session_id})
        except AdapterError:
            pass  # 幂等：删除失败不阻断清理

    # ── TracePort（归因主路径：解析 chat 响应 session.messages，P0-6）────────

    async def collect_agent_trace(
        self, session: ChatSession, result: ChatResult
    ) -> list[AgentToolCall]:
        messages = ((result.raw.get("session") or {}).get("messages")) or []
        # tool 消息按 tool_call_id 索引
        tool_msgs: dict[str, dict] = {}
        for m in messages:
            if m.get("tool_call_id") and (m.get("tool_name") or m.get("role") == "tool"):
                tool_msgs[m["tool_call_id"]] = m

        calls: list[AgentToolCall] = []
        for m in messages:
            for tc in m.get("tool_calls") or []:
                name = tc.get("name") or ""
                try:
                    args = json.loads(tc.get("arguments") or "{}")
                except (TypeError, json.JSONDecodeError):
                    args = {}
                tool_msg = tool_msgs.get(tc.get("id") or "", {})
                chunk_count, hit_doc_ids, is_error = _parse_tool_result(tool_msg.get("content"))
                calls.append(AgentToolCall(
                    tool=name,
                    args=args,
                    chunk_count=chunk_count,
                    hit_doc_ids=hit_doc_ids,
                    is_error=is_error,
                    source="chat_response",
                ))
        return calls


def _build_message(request: ChatRequest) -> str:
    """kb_id 注入确定性检索指令（P0-3）：避免 LLM 自路由不确定性淹没评测信号。"""
    if request.kb_id_inject:
        return (
            f"[测试指令] 请调用 knowledge_retrieve 工具检索知识库"
            f"（kb_id={request.kb_id_inject}），然后基于检索结果回答。\n\n"
            f"问题：{request.question}"
        )
    return request.question


def _parse_tool_result(content: Any) -> tuple[int | None, list[str], bool]:
    """解析 knowledge_* 工具返回的 JSON 字符串 → (chunk_count, hit_doc_ids, is_error)。"""
    if not content:
        return None, [], False
    try:
        data = json.loads(content) if isinstance(content, str) else content
    except (TypeError, json.JSONDecodeError):
        return None, [], False
    if not isinstance(data, dict):
        return None, [], False
    # 错误形态两种：{success: false} 或 {"error": "..."}（知识库ID无效 等）
    is_error = data.get("success") is False or "error" in data
    chunks = data.get("chunks") or []
    hit_doc_ids = [str(c.get("document_id")) for c in chunks if isinstance(c, dict) and c.get("document_id")]
    count = data.get("count")
    return (int(count) if isinstance(count, int) else (len(chunks) if chunks else None),
            hit_doc_ids, is_error)
