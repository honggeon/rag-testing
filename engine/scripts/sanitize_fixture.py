#!/usr/bin/env python3
"""fixture 脱敏：保留契约结构，只擦除文本内容。

规则：
- tool 消息的 content 是 JSON 字符串 → 解析后保留 success/count/degraded/chunks 骨架，
  chunk 内只保留 chunk_id/document_id/score，文本字段（content/text/answer 等）替换为占位符
- assistant/user 的 content、response、answer 等长文本 → <REDACTED:N chars>
- usage/token 数字原样保留
- tool_calls 的 id/name/arguments 原样保留（契约关键）
"""
import json
import sys
from pathlib import Path

TEXT_KEYS = {"content", "text", "text_content", "answer", "response", "title", "query",
             "question", "breadcrumb", "text_breadcrumb"}
ID_KEYS = {"chunk_id", "document_id", "kb_id", "tool_call_id", "id", "session_id",
           "message_uuid", "tool_name", "name"}
KEEP_NUMERIC = True


def scrub_kb_list(items: list) -> list:
    """knowledge_bases 列表：保留结构，知识库名替换为占位符（内部信息不入库）。"""
    out = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            item = dict(item)
            if "name" in item:
                item["name"] = f"kb-{i:02d}"
        out.append(item)
    return out


def scrub_str(s: str, key: str) -> str:
    if key in ID_KEYS:
        return s
    if key in TEXT_KEYS and len(s) > 20:
        return f"<REDACTED:{len(s)} chars>"
    return s


def walk(o, key=""):
    if isinstance(o, dict):
        return {k: walk(v, k) for k, v in o.items()}
    if isinstance(o, list):
        if key == "knowledge_bases":
            return scrub_kb_list(o)
        return [walk(v, key) for v in o]
    if isinstance(o, str):
        # tool 消息的 content：JSON 字符串，解析后结构性脱敏
        if key == "content" and o.strip().startswith("{"):
            try:
                inner = json.loads(o)
                return json.dumps(walk(inner), ensure_ascii=False)
            except (ValueError, TypeError):
                pass
        return scrub_str(o, key)
    return o


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    d = json.load(open(src))
    json.dump(walk(d), open(dst, "w"), ensure_ascii=False, indent=2)
    print(f"脱敏完成: {src} → {dst}")


if __name__ == "__main__":
    main()
