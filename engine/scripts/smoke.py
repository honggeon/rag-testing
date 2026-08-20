#!/usr/bin/env python3
"""M0 冒烟：对真实 arag 跑通 login → 建库 → 上传 → 轮询就绪 → 检索 → 清理。

用法：
    export RAGTEST_ARAG_ADMIN_PASSWORD=<ADMIN_PASSWORD>
    uv run python scripts/smoke.py [--file path/to/doc.md]

退出码：0=通过；2=运行错误；3=配置错误（架构 §12.1）
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ragtest.adapters.arag import AragAdapter
from ragtest.adapters.base import (
    AdapterError,
    DocumentAsset,
    Identity,
    KBSpec,
    PollPolicy,
    RetrievalQuery,
    RunLease,
)
from ragtest.artifacts import RunStatusWriter
from ragtest.config import load_settings
from ragtest.models import RunState

SMOKE_DOC = """# ragtest 冒烟文档

## 概述

ragtest 是 RAG 自动化测试引擎。本冒烟文档用于验证知识库的
摄入、索引与检索链路。玄鉴测试平台支持召回评估与基线对比。

## 关键事实

- 冒烟测试标记词：ragtest-smoke-marker
- 创建时间：Milestone 0
"""


def log(step: str, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [{step}] {msg}", flush=True)


async def main() -> int:
    settings = load_settings()
    if not settings.arag_admin_password:
        log("CONFIG", "缺少 RAGTEST_ARAG_ADMIN_PASSWORD")
        return 3

    # 自带文档或临时生成
    file_arg = sys.argv[sys.argv.index("--file") + 1] if "--file" in sys.argv else None
    tmp_dir = None
    if file_arg:
        doc_path = Path(file_arg)
    else:
        tmp_dir = tempfile.TemporaryDirectory()
        doc_path = Path(tmp_dir.name) / "ragtest-smoke.md"
        doc_path.write_text(SMOKE_DOC, encoding="utf-8")

    run_id = time.strftime("smoke-%Y%m%d-%H%M%S")
    writer = RunStatusWriter(settings.artifacts_dir / "runs" / run_id, run_id, total=6)
    heartbeat = asyncio.create_task(writer.heartbeat())
    lease = RunLease(run_id=run_id)

    try:
        async with AragAdapter(settings.arag_base_url, settings.arag_auth_url) as ad:
            # 1. admin 登录
            writer.update(RunState.LOGIN, message="admin 登录")
            admin = Identity(
                logical_name="admin",
                role="admin",
                email=settings.arag_admin_email,
                password=settings.arag_admin_password,
            )
            session = await ad.login(admin)
            log("LOGIN", f"admin 登录成功 user_id={session.user_id}")

            # 2. 建库
            writer.update(RunState.CREATE_KB, done=1)
            kb = await ad.create_knowledge_base(
                session, KBSpec(name=f"ragtest-smoke-{run_id}", description="M0 冒烟")
            )
            lease.knowledge_bases.append(kb)
            log("CREATE_KB", f"kb_id={kb.kb_id}")

            # 3. 上传
            writer.update(RunState.UPLOAD_DOCUMENTS, done=2)
            doc = await ad.upload_document(
                session, kb, DocumentAsset(path=doc_path, logical_id="smoke_doc")
            )
            lease.documents.append((kb, doc))
            log("UPLOAD", f"doc_id={doc.doc_id} sha256={doc.sha256[:12]}…")

            # 4. 轮询就绪（分级超时：ingest 档 300s）
            writer.update(RunState.WAIT_INDEX_READY, done=3)
            t0 = time.perf_counter()
            info = await ad.wait_until_ready(session, kb, doc, poll=PollPolicy(timeout_s=300.0))
            indexing_ms = int((time.perf_counter() - t0) * 1000)
            log(
                "READY",
                f"status={info.raw_status} chunks={info.chunk_count} 索引耗时 {indexing_ms}ms",
            )

            # 5. 检索
            writer.update(RunState.RUN_TEST_CASES, done=4)
            result = await ad.retrieve(
                session, kb, RetrievalQuery(query="ragtest 冒烟测试标记词", top_k=5)
            )
            log("SEARCH", f"命中 {len(result.chunks)} chunks, degraded={result.degraded}, "
                         f"latency={result.latency_ms}ms")
            for c in result.chunks:
                log("  CHUNK", f"rank={c.rank} score={c.score:.4f} doc={c.document_id} "
                              f"{(c.content or '')[:40]!r}")
            if not result.chunks:
                from ragtest.adapters.base import ErrorKind

                raise AdapterError(ErrorKind.SERVER, "检索返回 0 chunks，冒烟断言失败")
            if not any("ragtest-smoke-marker" in (c.content or "") for c in result.chunks):
                log("WARN", "top-5 未包含标记词 ragtest-smoke-marker（不阻断，需人工关注召回质量）")

            # 6. 清理（RunLease 逆序）
            writer.update(RunState.CLEANUP, done=5)
            await ad.cleanup(session, lease)
            log("CLEANUP", "文档与知识库已删除")

        writer.update(RunState.DONE, done=6, message="冒烟通过")
        log("DONE", f"✅ 冒烟通过；status.json: {writer.path}")
        return 0

    except AdapterError as e:
        log("ERROR", f"❌ {e}")
        try:
            writer.update(RunState.CLEANUP, message="异常清理")
            async with AragAdapter(settings.arag_base_url, settings.arag_auth_url) as ad:
                admin = Identity(
                    logical_name="admin", role="admin",
                    email=settings.arag_admin_email, password=settings.arag_admin_password,
                )
                session = await ad.login(admin)
                await ad.cleanup(session, lease)
        except AdapterError as ce:
            log("CLEANUP", f"清理失败（残留见 lease）: {ce}")
        writer.update(RunState.ERROR, message=str(e))
        return 2
    finally:
        heartbeat.cancel()
        if tmp_dir:
            tmp_dir.cleanup()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
