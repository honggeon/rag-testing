#!/usr/bin/env python3
"""M0 spike：对 xuanjian-agent 发一次同步 chat，落响应 fixture（评审 P0-6 验证）。

验证目标：`POST /api/chat` 响应中 `session.messages` 是否包含 tool_calls
（knowledge_retrieve 的真实入参与返回），作为 M4 归因解析的契约基准。

用法：
    export RAGTEST_AGENT_UID=<arag user uuid>
    uv run python scripts/spike_chat.py ["问题"] [--skill xj-kbase]

产出：engine/tests/fixtures/agent_chat_response.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from ragtest.config import load_settings

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "agent_chat_response.json"


def log(step: str, msg: str) -> None:
    print(f"[{step}] {msg}", flush=True)


async def main() -> int:
    settings = load_settings()
    if not settings.agent_uid:
        log("CONFIG", "缺少 RAGTEST_AGENT_UID（= arag user_id）")
        return 3

    question = next((a for a in sys.argv[1:] if not a.startswith("--")), "你好，请简单介绍你自己")
    skill = "xj-kbase"
    if "--skill" in sys.argv:
        idx = sys.argv.index("--skill")
        skill = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""

    base = settings.agent_base_url.rstrip("/")
    headers = {"X-USER-ID": settings.agent_uid}

    async with httpx.AsyncClient(timeout=300.0, headers=headers, trust_env=False) as client:  # chat 档超时 300s
        # 1. 建会话
        resp = await client.post(f"{base}/api/session/new")
        resp.raise_for_status()
        body = resp.json()
        session_id = body.get("session_id") or (body.get("data") or {}).get("session_id")
        if not session_id:
            log("ERROR", f"无法解析 session_id: {json.dumps(body, ensure_ascii=False)[:300]}")
            return 2
        log("SESSION", f"session_id={session_id}")

        # 2. 同步 chat
        payload: dict = {"session_id": session_id, "message": question}
        if skill:
            payload["skill"] = skill
        log("CHAT", f"question={question!r} skill={skill!r}（最长等待 300s）…")
        resp = await client.post(f"{base}/api/chat", json=payload)
        resp.raise_for_status()
        result = resp.json()

    # 3. 落 fixture
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log("FIXTURE", f"已写入 {FIXTURE_PATH}")

    # 4. 结构摘要（验证 P0-6：session.messages 是否含 tool_calls）
    log("KEYS", f"顶层字段: {sorted(result.keys())}")
    session = result.get("session") or {}
    messages = session.get("messages") or []
    log("MESSAGES", f"session.messages 共 {len(messages)} 条")
    tool_calls_found = 0
    tool_msgs = 0
    for m in messages:
        if m.get("tool_calls"):
            tool_calls_found += len(m["tool_calls"])
        if m.get("role") == "tool" or m.get("tool_name"):
            tool_msgs += 1
    usage = result.get("usage")
    log("USAGE", json.dumps(usage, ensure_ascii=False) if usage else "无 usage 字段")
    if tool_calls_found:
        log("P0-6", f"✅ 发现 {tool_calls_found} 个 tool_calls（{tool_msgs} 条 tool 消息）——归因主路径可行")
    else:
        log("P0-6", "⚠️ 未发现 tool_calls（问题可能未触发知识库检索；换个知识类问题重试）")

    # 5. 清理会话
    async with httpx.AsyncClient(timeout=30.0, headers=headers, trust_env=False) as client:
        await client.post(f"{base}/api/session/delete", json={"session_id": session_id})
    log("CLEANUP", "会话已删除")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
