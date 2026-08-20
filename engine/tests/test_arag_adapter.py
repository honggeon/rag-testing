"""AragAdapter 契约测试（respx mock，验证信封解析 / 状态映射 / 错误归一化）。

响应 fixture 形状来自被测系统源码事实：
- 信封 ApiResponse{code, message, data}（arag-app/src/api_types/response.rs:12）
- 登录 data={token, user{id,...}}（arag-auth/src/schema.rs:162）
- 文档状态 JSON 中文（arag-app/src/entities/document.rs）
- 搜索 items 字段（arag-app/src/search/schema.rs:94）
"""

from pathlib import Path

import httpx
import pytest
import respx

from ragtest.adapters.arag import AragAdapter
from ragtest.adapters.base import (
    AdapterError,
    DocumentAsset,
    ErrorKind,
    Identity,
    IngestFailed,
    KBHandle,
    KBSpec,
    PollPolicy,
    RetrievalQuery,
)
from ragtest.models import NormalizedDocStatus

APP = "http://arag-test:9013"
AUTH = "http://arag-test:9011"

LOGIN_BODY = {"code": 200, "message": "ok", "data": {"token": "tok-123", "user": {"id": "u-1"}}}
KB_BODY = {"code": 200, "message": "ok", "data": {"id": "kb-1", "name": "t"}}
DOC_BODY = {"code": 200, "message": "ok", "data": {"id": "doc-1", "filename": "a.md"}}
SEARCH_BODY = {
    "code": 200,
    "message": "ok",
    "data": {
        "items": [
            {
                "kb_id": "kb-1",
                "document_id": "doc-1",
                "chunk_id": "c-1",
                "score": 0.83,
                "text_content": "内容甲",
                "text_breadcrumb": "第一章",
                "embedding_model": "qwen3-embedding-0.6b",
            },
            {"kb_id": "kb-1", "document_id": "doc-2", "chunk_id": "c-2", "score": 0.61},
        ],
        "degraded": False,
    },
}

FAST_POLL = PollPolicy(initial_s=0.01, factor=2.0, max_interval_s=0.02, timeout_s=5.0)


def make_identity() -> Identity:
    return Identity(logical_name="admin", role="admin", email="admin", password="secret")


@pytest.fixture
async def adapter():
    async with AragAdapter(APP, AUTH) as ad:
        yield ad


async def login(ad: AragAdapter):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{AUTH}/auth/v1/login").mock(return_value=httpx.Response(200, json=LOGIN_BODY))
        return await ad.login(make_identity())


async def test_login_parses_envelope_and_binds_identity(adapter):
    identity = make_identity()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{AUTH}/auth/v1/login").mock(
            return_value=httpx.Response(200, json=LOGIN_BODY)
        )
        session = await adapter.login(identity)
    assert session.token == "tok-123"
    assert session.user_id == "u-1"
    # 身份绑定回写（评审 P0-1）
    assert identity.arag_user_id == "u-1"
    assert identity.agent_uid == "u-1"
    assert route.called


async def test_ensure_user_falls_back_to_login_when_registered(adapter):
    identity = Identity(
        logical_name="reader", email="r@test.dev", password="pw-12345678"
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{AUTH}/auth/v1/register").mock(
            return_value=httpx.Response(200, json={"code": 400, "message": "邮箱已注册", "data": None})
        )
        mock.post(f"{AUTH}/auth/v1/login").mock(return_value=httpx.Response(200, json=LOGIN_BODY))
        await adapter.ensure_user(await login(adapter), identity)
    assert identity.arag_user_id == "u-1"


async def test_create_kb_and_delete_ignores_404(adapter):
    session = await login(adapter)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{APP}/api/v1/knowledge-bases").mock(return_value=httpx.Response(200, json=KB_BODY))
        mock.delete(f"{APP}/api/v1/knowledge-bases/kb-1").mock(
            return_value=httpx.Response(200, json={"code": 404, "message": "不存在", "data": None})
        )
        kb = await adapter.create_knowledge_base(session, KBSpec(name="t"))
        assert kb.kb_id == "kb-1"
        await adapter.delete_knowledge_base(session, kb)  # 404 幂等忽略


async def test_upload_sends_multipart_file_field(adapter, tmp_path: Path):
    session = await login(adapter)
    f = tmp_path / "a.md"
    f.write_text("# 测试", encoding="utf-8")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{APP}/api/v1/knowledge-bases/kb-1/documents").mock(
            return_value=httpx.Response(200, json=DOC_BODY)
        )
        doc = await adapter.upload_document(
            session, KBHandle(kb_id="kb-1", name="t"), DocumentAsset(path=f, logical_id="doc_a")
        )
    assert doc.doc_id == "doc-1"
    assert doc.logical_id == "doc_a"
    assert doc.sha256 and len(doc.sha256) == 64
    # multipart 字段名必须是 file（arag-app/src/document/handlers.rs:1041）
    request = route.calls[0].request
    assert b'name="file"' in request.content


async def test_get_document_status_maps_chinese(adapter):
    session = await login(adapter)
    body = {
        "code": 200,
        "message": "ok",
        "data": {"id": "doc-1", "status": "索引中", "ingest_progress": 45, "chunk_count": None},
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{APP}/api/v1/knowledge-bases/kb-1/documents/doc-1").mock(
            return_value=httpx.Response(200, json=body)
        )
        info = await adapter.get_document_status(
            session, KBHandle(kb_id="kb-1", name="t"), doc_handle()
        )
    assert info.status is NormalizedDocStatus.PROCESSING
    assert info.progress == 45
    assert info.raw_status == "索引中"


def doc_handle():
    from ragtest.adapters.base import DocumentHandle

    return DocumentHandle(doc_id="doc-1", filename="a.md", logical_id="doc_a")


async def test_wait_until_ready_success_sequence(adapter):
    session = await login(adapter)
    seq = [
        {"id": "doc-1", "status": "索引中", "ingest_progress": 30},
        {"id": "doc-1", "status": "索引中", "ingest_progress": 80},
        {"id": "doc-1", "status": "就绪", "ingest_progress": 100, "chunk_count": 7},
    ]
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{APP}/api/v1/knowledge-bases/kb-1/documents/doc-1").mock(
            side_effect=[httpx.Response(200, json={"code": 200, "message": "ok", "data": d}) for d in seq]
        )
        info = await adapter.wait_until_ready(
            session, KBHandle(kb_id="kb-1", name="t"), doc_handle(), poll=FAST_POLL
        )
    assert info.status is NormalizedDocStatus.READY
    assert info.chunk_count == 7


async def test_wait_until_ready_raises_on_failed(adapter):
    session = await login(adapter)
    body = {"code": 200, "message": "ok",
            "data": {"id": "doc-1", "status": "失败", "error_message": "PDF 解析失败"}}
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{APP}/api/v1/knowledge-bases/kb-1/documents/doc-1").mock(
            return_value=httpx.Response(200, json=body)
        )
        with pytest.raises(IngestFailed, match="PDF 解析失败"):
            await adapter.wait_until_ready(
                session, KBHandle(kb_id="kb-1", name="t"), doc_handle(), poll=FAST_POLL
            )


async def test_retrieve_parses_items_with_rank(adapter):
    session = await login(adapter)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{APP}/api/v1/knowledge-bases/kb-1/search").mock(
            return_value=httpx.Response(200, json=SEARCH_BODY)
        )
        result = await adapter.retrieve(
            session, KBHandle(kb_id="kb-1", name="t"), RetrievalQuery(query="测试", top_k=5)
        )
    assert len(result.chunks) == 2
    assert result.chunks[0].rank == 1 and result.chunks[1].rank == 2
    assert result.chunks[0].score == pytest.approx(0.83)
    assert result.chunks[0].content == "内容甲"
    assert result.chunks[0].metadata["breadcrumb"] == "第一章"
    assert result.degraded is False
    assert result.latency_ms >= 0
    # 请求体只含 arag schema 三字段
    import json as jsonlib

    sent = jsonlib.loads(route.calls[0].request.content)
    assert set(sent.keys()) == {"query", "top_k", "score_threshold"}


async def test_error_kind_mapping(adapter):
    session = await login(adapter)
    cases = [(400, ErrorKind.VALIDATION), (401, ErrorKind.AUTH), (403, ErrorKind.AUTH),
             (404, ErrorKind.NOT_FOUND), (429, ErrorKind.RATE_LIMIT), (500, ErrorKind.SERVER)]
    for code, kind in cases:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(f"{APP}/api/v1/knowledge-bases/kb-1/search").mock(
                return_value=httpx.Response(200, json={"code": code, "message": "err", "data": None})
            )
            with pytest.raises(AdapterError) as exc_info:
                await adapter.retrieve(
                    session, KBHandle(kb_id="kb-1", name="t"), RetrievalQuery(query="x")
                )
            assert exc_info.value.kind is kind, f"code={code}"
